"""Road links and car connectors, shared by the car base network and the multimodal network.

Naming follows src/transport/land.py: road links are ``road/<edge_id>/ab|ba``
between ``road/<node_id>`` nodes, car connectors are
``connector/car/<zone_id>/<node_id>/in|out`` between ``zone/<zone_id>/car``
and a road node.

Only free-flow time and length are set here. Costs and capacities depend on
scenario settings and are added by the multimodal network build.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from transport.land import zone_node

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


def road_links(roads: pd.DataFrame, capacity=INF) -> pd.DataFrame:
    """One directed link per direction of travel (``/ab`` along the geometry, ``/ba`` back unless oneway).

    ``roads``: edge_id, from_node, to_node, oneway, length_km, time_min.
    ``capacity``: persons per period, a scalar or a Series aligned with ``roads``.
    """
    if not isinstance(capacity, pd.Series):
        capacity = pd.Series(capacity, index=roads.index, dtype=float)
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
            "capacity": capacity,
        }
    )
    reverse = road[~roads["oneway"].astype(bool).to_numpy()].copy()
    reverse["edge_id"] = reverse["edge_id"].str.replace(r"/ab$", "/ba", regex=True)
    reverse[["edge_from", "edge_to"]] = reverse[["edge_to", "edge_from"]].to_numpy()
    return pd.concat([road, reverse], ignore_index=True)


def car_connectors(zones, road_nodes, connectors: dict) -> pd.DataFrame:
    """Links between each ``zone/<id>/car`` centroid and its ``car_k`` nearest road nodes within ``car_max_km``.

    ``connectors`` is the ``connectors`` config section (car_k, car_max_km, car_speed_kph).
    Zones with no road node in reach get no links: see :func:`zones_without_car_access`.
    """
    za, nb, dist = nearest_pairs(
        zones.geometry, road_nodes.geometry, connectors["car_k"], connectors["car_max_km"] * 1000
    )
    km = dist / 1000.0
    zone_ids = zones["zone_id"].to_numpy()[za]
    node_ids = road_nodes["node_id"].to_numpy()[nb]
    links = pd.DataFrame(
        {
            "edge_from": [zone_node(z, "car") for z in zone_ids],
            "edge_to": "road/" + node_ids,
            "layer": "car_connector",
            "mode": "car",
            "asset_id": None,
            "length_km": km,
            "time_min": km / connectors["car_speed_kph"] * 60.0,
            "fare": 0.0,
            "capacity": INF,
        }
    )
    return both_ways(links, "connector/car/" + zone_ids + "/" + node_ids)


def zones_without_car_access(zones, road_nodes, connectors: dict) -> pd.DataFrame:
    """Zones with no road node within ``car_max_km``: they get no car connector, so no car trips.

    Columns: zone_id, nearest_road_node, nearest_km (null if there are no road
    nodes), plus population, employed_residents and jobs where the zones have them.
    """
    za, nb, dist = nearest_pairs(zones.geometry, road_nodes.geometry, 1, INF)
    nearest = pd.DataFrame(
        {
            "zone_id": zones["zone_id"].to_numpy()[za],
            "nearest_road_node": road_nodes["node_id"].to_numpy()[nb],
            "nearest_km": dist / 1000.0,
        }
    )
    extra = [c for c in ("population", "employed_residents", "jobs") if c in zones.columns]
    table = pd.DataFrame(zones[["zone_id", *extra]]).merge(nearest, on="zone_id", how="left")
    # nearest_pairs keeps neighbours strictly within the radius.
    missing = table["nearest_km"].isna() | (table["nearest_km"] >= connectors["car_max_km"])
    return table.loc[missing, ["zone_id", "nearest_road_node", "nearest_km", *extra]].reset_index(drop=True)
