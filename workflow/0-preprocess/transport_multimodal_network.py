"""Build the directed multimodal network and the failure units.

See src/transport/land.py for node and link naming, and
docs/src/infrastructure-transport.md for the method.

Outputs
    links.parquet           LINK_COLUMNS; the input network for transport_flow_model
                            (edge_from / edge_to / edge_id / cost / capacity)
    nodes.geoparquet        node_id, kind, zone_id, has_access, geometry
    failure-units.parquet   asset_type (road|metro|metro_station), asset_id, road_class,
                            link_ids (list: links removed), link_changes (list of
                            {edge_id, time_min, length_km, fare}: bus links that
                            stay with a detour)
"""

import click
import geopandas
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from transport.costs import generalised_cost
from transport.detours import bus_detours
from transport.land import LINK_COLUMNS, peak_capacity, read_config, zone_node

INF = float("inf")


def nearest_pairs(points_a, points_b, k, max_m):
    """(index_a, index_b, distance_m) for up to k neighbours in b of each point in a, within max_m."""
    if len(points_a) == 0 or len(points_b) == 0:
        empty = np.array([], dtype=int)
        return empty, empty, np.array([], dtype=float)
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
@click.option("--island", default=None)
@click.option("--scenario", default=None)
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
    island,
    scenario,
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
    cfg = read_config(config_path, island=island, scenario=scenario)
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
            "route_key": bus["route_id"].astype(str) + "/" + bus["direction"].astype(str),
            "stage_from": bus["stage_from"] if "stage_from" in bus else np.nan,
            "stage_to": bus["stage_to"] if "stage_to" in bus else np.nan,
            "length_km": bus["length_km"],
            "time_min": bus["time_min"],
            # Stage fares approximated as boarding + per km (cost.fare.bus).
            "fare": fares["bus"]["per_km"] * bus["length_km"],
            # Commuters only get the capacity left by other purposes (e.g. school).
            "capacity": peak_capacity(bus["headway_min"], bus["vehicle_capacity"], hours)
            * (1.0 - cfg["bus"]["other_purpose_load_share"]),
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
            "edge_from": "bus_stop/" + calls["stop_id"] + "/dep",
            "edge_to": call_nodes,
            "layer": "bus_board",
            "mode": "bus",
            "asset_id": None,
            "length_km": 0.0,
            "time_min": weight["wait"] * calls["headway_min"] / 2 + weight["transfer_penalty_min"],
            "fare": fares["bus"]["board"],
            "capacity": INF,
        }
    )
    alight = pd.DataFrame(
        {
            "edge_id": "alight/bus/" + tail,
            "edge_from": call_nodes,
            "edge_to": "bus_stop/" + calls["stop_id"] + "/arr",
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

    # Metro: same pattern, per line (main line and branch), two directions.
    # Line call nodes: metro/<line>/<direction>/<station>.
    metro_links = pd.DataFrame(
        {
            "edge_id": "metro/" + metro["direction"].astype(str) + "/" + metro["segment_id"],
            "edge_from": "metro/"
            + metro["line_id"]
            + "/"
            + metro["direction"].astype(str)
            + "/"
            + metro["from_station"],
            "edge_to": "metro/" + metro["line_id"] + "/" + metro["direction"].astype(str) + "/" + metro["to_station"],
            "layer": "metro",
            "mode": "metro",
            "asset_id": metro["segment_id"],
            "station_from": metro["from_station"],
            "station_to": metro["to_station"],
            "length_km": metro["length_km"],
            "time_min": metro["time_min"],
            "fare": fares["metro"]["per_km"] * metro["length_km"],
            "capacity": peak_capacity(metro["headway_min"], metro["capacity_per_train"], hours)
            * (1.0 - cfg["metro"]["other_purpose_load_share"]),
        }
    )
    mcalls = pd.concat(
        [
            metro[["line_id", "direction", "from_station", "headway_min"]].rename(
                columns={"from_station": "station_id"}
            ),
            metro[["line_id", "direction", "to_station", "headway_min"]].rename(columns={"to_station": "station_id"}),
        ]
    ).drop_duplicates(["line_id", "direction", "station_id"])
    parts.append(metro_links)
    mnode = "metro/" + mcalls["line_id"] + "/" + mcalls["direction"].astype(str) + "/" + mcalls["station_id"]
    parts += [
        pd.DataFrame(
            {
                "edge_id": "board/" + mnode,
                "edge_from": "metro_station/" + mcalls["station_id"] + "/dep",
                "edge_to": mnode,
                "layer": "metro_board",
                "mode": "metro",
                "asset_id": None,
                "length_km": 0.0,
                "time_min": weight["wait"] * mcalls["headway_min"] / 2 + weight["transfer_penalty_min"],
                "fare": fares["metro"]["board"],
                "capacity": INF,
            }
        ),
        pd.DataFrame(
            {
                "edge_id": "alight/" + mnode,
                "edge_from": mnode,
                "edge_to": "metro_station/" + mcalls["station_id"] + "/arr",
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

    # Walk access. Bus and metro trips have separate centroids and stay on their
    # own networks (no bus <-> metro transfers; TODO feeder bus):
    #   zone/<id>/bus   <-> bus stops within walk_max_km
    #   zone/<id>/metro <-> metro stations within metro_walk_max_km
    def walk_links(points, node_ids, kind, max_km):
        za, pb, dist = nearest_pairs(zones.geometry, points, 10, max_km * 1000)
        walk_km = dist / 1000.0 * conn["walk_detour_factor"]
        access = pd.DataFrame(
            {
                "edge_from": [zone_node(z, kind) for z in zones["zone_id"].to_numpy()[za]],
                "edge_to": node_ids[pb],
                "layer": "walk_access",
                "mode": "walk",
                "asset_id": None,
                "length_km": walk_km,
                "time_min": weight["walk"] * walk_km / conn["walk_speed_kph"] * 60.0,
                "fare": 0.0,
                "capacity": INF,
            }
        )
        # Walk in to the departure side, walk out from the arrival side, so a
        # trip cannot walk through a stop without boarding.
        prefix = "walk/" + zones["zone_id"].to_numpy()[za] + "/" + node_ids[pb]
        inbound = access.assign(edge_id=prefix + "/in", edge_to=access["edge_to"] + "/dep")
        outbound = access.assign(
            edge_id=prefix + "/out", edge_from=access["edge_to"] + "/arr", edge_to=access["edge_from"]
        )
        return pd.concat([inbound, outbound], ignore_index=True)

    # Interchange at a stop or station (arrival side -> departure side), for
    # changing bus route or metro direction. The boarding link carries the
    # wait and transfer penalty.
    interchange_nodes = np.r_[
        ("bus_stop/" + stops["stop_id"]).to_numpy(), ("metro_station/" + stations["station_id"]).to_numpy()
    ]
    parts.append(
        pd.DataFrame(
            {
                "edge_id": "interchange/" + interchange_nodes,
                "edge_from": interchange_nodes + "/arr",
                "edge_to": interchange_nodes + "/dep",
                "layer": "interchange",
                "mode": "walk",
                "asset_id": None,
                "length_km": 0.0,
                "time_min": 0.0,
                "fare": 0.0,
                "capacity": INF,
            }
        )
    )

    parts.append(walk_links(stops.geometry, ("bus_stop/" + stops["stop_id"]).to_numpy(), "bus", conn["walk_max_km"]))
    parts.append(
        walk_links(
            stations.geometry,
            ("metro_station/" + stations["station_id"]).to_numpy(),
            "metro",
            conn["metro_walk_max_km"],
        )
    )

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
    links = links.reindex(columns=list(LINK_COLUMNS))
    if links["edge_id"].duplicated().any():
        raise ValueError(f"Duplicate link ids, e.g. {links.loc[links.edge_id.duplicated(), 'edge_id'].head().tolist()}")
    links.to_parquet(output_links, index=False)

    # Nodes. has_access marks zone centroids that are joined to their network
    # (the mode split only sends trips between connected centroids).
    linked = set(links["edge_from"])
    zone_rows = [
        pd.DataFrame(
            {
                "node_id": [zone_node(z, kind) for z in zones.zone_id],
                "kind": f"zone_{kind}",
                "zone_id": zones.zone_id,
                "geometry": zones.geometry,
            }
        )
        for kind in ("car", "bus", "metro")
    ]
    nodes = pd.concat(
        [
            *zone_rows,
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
    nodes["has_access"] = nodes["node_id"].isin(linked)
    geopandas.GeoDataFrame(nodes, crs=zones.crs).to_parquet(output_nodes)

    # Failure units: what each physical asset takes with it when it fails.
    # A road edge removes its road links. Buses over it detour, skip stops
    # that cannot be reached (their boarding / alighting links are removed),
    # or are cut (transport.detours); detoured bus links are kept with new
    # times and lengths (link_changes).
    road_links = links[links.layer == "road"].groupby("asset_id")["edge_id"].agg(list)
    bus_road_edges = sorted({e for ids in bus["road_edge_ids"] for e in ids})
    detour_cfg = cfg["criticality"]["bus_detour"]
    detours = bus_detours(
        roads,
        bus,
        stops.set_index("stop_id")["road_node"].to_dict(),
        bus_road_edges,
        speed_factor=cfg["bus"]["speed_factor"],
        max_skipped_stops=detour_cfg["max_skipped_stops"],
        max_detour_factor=detour_cfg["max_detour_factor"],
    )
    fares_bus = cfg["cost"]["fare"]["bus"]

    def removed_and_changed(asset, base_links):
        detour = detours.get(asset)
        if detour is None:
            return base_links, []
        removed = list(base_links) + ["bus/" + s for s in detour.cut_segments]
        for route, direction, stop in detour.skipped_stops:
            tail = f"{route}/{direction}/{stop}"
            removed += [f"board/bus/{tail}", f"alight/bus/{tail}"]
        changes = [
            {
                "edge_id": "bus/" + c["segment_id"],
                "time_min": c["time_min"],
                "length_km": c["length_km"],
                "fare": fares_bus["per_km"] * c["length_km"],
            }
            for c in detour.changes
        ]
        return removed, changes

    road_units = pd.DataFrame({"asset_id": road_links.index, "link_ids": road_links.to_numpy()})
    pairs = [removed_and_changed(a, ls) for a, ls in zip(road_units.asset_id, road_units.link_ids)]
    road_units["link_ids"] = [p[0] for p in pairs]
    road_units["link_changes"] = [p[1] for p in pairs]
    road_units["asset_type"] = "road"
    road_units = road_units.merge(roads[["edge_id", "road_class"]].rename(columns={"edge_id": "asset_id"}), how="left")
    metro_units = links[links.layer == "metro"].groupby("asset_id")["edge_id"].agg(list).reset_index()
    metro_units = metro_units.rename(columns={"edge_id": "link_ids"}).assign(asset_type="metro", road_class=None)
    # Station closures: every boarding and alighting link at the station, both
    # directions. Trains still run through.
    board = links[links.layer == "metro_board"]
    alight = links[links.layer == "metro_alight"]
    station_links = pd.concat(
        [
            pd.DataFrame(
                {
                    "asset_id": board["edge_from"].str.removeprefix("metro_station/").str.removesuffix("/dep"),
                    "link": board["edge_id"],
                }
            ),
            pd.DataFrame(
                {
                    "asset_id": alight["edge_to"].str.removeprefix("metro_station/").str.removesuffix("/arr"),
                    "link": alight["edge_id"],
                }
            ),
        ]
    )
    station_units = (
        station_links.groupby("asset_id")["link"]
        .agg(list)
        .reset_index()
        .rename(columns={"link": "link_ids"})
        .assign(asset_type="metro_station", road_class=None)
    )
    units = pd.concat([road_units, metro_units, station_units], ignore_index=True)
    units["link_changes"] = [c if isinstance(c, list) else [] for c in units.get("link_changes", [None] * len(units))]
    units = units[["asset_type", "asset_id", "road_class", "link_ids", "link_changes"]]
    units.to_parquet(output_failure_units, index=False)


if __name__ == "__main__":
    main()
