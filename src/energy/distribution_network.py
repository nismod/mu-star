"""Build the estimated distribution network: roads as lines, power assets joined to the nearest road point.

The graph holds connections only (``stage="connectivity_only"``), no voltages or ratings. Road nodes
and all edges have ``inferred=True``; power-asset nodes (CEB or OpenStreetMap) have ``inferred=False``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely
from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import substring

GEOGRAPHIC_CRS = "EPSG:4326"
DEFAULT_MAX_ANCHOR_DISTANCE_M = 1000.0
WGS84_GEOD = Geod(ellps="WGS84")
# Node keys round coordinates to 6 decimal places of a degree (about 0.1 m).
NODE_KEY_DECIMALS = 6
# A connection point within this distance of a line end uses the end node instead of splitting the line.
SPLIT_SNAP_TOLERANCE_M = 1.0


@dataclass(frozen=True)
class InferredDistributionOutputs:
    nodes: Path
    edges: Path
    metadata: Path


def node_key(x: float, y: float) -> str:
    """Return the node id of a road junction at longitude ``x``, latitude ``y``."""
    return f"dist::{round(x, NODE_KEY_DECIMALS)}::{round(y, NODE_KEY_DECIMALS)}"


def line_endpoints(line: LineString) -> tuple[tuple[float, float], tuple[float, float]]:
    coords = line.coords
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


def geodesic_length_km(line: LineString) -> float:
    """Return the length in km of a lon/lat line, measured on the WGS84 ellipsoid (no UTM zone needed)."""
    return abs(float(WGS84_GEOD.geometry_length(line))) / 1000


def _asset_node_id(row) -> str:
    """Return a power asset's node id: ``bus::<id>`` for a substation, ``asset::<id>`` otherwise."""
    asset_id = str(getattr(row, "asset_id", getattr(row, "bus_id", "")))
    asset_kind = str(getattr(row, "asset_kind", getattr(row, "kind", "substation"))).lower()
    return f"{'bus' if asset_kind == 'substation' else 'asset'}::{asset_id}"


def _distribution_nodes(graph: nx.MultiGraph) -> list[str]:
    return [node for node, attrs in graph.nodes(data=True) if attrs.get("kind") == "distribution_node"]


def _add_junction(graph: nx.MultiGraph, node: str, x: float, y: float, *, source: str, region: object) -> None:
    if node not in graph:
        graph.add_node(
            node,
            kind="distribution_node",
            inferred=True,
            source=source,
            region=region,
            x=x,
            y=y,
            demand_mw=0.0,
        )
    graph.nodes[node].setdefault("line_sources", set()).add(source)


def _add_distribution_lines(graph: nx.MultiGraph, lines: gpd.GeoDataFrame | None, *, source: str) -> None:
    """Add each LineString as an edge keyed by its id (``osm_000001`` etc.), so parallel lines stay separate."""
    if lines is None or lines.empty:
        return
    prepared = lines.to_crs(GEOGRAPHIC_CRS).explode(index_parts=False)
    prepared = prepared[prepared.geometry.geom_type.eq("LineString")]
    for row_number, row in enumerate(prepared.itertuples(), start=1):
        region = getattr(row, "region", None)
        start, end = line_endpoints(row.geometry)
        start_node = node_key(*start)
        end_node = node_key(*end)
        length_km = geodesic_length_km(row.geometry)
        if start_node == end_node or length_km <= 0:
            continue
        _add_junction(graph, start_node, start[0], start[1], source=source, region=region)
        _add_junction(graph, end_node, end[0], end[1], source=source, region=region)
        edge_id = f"{source}_{row_number:06d}"
        graph.add_edge(
            start_node,
            end_node,
            key=edge_id,
            edge_id=edge_id,
            source=source,
            region=region,
            inferred=True,
            stage="connectivity_only",
            length_km=length_km,
            geometry=row.geometry,
        )


def _edge_table(graph: nx.MultiGraph, sources: set[str] | None = None) -> pd.DataFrame:
    rows = [
        {"u": u, "v": v, "key": key, "geometry": attrs["geometry"]}
        for u, v, key, attrs in graph.edges(keys=True, data=True)
        if attrs.get("geometry") is not None and (sources is None or attrs.get("source") in sources)
    ]
    return pd.DataFrame(rows, columns=["u", "v", "key", "geometry"])


def _oriented_ends(graph: nx.MultiGraph, u: str, v: str, geometry: LineString) -> tuple[str, str]:
    """Return the edge's two end nodes in the order its geometry runs.

    networkx may list an undirected edge's ends either way round (about a third of roads come out
    reversed). Cut distances are measured from the geometry's first point, so that end must come
    first, or a connection near the start of a line is attached to its far end.
    """
    first_x, first_y = geometry.coords[0][:2]

    def gap(node: str) -> float:
        return abs(float(graph.nodes[node]["x"]) - first_x) + abs(float(graph.nodes[node]["y"]) - first_y)

    return (u, v) if gap(u) <= gap(v) else (v, u)


def _split_edge_at(
    graph: nx.MultiGraph,
    u: str,
    v: str,
    key: str,
    split_points_m: list[tuple[float, str, Point]],
    metric_line: LineString,
    to_geographic,
) -> list[str]:
    """Replace edge ``(u, v, key)`` by pieces cut at the distances in ``split_points_m``.

    ``split_points_m`` holds ``(metres along metric_line, asset node, lon/lat point)``, sorted by
    distance. Returns the junction node made or reused for each cut, in the same order.
    """
    attrs = dict(graph.edges[u, v, key])
    u, v = _oriented_ends(graph, u, v, attrs["geometry"])
    source = attrs.get("source")
    region = attrs.get("region")
    edge_id = attrs["edge_id"]
    length_m = metric_line.length

    # A cut within SPLIT_SNAP_TOLERANCE_M of a line end uses that end node.
    cut_nodes: list[str] = []
    cut_positions: list[float] = []
    for distance_m, _, point in split_points_m:
        if distance_m <= SPLIT_SNAP_TOLERANCE_M:
            cut_nodes.append(u)
            cut_positions.append(0.0)
            continue
        if distance_m >= length_m - SPLIT_SNAP_TOLERANCE_M:
            cut_nodes.append(v)
            cut_positions.append(length_m)
            continue
        node = node_key(point.x, point.y)
        _add_junction(graph, node, point.x, point.y, source=source, region=region)
        cut_nodes.append(node)
        cut_positions.append(distance_m)

    first_position: dict[str, float] = {}
    for pos, node in zip(cut_positions, cut_nodes, strict=True):
        if 0.0 < pos < length_m:
            first_position.setdefault(node, pos)
    interior = sorted((pos, node) for node, pos in first_position.items())
    if not interior:
        return cut_nodes

    graph.remove_edge(u, v, key)
    boundaries = [(0.0, u), *interior, (length_m, v)]
    for piece_number, ((start_m, start_node), (end_m, end_node)) in enumerate(pairwise(boundaries), start=1):
        piece_metric = substring(metric_line, start_m, end_m)
        piece = to_geographic(piece_metric)
        piece_id = f"{edge_id}::{piece_number}"
        graph.add_edge(
            start_node,
            end_node,
            key=piece_id,
            **{**attrs, "edge_id": piece_id, "length_km": geodesic_length_km(piece), "geometry": piece},
        )
    return cut_nodes


def _anchor_assets(
    graph: nx.MultiGraph,
    assets: gpd.GeoDataFrame,
    *,
    max_anchor_distance_m: float,
    anchor_to_each_line_source: bool,
) -> None:
    """Connect each asset to the nearest point on the nearest line within ``max_anchor_distance_m``.

    With ``anchor_to_each_line_source`` an asset gets one connection per line ``source``, so a
    substation joins both the OSM roads and the CEB 66 kV lines.
    """
    edges = _edge_table(graph)
    asset_nodes = [_asset_node_id(row) for row in assets.itertuples()]
    for node in asset_nodes:
        graph.nodes[node]["anchor_status"] = "unanchored"
        graph.nodes[node]["anchor_distance_m"] = float("inf")
    if edges.empty or assets.empty:
        return

    metric_crs = gpd.GeoSeries(edges["geometry"], crs=GEOGRAPHIC_CRS).estimate_utm_crs()
    edges_metric = gpd.GeoSeries(edges["geometry"].to_numpy(), crs=GEOGRAPHIC_CRS).to_crs(metric_crs)
    asset_metric = assets.to_crs(metric_crs).geometry
    source_of_edge = pd.Series(
        [graph.edges[u, v, k].get("source") for u, v, k in edges[["u", "v", "key"]].itertuples(index=False)]
    )

    def to_geographic(geometry):
        return gpd.GeoSeries([geometry], crs=metric_crs).to_crs(GEOGRAPHIC_CRS).iloc[0]

    groups = sorted(set(source_of_edge.dropna())) if anchor_to_each_line_source else ["all"]
    # edge row -> [(metres along the line, asset node, lon/lat point)]
    cuts: dict[int, list[tuple[float, str, Point]]] = {}
    anchor_requests: list[tuple[str, int, float, str]] = []  # (asset node, edge row, distance in m, line source)
    for group in groups:
        member_rows = np.flatnonzero(source_of_edge.eq(group).to_numpy()) if group != "all" else np.arange(len(edges))
        tree = shapely.STRtree(edges_metric.to_numpy()[member_rows])
        asset_index, tree_index = tree.query_nearest(
            asset_metric.to_numpy(), max_distance=max_anchor_distance_m, all_matches=False
        )
        for asset_position, local_row in zip(asset_index, tree_index, strict=True):
            edge_row = int(member_rows[local_row])
            asset_node = asset_nodes[asset_position]
            point_metric = asset_metric.iloc[asset_position]
            line_metric = edges_metric.iloc[edge_row]
            along_m = float(line_metric.project(point_metric))
            distance_m = float(line_metric.distance(point_metric))
            snapped = to_geographic(line_metric.interpolate(along_m))
            cuts.setdefault(edge_row, []).append((along_m, asset_node, snapped))
            anchor_requests.append((asset_node, edge_row, distance_m, group))

    # For unconnected assets, record the distance to the nearest line, for review.
    tree_all = shapely.STRtree(edges_metric.to_numpy())
    anchored_nodes = {request[0] for request in anchor_requests}
    unanchored = [n for n in asset_nodes if n not in anchored_nodes]
    if unanchored:
        positions = [asset_nodes.index(n) for n in unanchored]
        _, distances = tree_all.query_nearest(
            asset_metric.iloc[positions].to_numpy(), return_distance=True, all_matches=False
        )
        for node, distance_m in zip(unanchored, distances, strict=True):
            graph.nodes[node]["anchor_distance_m"] = float(distance_m)

    # Split each line once at all its cuts, then add the connecting edges.
    junction_for_request: dict[tuple[str, int], str] = {}
    for edge_row, points in cuts.items():
        points = sorted(points, key=lambda item: item[0])
        u, v, key = edges.loc[edge_row, ["u", "v", "key"]]
        junctions = _split_edge_at(graph, u, v, key, points, edges_metric.iloc[edge_row], to_geographic)
        for (_, asset_node, _), junction in zip(points, junctions, strict=True):
            junction_for_request[(asset_node, edge_row)] = junction

    for asset_node, edge_row, distance_m, group in anchor_requests:
        junction = junction_for_request[(asset_node, edge_row)]
        asset_attrs = graph.nodes[asset_node]
        asset_kind = asset_attrs.get("kind", "substation")
        asset_id = asset_attrs.get("asset_id")
        asset_attrs["anchor_status"] = "anchored"
        asset_attrs["anchor_distance_m"] = min(float(asset_attrs["anchor_distance_m"]), distance_m)
        label = group if group != "all" else "road"
        edge_id = f"anchor::{asset_id}::{label}"
        graph.add_edge(
            asset_node,
            junction,
            key=edge_id,
            edge_id=edge_id,
            source=f"{asset_kind}_anchor",
            region=asset_attrs.get("region"),
            inferred=True,
            stage="connectivity_only",
            length_km=max(distance_m / 1000, 0.001),
            geometry=LineString(
                [
                    (float(asset_attrs["x"]), float(asset_attrs["y"])),
                    (float(graph.nodes[junction]["x"]), float(graph.nodes[junction]["y"])),
                ]
            ),
        )


def build_inferred_distribution_graph(
    power_assets: gpd.GeoDataFrame,
    *,
    precomputed_lines: gpd.GeoDataFrame | None = None,
    osm_distribution_lines: gpd.GeoDataFrame | None = None,
    provided_backbone_lines: gpd.GeoDataFrame | None = None,
    max_anchor_distance_m: float = DEFAULT_MAX_ANCHOR_DISTANCE_M,
    anchor_to_each_line_source: bool = False,
) -> nx.MultiGraph:
    """Return the estimated distribution network as a networkx MultiGraph (lon/lat, EPSG:4326).

    Each line becomes an edge with ``source`` ``precomputed``, ``osm`` or ``provided_transmission``;
    parallel lines between the same two nodes stay separate. ``power_assets`` (substations and
    generators) become nodes with ``is_root=True``. An asset within ``max_anchor_distance_m`` of a
    line is connected to the nearest point on it, and the line is split there.
    """
    if "asset_id" not in power_assets.columns and "bus_id" not in power_assets.columns:
        raise ValueError("power_assets must contain asset_id or bus_id")
    if max_anchor_distance_m < 0:
        raise ValueError("max_anchor_distance_m must be non-negative")

    graph = nx.MultiGraph(
        scenario="inferred_distribution",
        stage="connectivity_only",
        inferred=True,
        coordinate_crs=GEOGRAPHIC_CRS,
        max_anchor_distance_m=float(max_anchor_distance_m),
    )
    _add_distribution_lines(graph, precomputed_lines, source="precomputed")
    _add_distribution_lines(graph, osm_distribution_lines, source="osm")
    _add_distribution_lines(graph, provided_backbone_lines, source="provided_transmission")

    geographic_assets = power_assets.to_crs(GEOGRAPHIC_CRS)
    for row in geographic_assets.itertuples():
        asset_id = str(getattr(row, "asset_id", getattr(row, "bus_id", "")))
        asset_kind = str(getattr(row, "asset_kind", getattr(row, "kind", "substation"))).lower()
        graph.add_node(
            _asset_node_id(row),
            kind=asset_kind,
            inferred=False,
            bus_id=asset_id if asset_kind == "substation" else None,
            asset_id=asset_id,
            is_root=True,
            source=getattr(row, "source", "osm_power"),
            region=getattr(row, "region", None),
            provisional_root=bool(getattr(row, "provisional_root", False)),
            x=float(row.geometry.x),
            y=float(row.geometry.y),
            demand_mw=0.0,
        )

    _anchor_assets(
        graph,
        geographic_assets,
        max_anchor_distance_m=max_anchor_distance_m,
        anchor_to_each_line_source=anchor_to_each_line_source,
    )
    return graph


def assign_proxy_demand_to_graph(
    graph: nx.MultiGraph,
    demand_points: gpd.GeoDataFrame,
    *,
    demand_column: str = "demand_mw",
) -> nx.MultiGraph:
    """Return a copy of ``graph`` with each point's demand (MW) added to the nearest road node.

    Kept for the interruption step. Raises ValueError for negative demand or a graph with no road nodes.
    """
    if demand_column not in demand_points.columns:
        raise ValueError(f"demand_points must contain {demand_column}")
    candidates = _distribution_nodes(graph)
    if not candidates:
        raise ValueError("Cannot assign proxy demand without distribution nodes")

    updated = graph.copy()
    candidate_points = gpd.GeoSeries(
        [Point(float(updated.nodes[n]["x"]), float(updated.nodes[n]["y"])) for n in candidates], crs=GEOGRAPHIC_CRS
    )
    metric_crs = candidate_points.estimate_utm_crs()
    tree = shapely.STRtree(candidate_points.to_crs(metric_crs).to_numpy())
    points = demand_points.to_crs(metric_crs)
    demands = points[demand_column].astype(float).to_numpy()
    if (demands < 0).any():
        raise ValueError("Proxy demand cannot be negative")
    point_index, nearest = tree.query_nearest(points.geometry.to_numpy(), all_matches=False)
    for position, candidate_position in zip(point_index, nearest, strict=True):
        node = candidates[candidate_position]
        updated.nodes[node]["demand_mw"] = float(updated.nodes[node].get("demand_mw", 0.0)) + float(demands[position])
    return updated


def topology_disconnection_impacts(
    graph: nx.MultiGraph,
    *,
    failed_bus_ids: list[str] | tuple[str, ...] = (),
    failed_edge_ids: list[str] | tuple[str, ...] = (),
) -> pd.DataFrame:
    """Return the demand (MW) of each graph component cut off from every power asset by the failures.

    ``failed_bus_ids`` are substation ids. Kept for the interruption step.
    """
    scenario = graph.copy()
    failed_bus_nodes = {f"bus::{bus_id}" for bus_id in failed_bus_ids}
    scenario.remove_nodes_from(node for node in failed_bus_nodes if node in scenario)

    failed_edges = set(failed_edge_ids)
    scenario.remove_edges_from(
        [
            (u, v, key)
            for u, v, key, attrs in scenario.edges(keys=True, data=True)
            if attrs.get("edge_id") in failed_edges
        ]
    )

    rows: list[dict[str, object]] = []
    root_nodes = {
        node for node, attrs in scenario.nodes(data=True) if attrs.get("is_root", attrs.get("kind") == "substation")
    }
    for component_id, nodes in enumerate(nx.connected_components(scenario), start=1):
        node_set = set(nodes)
        has_root = bool(node_set & root_nodes)
        demand_mw = sum(float(scenario.nodes[node].get("demand_mw", 0.0)) for node in node_set)
        if has_root or demand_mw == 0:
            continue
        rows.append(
            {
                "component_id": component_id,
                "unserved_demand_mw": demand_mw,
                "node_count": len(node_set),
                "edge_count": scenario.subgraph(node_set).number_of_edges(),
                "inferred": True,
                "stage": "connectivity_only",
            }
        )
    return pd.DataFrame(
        rows,
        columns=["component_id", "unserved_demand_mw", "node_count", "edge_count", "inferred", "stage"],
    )


def write_inferred_distribution_tables(graph: nx.MultiGraph, output_dir: Path) -> InferredDistributionOutputs:
    """Write ``inferred_distribution_{nodes,edges}.csv`` and ``inferred_distribution_metadata.json``."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes = pd.DataFrame([{"node_id": node, **attrs} for node, attrs in graph.nodes(data=True)])
    edges = pd.DataFrame([{"u": u, "v": v, **attrs} for u, v, attrs in graph.edges(data=True)])
    metadata = {
        "scenario": graph.graph.get("scenario"),
        "stage": graph.graph.get("stage"),
        "inferred": graph.graph.get("inferred"),
        "max_anchor_distance_m": graph.graph.get("max_anchor_distance_m"),
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
    }

    node_path = output_dir / "inferred_distribution_nodes.csv"
    edge_path = output_dir / "inferred_distribution_edges.csv"
    metadata_path = output_dir / "inferred_distribution_metadata.json"
    nodes.to_csv(node_path, index=False)
    edges.to_csv(edge_path, index=False)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return InferredDistributionOutputs(nodes=node_path, edges=edge_path, metadata=metadata_path)
