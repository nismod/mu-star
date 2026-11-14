"""Bus route segments over the road network.

Provided schema:
    bus_stops.gpkg        stop_id, name, geometry (Point)
    bus_routes.gpkg       route_id, name, operator, and the peak service level as
                          frequency_per_hour or headway_min; geometry
                          (LineString, optional, for QA)
    bus_route_stops.csv   route_id, direction (0/1), stop_sequence (int), stop_id,
                          time_to_next_min (scheduled time to the next stop,
                          including dwell; blank on the last stop)

All routes use the standard vehicle size bus.vehicle_capacity. In-vehicle
time is the scheduled time between stops; where missing it falls back to road
free-flow time / bus.speed_factor + bus.dwell_min.

Road mapping (draft), used to link road failures to bus segments: snap each
stop to its nearest road node, then for each pair of consecutive stops on a
route take the fastest road path between them. The
segment's road_edge_ids are the road edges on that path. TODO: constrain the
path to stay near the route geometry (map matching) where route shapes exist,
since the fastest path is not always the route the bus takes.

Output segments: segment_id, route_id, direction, seq, from_stop, to_stop,
road_edge_ids (list), length_km, time_min (in-vehicle), time_source
(timetable|road), headway_min, vehicle_capacity, geometry.
"""

import click
import geopandas
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.ops import linemerge

from transport.land import read_config, require_columns


def road_graph(edges, nodes):
    """Directed sparse graph of road edges weighted by time, plus lookups."""
    index = {node: i for i, node in enumerate(nodes["node_id"])}
    a = edges["from_node"].map(index).to_numpy()
    b = edges["to_node"].map(index).to_numpy()
    two_way = ~edges["oneway"].to_numpy()
    rows = np.concatenate([a, b[two_way]])
    cols = np.concatenate([b, a[two_way]])
    weight = np.concatenate([edges["time_min"].to_numpy(), edges["time_min"].to_numpy()[two_way]])
    edge_ids = np.concatenate([edges["edge_id"].to_numpy(), edges["edge_id"].to_numpy()[two_way]])
    # Keep the fastest edge between each node pair (parallel edges).
    order = np.lexsort((weight, cols, rows))
    rows, cols, weight, edge_ids = rows[order], cols[order], weight[order], edge_ids[order]
    first = np.ones(len(rows), dtype=bool)
    first[1:] = (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1])
    rows, cols, weight, edge_ids = rows[first], cols[first], weight[first], edge_ids[first]
    n = len(index)
    graph = csr_matrix((weight + 1e-9, (rows, cols)), shape=(n, n))
    pair_edge = dict(zip(zip(rows, cols), edge_ids))
    return graph, index, pair_edge


def trace(predecessors, source, target):
    path = []
    node = target
    while node != source:
        prev = predecessors[node]
        if prev < 0:
            return None
        path.append((prev, node))
        node = prev
    return path[::-1]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--stops", required=True)
@click.option("--routes", required=True)
@click.option("--route-stops", required=True)
@click.option("--road-edges", required=True)
@click.option("--road-nodes", required=True)
@click.option("--output-stops", required=True)
@click.option("--output-segments", required=True)
def main(config_path, stops, routes, route_stops, road_edges, road_nodes, output_stops, output_segments):
    cfg = read_config(config_path)
    bus = cfg["bus"]
    crs = cfg["crs_metric"]

    stops = geopandas.read_file(stops).to_crs(crs)
    require_columns(stops, ["stop_id", "geometry"], "bus_stops")
    stops["stop_id"] = stops["stop_id"].astype(str)
    routes = geopandas.read_file(routes)
    require_columns(routes, ["route_id"], "bus_routes")
    sequence = pd.read_csv(route_stops, dtype={"route_id": str, "stop_id": str})
    require_columns(sequence, ["route_id", "direction", "stop_sequence", "stop_id"], "bus_route_stops")

    edges = geopandas.read_parquet(road_edges)
    nodes = geopandas.read_parquet(road_nodes)
    graph, index, pair_edge = road_graph(edges, nodes)
    edge_info = edges.set_index("edge_id")

    snapped = geopandas.sjoin_nearest(stops, nodes[["node_id", "geometry"]], distance_col="snap_m")
    stops = snapped.drop(columns="index_right").drop_duplicates("stop_id").rename(columns={"node_id": "road_node"})
    # TODO: warn on / drop stops snapped further than e.g. 200 m.
    stops.to_parquet(output_stops)
    stop_node = stops.set_index("stop_id")["road_node"].map(index)

    routes = pd.DataFrame(routes.drop(columns="geometry", errors="ignore"))
    routes["route_id"] = routes["route_id"].astype(str)
    routes = routes.set_index("route_id")
    headway = pd.Series(float("nan"), index=routes.index)
    if "headway_min" in routes:
        headway = routes["headway_min"].astype(float)
    if "frequency_per_hour" in routes:
        headway = headway.fillna(60.0 / routes["frequency_per_hour"].astype(float))
    headway = headway.fillna(bus["default_headway_min"])

    sequence = sequence.sort_values(["route_id", "direction", "stop_sequence"])
    sequence["next_stop"] = sequence.groupby(["route_id", "direction"])["stop_id"].shift(-1)
    pairs = sequence.dropna(subset=["next_stop"])

    records = []
    sources = np.unique(stop_node.loc[pairs["stop_id"]].to_numpy())
    _, predecessors = dijkstra(graph, indices=sources, return_predecessors=True)
    source_row = {s: i for i, s in enumerate(sources)}
    for row in pairs.itertuples():
        a, b = stop_node[row.stop_id], stop_node[row.next_stop]
        hops = trace(predecessors[source_row[a]], a, b) if a != b else []
        if hops is None:
            raise ValueError(f"No road path between stops {row.stop_id} and {row.next_stop} on route {row.route_id}")
        road_ids = [pair_edge[hop] for hop in hops]
        info = edge_info.loc[road_ids] if road_ids else edge_info.iloc[0:0]
        scheduled = getattr(row, "time_to_next_min", float("nan"))
        timetabled = pd.notna(scheduled)
        records.append(
            {
                "segment_id": f"{row.route_id}/{row.direction}/{row.stop_sequence}",
                "route_id": row.route_id,
                "direction": row.direction,
                "seq": row.stop_sequence,
                "from_stop": row.stop_id,
                "to_stop": row.next_stop,
                "road_edge_ids": road_ids,
                "length_km": info["length_km"].sum(),
                "time_min": (
                    float(scheduled) if timetabled else info["time_min"].sum() / bus["speed_factor"] + bus["dwell_min"]
                ),
                "time_source": "timetable" if timetabled else "road",
                "headway_min": headway[row.route_id],
                "vehicle_capacity": bus["vehicle_capacity"],
                "geometry": linemerge(list(info.geometry)) if road_ids else None,
            }
        )
    geopandas.GeoDataFrame(records, crs=crs).to_parquet(output_segments)


if __name__ == "__main__":
    main()
