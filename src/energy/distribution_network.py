"""Inferred distribution-network graph experiments.

This module is deliberately separate from the provided transmission baseline.
It supports a topology-only scenario: infer candidate feeders from precomputed
or OSM lines, anchor them to known power assets, place proxy demand on graph
nodes and estimate demand disconnected by graph cuts. It does not run
distribution power flow or create confirmed engineering assets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import Geod
from shapely.geometry import LineString

GEOGRAPHIC_CRS = "EPSG:4326"
DEFAULT_MAX_ANCHOR_DISTANCE_M = 1000.0
WGS84_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class InferredDistributionOutputs:
    nodes: Path
    edges: Path
    metadata: Path


def _node_key(x: float, y: float) -> str:
    return f"dist::{round(x, 6)}::{round(y, 6)}"


def _line_endpoints(line: LineString) -> tuple[tuple[float, float], tuple[float, float]]:
    coords = list(line.coords)
    return (float(coords[0][0]), float(coords[0][1])), (
        float(coords[-1][0]),
        float(coords[-1][1]),
    )


def geodesic_length_km(line: LineString) -> float:
    """Measure a geographic line on WGS84 without assuming one UTM zone."""
    return abs(float(WGS84_GEOD.geometry_length(line))) / 1000


def _distribution_nodes(graph: nx.Graph) -> list[str]:
    return [node for node, attrs in graph.nodes(data=True) if attrs.get("kind") == "distribution_node"]


def _nearest_node(
    graph: nx.Graph,
    x: float,
    y: float,
    candidates: list[str],
) -> tuple[str | None, float]:
    if not candidates:
        return None, float("inf")
    distances = {
        node: abs(
            float(
                WGS84_GEOD.inv(
                    x,
                    y,
                    float(graph.nodes[node]["x"]),
                    float(graph.nodes[node]["y"]),
                )[2]
            )
        )
        for node in candidates
    }
    node = min(distances, key=distances.get)
    return node, float(distances[node])


def _add_distribution_lines(
    graph: nx.Graph,
    lines: gpd.GeoDataFrame | None,
    *,
    source: str,
) -> None:
    if lines is None or lines.empty:
        return

    prepared = lines.to_crs(GEOGRAPHIC_CRS).explode(index_parts=False).copy()
    prepared = prepared[prepared.geometry.geom_type.eq("LineString")]
    for row_number, row in enumerate(prepared.itertuples(), start=1):
        region = getattr(row, "region", None)
        start, end = _line_endpoints(row.geometry)
        start_node = _node_key(*start)
        end_node = _node_key(*end)
        length_km = geodesic_length_km(row.geometry)
        if start_node == end_node or length_km <= 0:
            continue
        graph.add_node(
            start_node,
            kind="distribution_node",
            inferred=True,
            source=source,
            region=region,
            x=start[0],
            y=start[1],
            demand_mw=graph.nodes[start_node].get("demand_mw", 0.0) if start_node in graph else 0.0,
        )
        graph.nodes[start_node].setdefault("line_sources", set()).add(source)
        graph.add_node(
            end_node,
            kind="distribution_node",
            inferred=True,
            source=source,
            region=region,
            x=end[0],
            y=end[1],
            demand_mw=graph.nodes[end_node].get("demand_mw", 0.0) if end_node in graph else 0.0,
        )
        graph.nodes[end_node].setdefault("line_sources", set()).add(source)
        graph.add_edge(
            start_node,
            end_node,
            edge_id=f"{source}_{row_number:06d}",
            source=source,
            region=region,
            inferred=True,
            stage="connectivity_only",
            length_km=length_km,
            geometry=row.geometry,
        )


def build_inferred_distribution_graph(
    power_assets: gpd.GeoDataFrame,
    *,
    precomputed_lines: gpd.GeoDataFrame | None = None,
    osm_distribution_lines: gpd.GeoDataFrame | None = None,
    provided_backbone_lines: gpd.GeoDataFrame | None = None,
    max_anchor_distance_m: float = DEFAULT_MAX_ANCHOR_DISTANCE_M,
    anchor_to_each_line_source: bool = False,
) -> nx.Graph:
    """Build a labelled topology-only distribution graph.

    Supplied substations and generator sites are added as root nodes.
    Precomputed and OSM line endpoints become inferred distribution nodes. A
    power asset is anchored to the nearest distribution node only when it lies within
    ``max_anchor_distance_m``.
    """
    if "asset_id" not in power_assets.columns and "bus_id" not in power_assets.columns:
        raise ValueError("power_assets must contain asset_id or bus_id")
    if max_anchor_distance_m < 0:
        raise ValueError("max_anchor_distance_m must be non-negative")

    graph = nx.Graph(
        scenario="inferred_distribution",
        stage="connectivity_only",
        inferred=True,
        coordinate_crs=GEOGRAPHIC_CRS,
        max_anchor_distance_m=float(max_anchor_distance_m),
    )
    _add_distribution_lines(graph, precomputed_lines, source="precomputed")
    _add_distribution_lines(graph, osm_distribution_lines, source="osm")
    _add_distribution_lines(
        graph,
        provided_backbone_lines,
        source="provided_transmission",
    )

    geographic_assets = power_assets.to_crs(GEOGRAPHIC_CRS)
    for row in geographic_assets.itertuples():
        asset_id = str(getattr(row, "asset_id", getattr(row, "bus_id", "")))
        asset_kind = str(getattr(row, "asset_kind", getattr(row, "kind", "substation"))).lower()
        node_prefix = "bus" if asset_kind == "substation" else "asset"
        asset_node = f"{node_prefix}::{asset_id}"
        graph.add_node(
            asset_node,
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

    candidates = _distribution_nodes(graph)
    candidates_by_source = {
        source: [node for node in candidates if source in graph.nodes[node].get("line_sources", set())]
        for source in {source for node in candidates for source in graph.nodes[node].get("line_sources", set())}
    }
    for row in geographic_assets.itertuples():
        asset_id = str(getattr(row, "asset_id", getattr(row, "bus_id", "")))
        asset_kind = str(getattr(row, "asset_kind", getattr(row, "kind", "substation"))).lower()
        node_prefix = "bus" if asset_kind == "substation" else "asset"
        asset_node = f"{node_prefix}::{asset_id}"
        candidate_groups = candidates_by_source.items() if anchor_to_each_line_source else (("all", candidates),)
        anchors: list[tuple[str, str, float]] = []
        for line_source, source_candidates in candidate_groups:
            nearest, distance_m = _nearest_node(
                graph,
                float(row.geometry.x),
                float(row.geometry.y),
                source_candidates,
            )
            if nearest is not None and distance_m <= max_anchor_distance_m:
                anchors.append((line_source, nearest, distance_m))
        if not anchors:
            _, distance_m = _nearest_node(
                graph,
                float(row.geometry.x),
                float(row.geometry.y),
                candidates,
            )
            graph.nodes[asset_node]["anchor_status"] = "unanchored"
            graph.nodes[asset_node]["anchor_distance_m"] = distance_m
            continue
        graph.nodes[asset_node]["anchor_status"] = "anchored"
        graph.nodes[asset_node]["anchor_distance_m"] = min(distance_m for _, _, distance_m in anchors)
        for line_source, nearest, distance_m in anchors:
            graph.add_edge(
                asset_node,
                nearest,
                edge_id=f"anchor::{asset_id}::{line_source}",
                source=f"{asset_kind}_anchor",
                region=graph.nodes[asset_node].get("region"),
                inferred=True,
                stage="connectivity_only",
                length_km=distance_m / 1000,
                geometry=LineString(
                    [
                        (float(row.geometry.x), float(row.geometry.y)),
                        (
                            float(graph.nodes[nearest]["x"]),
                            float(graph.nodes[nearest]["y"]),
                        ),
                    ]
                ),
            )
    return graph


def assign_proxy_demand_to_graph(
    graph: nx.Graph,
    demand_points: gpd.GeoDataFrame,
    *,
    demand_column: str = "demand_mw",
) -> nx.Graph:
    """Attach demand proxy values to the nearest inferred distribution node."""
    if demand_column not in demand_points.columns:
        raise ValueError(f"demand_points must contain {demand_column}")
    candidates = _distribution_nodes(graph)
    if not candidates:
        raise ValueError("Cannot assign proxy demand without distribution nodes")

    updated = graph.copy()
    geographic_points = demand_points.to_crs(GEOGRAPHIC_CRS)
    for row in geographic_points.itertuples():
        demand = float(getattr(row, demand_column))
        if demand < 0:
            raise ValueError("Proxy demand cannot be negative")
        nearest, _ = _nearest_node(
            updated,
            float(row.geometry.x),
            float(row.geometry.y),
            candidates,
        )
        if nearest is None:
            continue
        updated.nodes[nearest]["demand_mw"] = float(updated.nodes[nearest].get("demand_mw", 0.0)) + demand
    return updated


def topology_disconnection_impacts(
    graph: nx.Graph,
    *,
    failed_bus_ids: list[str] | tuple[str, ...] = (),
    failed_edge_ids: list[str] | tuple[str, ...] = (),
) -> pd.DataFrame:
    """Return demand on graph components disconnected from every power root."""
    scenario = graph.copy()
    failed_bus_nodes = {f"bus::{bus_id}" for bus_id in failed_bus_ids}
    scenario.remove_nodes_from(node for node in failed_bus_nodes if node in scenario)

    failed_edges = set(failed_edge_ids)
    edges_to_remove = [(u, v) for u, v, attrs in scenario.edges(data=True) if attrs.get("edge_id") in failed_edges]
    scenario.remove_edges_from(edges_to_remove)

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
        edge_count = scenario.subgraph(node_set).number_of_edges()
        rows.append(
            {
                "component_id": component_id,
                "unserved_demand_mw": demand_mw,
                "node_count": len(node_set),
                "edge_count": edge_count,
                "inferred": True,
                "stage": "connectivity_only",
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "component_id",
            "unserved_demand_mw",
            "node_count",
            "edge_count",
            "inferred",
            "stage",
        ],
    )


def write_inferred_distribution_tables(
    graph: nx.Graph,
    output_dir: Path,
) -> InferredDistributionOutputs:
    """Write graph nodes, edges and metadata to CSV/JSON review files."""
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
    return InferredDistributionOutputs(
        nodes=node_path,
        edges=edge_path,
        metadata=metadata_path,
    )
