"""Build the directed multimodal network and the failure units.

See src/transport/land.py for node and link naming, and
docs/src/infrastructure-transport.md for the method.

Outputs
    links.parquet           LINK_COLUMNS; the input network for transport_flow_model
                            (edge_from / edge_to / edge_id / cost / capacity)
    nodes.geoparquet        node_id, kind, zone_id, nearest_metro_km (zone pt nodes), geometry
    failure-units.parquet   asset_type (road|metro), asset_id, road_class, link_ids (list)
"""

import click
import geopandas
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from transport.costs import generalised_cost
from transport.land import LINK_COLUMNS, peak_capacity, read_config, zone_node

INF = float("inf")


def nearest_pairs(points_a, points_b, k, max_m):
    """(index_a, index_b, distance_m) for up to k neighbours in b of each point in a, within max_m."""
    tree = cKDTree(np.c_[points_b.x, points_b.y])
    k = min(k, len(points_b))
    dist, idx = tree.query(np.c_[points_a.x, points_a.y], k=k, distance_upper_bound=max_m)
    dist, idx = dist.reshape(len(points_a), -1), idx.reshape(len(points_a), -1)
    a = np.repeat(np.arange(len(points_a)), dist.shape[1])
    keep = np.isfinite(dist.ravel())
    return a[keep], idx.ravel()[keep], dist.ravel()[keep]


def both_ways(df, prefix):
    """Duplicate access links in both directions (…/in towards the network, …/out back to the zone)."""
    inbound = df.assign(edge_id=prefix + "/in")
    outbound = df.rename(columns={"edge_from": "edge_to", "edge_to": "edge_from"}).assign(edge_id=prefix + "/out")
    return pd.concat([inbound, outbound], ignore_index=True)


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--road-edges", required=True)
@click.option("--road-nodes", required=True)
@click.option("--bus-stops", required=True)
@click.option("--bus-segments", required=True)
@click.option("--metro-stations", required=True)
@click.option("--metro-segments", required=True)
@click.option("--zones", required=True)
@click.option("--output-links", required=True)
@click.option("--output-nodes", required=True)
@click.option("--output-failure-units", required=True)
def main(
    config_path,
    road_edges,
    road_nodes,
    bus_stops,
    bus_segments,
    metro_stations,
    metro_segments,
    zones,
    output_links,
    output_nodes,
    output_failure_units,
):
    cfg = read_config(config_path)
    hours = cfg["period"]["peak_period_hours"]
    conn = cfg["connectors"]
    weight = cfg["cost"]["weight"]
    fares = cfg["cost"]["fare"]

    roads = geopandas.read_parquet(road_edges)
    rnodes = geopandas.read_parquet(road_nodes)
    stops = geopandas.read_parquet(bus_stops)
    bus = geopandas.read_parquet(bus_segments)
    stations = geopandas.read_parquet(metro_stations)
    metro = geopandas.read_parquet(metro_segments)
    zones = geopandas.read_parquet(zones)

    parts = []

    # Roads: one directed link per direction of travel.
    if cfg["road"]["capacity_constrained"]:
        road_cap = roads["capacity_vph"] * hours * cfg["period"]["car_occupancy"]
    else:
        road_cap = pd.Series(INF, index=roads.index)
    road = pd.DataFrame(
        {
            "edge_id": "road/" + roads["edge_id"] + "/ab",
            "edge_from": "road/" + roads["from_node"],
            "edge_to": "road/" + roads["to_node"],
            "layer": "road",
            "mode": "car",
            "asset_id": roads["edge_id"],
            "length_km": roads["length_km"],
            "time_min": roads["time_min"],
            "fare": 0.0,
            "capacity": road_cap,
        }
    )
    reverse = road[~roads["oneway"].to_numpy()].copy()
    reverse["edge_id"] = reverse["edge_id"].str.replace(r"/ab$", "/ba", regex=True)
    reverse[["edge_from", "edge_to"]] = reverse[["edge_to", "edge_from"]].to_numpy()
    parts += [road, reverse]

    # Car connectors: zone/<id>/car <-> k nearest road nodes.
    za, nb, dist = nearest_pairs(zones.geometry, rnodes.geometry, conn["car_k"], conn["car_max_km"] * 1000)
    km = dist / 1000.0
    car_conn = pd.DataFrame(
        {
            "edge_from": [zone_node(z, "car") for z in zones["zone_id"].to_numpy()[za]],
            "edge_to": "road/" + rnodes["node_id"].to_numpy()[nb],
            "layer": "car_connector",
            "mode": "car",
            "asset_id": None,
            "length_km": km,
            "time_min": km / conn["car_speed_kph"] * 60.0,
            "fare": 0.0,
            "capacity": INF,
        }
    )
    prefix = "connector/car/" + zones["zone_id"].to_numpy()[za] + "/" + rnodes["node_id"].to_numpy()[nb]
    parts.append(both_ways(car_conn, prefix))
    # TODO: report zones with no road node within car_max_km (they get no car trips).

    # Bus: in-vehicle segments, boarding and alighting.
    route_node = lambda r, d, s: f"bus/{r}/{d}/{s}"  # noqa: E731
    bus_links = pd.DataFrame(
        {
            "edge_id": "bus/" + bus["segment_id"],
            "edge_from": [route_node(r, d, s) for r, d, s in zip(bus.route_id, bus.direction, bus.from_stop)],
            "edge_to": [route_node(r, d, s) for r, d, s in zip(bus.route_id, bus.direction, bus.to_stop)],
            "layer": "bus",
            "mode": "bus",
            "asset_id": None,
            "road_edge_ids": bus["road_edge_ids"],
            "length_km": bus["length_km"],
            "time_min": bus["time_min"],
            "fare": 0.0,
            "capacity": peak_capacity(bus["headway_min"], bus["vehicle_capacity"], hours),
        }
    )
    calls = pd.concat(
        [
            bus[["route_id", "direction", "from_stop", "headway_min"]].rename(columns={"from_stop": "stop_id"}),
            bus[["route_id", "direction", "to_stop", "headway_min"]].rename(columns={"to_stop": "stop_id"}),
        ]
    ).drop_duplicates(["route_id", "direction", "stop_id"])
    call_nodes = [route_node(r, d, s) for r, d, s in zip(calls.route_id, calls.direction, calls.stop_id)]
    tail = calls["route_id"] + "/" + calls["direction"].astype(str) + "/" + calls["stop_id"]
    # Waiting (half the headway, weighted) and a transfer penalty on every boarding.
    # TODO: the penalty also hits the first boarding; refine if it matters.
    board = pd.DataFrame(
        {
            "edge_id": "board/bus/" + tail,
            "edge_from": "bus_stop/" + calls["stop_id"],
            "edge_to": call_nodes,
            "layer": "bus_board",
            "mode": "bus",
            "asset_id": None,
            "length_km": 0.0,
            "time_min": weight["wait"] * calls["headway_min"] / 2 + weight["transfer_penalty_min"],
            "fare": fares["bus"],
            "capacity": INF,
        }
    )
    alight = pd.DataFrame(
        {
            "edge_id": "alight/bus/" + tail,
            "edge_from": call_nodes,
            "edge_to": "bus_stop/" + calls["stop_id"],
            "layer": "bus_alight",
            "mode": "bus",
            "asset_id": None,
            "length_km": 0.0,
            "time_min": 0.0,
            "fare": 0.0,
            "capacity": INF,
        }
    )
    parts += [bus_links, board, alight]

    # Metro: same pattern; one line, two directions.
    pd.DataFrame(
        {
            "edge_id": "metro/" + metro["direction"].astype(str) + "/" + metro["segment_id"],
            "edge_from": "metro/" + metro["direction"].astype(str) + "/" + metro["from_station"],
            "edge_to": "metro/" + metro["direction"].astype(str) + "/" + metro["to_station"],
            "layer": "metro",
            "mode": "metro",
            "asset_id": metro["segment_id"],
            "length_km": metro["length_km"],
            "time_min": metro["time_min"],
            "fare": 0.0,
            "capacity": peak_capacity(metro["headway_min"], metro["capacity_per_train"], hours),
        }
    )
    mcalls = pd.concat(
        [
            metro[["direction", "from_station", "headway_min"]].rename(columns={"from_station": "station_id"}),
            metro[["direction", "to_station", "headway_min"]].rename(columns={"to_station": "station_id"}),
        ]
    ).drop_duplicates(["direction", "station_id"])
    mnode = "metro/" + mcalls["direction"].astype(str) + "/" + mcalls["station_id"]
    parts += [
        pd.DataFrame(
            {
                "edge_id": "board/" + mnode,
                "edge_from": "metro_station/" + mcalls["station_id"],
                "edge_to": mnode,
                "layer": "metro_board",
                "mode": "metro",
                "asset_id": None,
                "length_km": 0.0,
                "time_min": weight["wait"] * mcalls["headway_min"] / 2 + weight["transfer_penalty_min"],
                "fare": fares["metro"],
                "capacity": INF,
            }
        ),
        pd.DataFrame(
            {
                "edge_id": "alight/" + mnode,
                "edge_from": mnode,
                "edge_to": "metro_station/" + mcalls["station_id"],
                "layer": "metro_alight",
                "mode": "metro",
                "asset_id": None,
                "length_km": 0.0,
                "time_min": 0.0,
                "fare": 0.0,
                "capacity": INF,
            }
        ),
    ]

    # Walk access: zone/<id>/pt <-> bus stops and metro stations within walking distance.
    pt_points = pd.concat(
        [
            pd.DataFrame({"node": "bus_stop/" + stops["stop_id"], "geometry": stops.geometry}),
            pd.DataFrame({"node": "metro_station/" + stations["station_id"], "geometry": stations.geometry}),
        ],
        ignore_index=True,
    )
    pt_points = geopandas.GeoDataFrame(pt_points, crs=stops.crs)
    za, pb, dist = nearest_pairs(zones.geometry, pt_points.geometry, 10, conn["walk_max_km"] * 1000)
    walk_km = dist / 1000.0 * conn["walk_detour_factor"]
    access = pd.DataFrame(
        {
            "edge_from": [zone_node(z, "pt") for z in zones["zone_id"].to_numpy()[za]],
            "edge_to": pt_points["node"].to_numpy()[pb],
            "layer": "walk_access",
            "mode": "walk",
            "asset_id": None,
            "length_km": walk_km,
            "time_min": weight["walk"] * walk_km / conn["walk_speed_kph"] * 60.0,
            "fare": 0.0,
            "capacity": INF,
        }
    )
    parts.append(both_ways(access, "walk/" + zones["zone_id"].to_numpy()[za] + "/" + pt_points["node"].to_numpy()[pb]))

    # Walk transfers between bus stops and metro stations.
    sa, sb, dist = nearest_pairs(stations.geometry, stops.geometry, 10, conn["transfer_max_km"] * 1000)
    tkm = dist / 1000.0 * conn["walk_detour_factor"]
    transfer = pd.DataFrame(
        {
            "edge_from": "metro_station/" + stations["station_id"].to_numpy()[sa],
            "edge_to": "bus_stop/" + stops["stop_id"].to_numpy()[sb],
            "layer": "walk_transfer",
            "mode": "walk",
            "asset_id": None,
            "length_km": tkm,
            "time_min": weight["walk"] * tkm / conn["walk_speed_kph"] * 60.0,
            "fare": 0.0,
            "capacity": INF,
        }
    )
    parts.append(both_ways(transfer, "walk/transfer/" + transfer["edge_from"] + "/" + transfer["edge_to"]))

    links = pd.concat(parts, ignore_index=True)
    if "road_edge_ids" not in links:
        links["road_edge_ids"] = None
    links["cost"] = generalised_cost(
        links["time_min"],
        links["length_km"],
        links["mode"],
        per_minute=cfg["cost"]["per_minute"],
        per_km=cfg["cost"]["per_km"],
        fare=links["fare"],
    )
    links = links[list(LINK_COLUMNS)]
    if links["edge_id"].duplicated().any():
        raise ValueError(f"Duplicate link ids, e.g. {links.loc[links.edge_id.duplicated(), 'edge_id'].head().tolist()}")
    links.to_parquet(output_links, index=False)

    # Nodes (for checks and for the mode split's metro catchment).
    station_tree = cKDTree(np.c_[stations.geometry.x, stations.geometry.y])
    nearest_metro_km = station_tree.query(np.c_[zones.geometry.x, zones.geometry.y])[0] / 1000.0
    nodes = pd.concat(
        [
            pd.DataFrame(
                {
                    "node_id": [zone_node(z, "car") for z in zones.zone_id],
                    "kind": "zone_car",
                    "zone_id": zones.zone_id,
                    "geometry": zones.geometry,
                }
            ),
            pd.DataFrame(
                {
                    "node_id": [zone_node(z, "pt") for z in zones.zone_id],
                    "kind": "zone_pt",
                    "zone_id": zones.zone_id,
                    "nearest_metro_km": nearest_metro_km,
                    "geometry": zones.geometry,
                }
            ),
            pd.DataFrame({"node_id": "road/" + rnodes.node_id, "kind": "road", "geometry": rnodes.geometry}),
            pd.DataFrame({"node_id": "bus_stop/" + stops.stop_id, "kind": "bus_stop", "geometry": stops.geometry}),
            pd.DataFrame(
                {
                    "node_id": "metro_station/" + stations.station_id,
                    "kind": "metro_station",
                    "geometry": stations.geometry,
                }
            ),
        ],
        ignore_index=True,
    )
    geopandas.GeoDataFrame(nodes, crs=zones.crs).to_parquet(output_nodes)

    # Failure units: what each physical asset takes with it when it fails.
    road_links = links[links.layer == "road"].groupby("asset_id")["edge_id"].agg(list)
    bus_over = (
        links.loc[links.layer == "bus", ["edge_id", "road_edge_ids"]]
        .explode("road_edge_ids")
        .dropna()
        .groupby("road_edge_ids")["edge_id"]
        .agg(list)
    )
    road_units = pd.DataFrame({"asset_id": road_links.index, "link_ids": road_links.to_numpy()})
    road_units["link_ids"] = [
        links_ + bus_over.get(asset, []) for asset, links_ in zip(road_units.asset_id, road_units.link_ids)
    ]
    road_units["asset_type"] = "road"
    road_units = road_units.merge(roads[["edge_id", "road_class"]].rename(columns={"edge_id": "asset_id"}), how="left")
    metro_units = links[links.layer == "metro"].groupby("asset_id")["edge_id"].agg(list).reset_index()
    metro_units = metro_units.rename(columns={"edge_id": "link_ids"}).assign(asset_type="metro", road_class=None)
    # TODO: station closures as failure units (all board/alight links at a station).
    units = pd.concat([road_units, metro_units], ignore_index=True)[
        ["asset_type", "asset_id", "road_class", "link_ids"]
    ]
    units.to_parquet(output_failure_units, index=False)


if __name__ == "__main__":
    main()
