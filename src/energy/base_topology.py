"""Derive the base transmission topology from provided route geometry."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, pairwise

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, polygonize, substring, unary_union

METRIC_CRS = "EPSG:32740"
STATION_JOIN_TOLERANCE_M = 100.0
MEANINGFUL_CYCLE_AREA_M2 = 1_000_000.0

# The provided point layer has generic labels. These names are transcribed from
# the supplied CEB 2025 network map, in the point layer's north-to-south order.
CEB_SUBSTATION_NAMES = {
    "SUB_001": "Sottise",
    "SUB_002": "Belle Vue",
    "SUB_003": "Jin Fei",
    "SUB_004": "Amaury",
    "SUB_005": "Dumas",
    "SUB_006": "LTK",
    "SUB_007": "F.U.E.L. S.E.",
    "SUB_008": "Anahita",
    "SUB_009": "Wooton",
    "SUB_010": "La Chaumiere",
    "SUB_011": "Ebene",
    "SUB_012": "Henrietta",
    "SUB_013": "Champagne",
    "SUB_014": "Case Noyale",
    "SUB_015": "Union Vale",
    "SUB_016": "Combo",
    "SUB_017": "L'Avenir",
    "SUB_018": "St Louis",
}

# This 171.5 m discontinuity opens the Ebene-Wooton side of the blue loop on
# the CEB map. It is kept explicit rather than hidden in a broad global snap.
REVIEWED_ROUTE_JOINS = (
    (
        "CEB_EBENE_WOOTON",
        "ROUTE_001_PART_008",
        "ROUTE_001_PART_001",
        200.0,
    ),
)


@dataclass(frozen=True)
class DerivedBaseTopology:
    buses: gpd.GeoDataFrame
    lines: gpd.GeoDataFrame
    route_gap_count: int
    route_gap_length_km: float
    connected_components: int
    substation_count: int
    junction_count: int
    cycle_rank: int
    meaningful_cycle_count: int
    source_route_length_km: float
    retained_source_length_km: float
    parallel_edge_count: int


def _require_columns(
    frame: pd.DataFrame,
    columns: set[str],
    label: str,
) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"{label} missing columns: {sorted(missing)}")


def _segments(geometry) -> list[LineString]:
    if geometry.geom_type == "LineString":
        return [geometry]
    return [part for part in geometry.geoms if part.geom_type == "LineString"]


def _node_key(coordinate: tuple[float, ...]) -> tuple[float, float]:
    return round(float(coordinate[0]), 3), round(float(coordinate[1]), 3)


def _linework_graph(linework) -> nx.MultiGraph:
    """Return noded linework without collapsing distinct parallel routes."""
    graph = nx.MultiGraph()
    for segment in _segments(linework):
        coordinates = list(segment.coords)
        graph.add_edge(
            _node_key(coordinates[0]),
            _node_key(coordinates[-1]),
            geometry=segment,
        )
    return graph


def _component_geometries(linework) -> list:
    graph = _linework_graph(linework)
    return [
        unary_union([attrs["geometry"] for _, _, attrs in graph.subgraph(nodes).edges(data=True)])
        for nodes in nx.connected_components(graph)
    ]


def _station_gap_connectors(
    linework,
    substations: gpd.GeoDataFrame,
    tolerance_m: float,
) -> tuple[list[LineString], set[frozenset[int]]]:
    """Join route ends through one station anchor instead of a connector clique."""
    components = _component_geometries(linework)
    connectors: list[LineString] = []
    station_pairs: set[frozenset[int]] = set()
    for row in substations.itertuples():
        nearby = [
            (index, component, float(component.distance(row.geometry)))
            for index, component in enumerate(components)
            if component.distance(row.geometry) <= tolerance_m
        ]
        if len(nearby) < 2:
            continue

        anchor_index, anchor_component, _ = min(nearby, key=lambda item: item[2])
        anchor = nearest_points(row.geometry, anchor_component)[1]
        for first, second in combinations((item[0] for item in nearby), 2):
            station_pairs.add(frozenset((first, second)))
        for component_index, component, _ in nearby:
            if component_index == anchor_index:
                continue
            end = nearest_points(anchor, component)[1]
            if anchor.distance(end) > 0.001:
                connectors.append(LineString([anchor, end]))
    return connectors, station_pairs


def _candidate_clusters(candidates: list[dict[str, object]], tolerance_m: float) -> list[set[int]]:
    """Group gap candidates that describe one local multi-route junction."""
    graph = nx.Graph()
    graph.add_nodes_from(range(len(candidates)))
    for first, second in combinations(range(len(candidates)), 2):
        first_midpoint = candidates[first]["geometry"].interpolate(0.5, normalized=True)
        second_midpoint = candidates[second]["geometry"].interpolate(0.5, normalized=True)
        if first_midpoint.distance(second_midpoint) <= tolerance_m:
            graph.add_edge(first, second)
    return list(nx.connected_components(graph))


def _route_gap_connectors(
    linework,
    tolerance_m: float,
    *,
    excluded_component_pairs: set[frozenset[int]] | None = None,
) -> list[LineString]:
    """Connect local route gaps using one minimal junction tree per location."""
    if tolerance_m < 0:
        raise ValueError("route_gap_tolerance_m must be non-negative")
    if tolerance_m == 0:
        return []
    components = _component_geometries(linework)
    excluded_component_pairs = excluded_component_pairs or set()
    candidates: list[dict[str, object]] = []
    for index, first in enumerate(components):
        for second_index, second in enumerate(components[index + 1 :], start=index + 1):
            component_pair = frozenset((index, second_index))
            if component_pair in excluded_component_pairs:
                continue
            distance = float(first.distance(second))
            if not 0.001 < distance <= tolerance_m:
                continue
            start, end = nearest_points(first, second)
            candidates.append(
                {
                    "component0": index,
                    "component1": second_index,
                    "distance": distance,
                    "geometry": LineString([start, end]),
                }
            )

    connectors: list[LineString] = []
    for cluster in _candidate_clusters(candidates, tolerance_m):
        junction = nx.Graph()
        for candidate_index in cluster:
            candidate = candidates[candidate_index]
            node0 = int(candidate["component0"])
            node1 = int(candidate["component1"])
            distance = float(candidate["distance"])
            existing = junction.get_edge_data(node0, node1)
            if existing is None or distance < existing["weight"]:
                junction.add_edge(
                    node0,
                    node1,
                    weight=distance,
                    candidate_index=candidate_index,
                )
        for _, _, attrs in nx.minimum_spanning_edges(junction, data=True):
            connectors.append(candidates[attrs["candidate_index"]]["geometry"])
    return connectors


def _reviewed_route_gap_connectors(route_parts: gpd.GeoDataFrame) -> list[LineString]:
    """Return the small set of CEB-map joins that lack a station anchor."""
    by_id = route_parts.set_index("route_part_id")["geometry"]
    connectors: list[LineString] = []
    for _, first_id, second_id, maximum_distance_m in REVIEWED_ROUTE_JOINS:
        if first_id not in by_id.index or second_id not in by_id.index:
            continue
        first = by_id.loc[first_id]
        second = by_id.loc[second_id]
        distance = float(first.distance(second))
        if not 0.001 < distance <= maximum_distance_m:
            raise ValueError(
                f"Reviewed route join {first_id} -> {second_id} is {distance:.1f} m; "
                f"expected at most {maximum_distance_m:.1f} m"
            )
        start, end = nearest_points(first, second)
        connectors.append(LineString([start, end]))
    return connectors


def _deduplicate_connectors(connectors: list[LineString]) -> list[LineString]:
    unique: dict[tuple[tuple[float, float], tuple[float, float]], LineString] = {}
    for connector in connectors:
        coordinates = list(connector.coords)
        key = tuple(sorted((_node_key(coordinates[0]), _node_key(coordinates[-1]))))
        unique[key] = connector
    return list(unique.values())


def _meaningful_cycle_count(graph: nx.MultiGraph) -> int:
    """Count independent cycles whose mapped footprint is at least 1 km²."""
    count = sum(
        Polygon(cycle).area >= MEANINGFUL_CYCLE_AREA_M2 for cycle in nx.cycle_basis(nx.Graph(graph)) if len(cycle) >= 3
    )
    for node0, node1 in {
        frozenset((node0, node1))
        for node0, node1 in graph.edges()
        if node0 != node1 and graph.number_of_edges(node0, node1) > 1
    }:
        edges = list(graph.get_edge_data(node0, node1).values())
        reference = edges[0]["geometry"]
        for attrs in edges[1:]:
            polygons = list(polygonize(unary_union([reference, attrs["geometry"]])))
            count += any(polygon.area >= MEANINGFUL_CYCLE_AREA_M2 for polygon in polygons)
    return count


def _prepared_route_parts(routes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    parts = routes.to_crs(METRIC_CRS).explode(index_parts=False).reset_index(drop=True)
    parts = parts[parts.geometry.geom_type.eq("LineString")].copy()
    if parts.empty:
        raise ValueError("transmission_routes has no LineString geometry")
    parts["route_part_id"] = [
        f"{route_id}_PART_{number:03d}"
        for route_id, number in zip(
            parts["route_id"],
            parts.groupby("route_id").cumcount() + 1,
            strict=True,
        )
    ]
    return parts


def _segment_source(
    segment: LineString,
    route_parts: gpd.GeoDataFrame,
    gap_connectors: list[LineString],
    *,
    default_voltage_kv: float,
) -> dict[str, object]:
    midpoint = segment.interpolate(0.5, normalized=True)
    route_distances = route_parts.geometry.distance(midpoint)
    route_index = route_distances.idxmin()
    route_distance = float(route_distances.loc[route_index])
    gap_distance = (
        min(float(connector.distance(midpoint)) for connector in gap_connectors) if gap_connectors else float("inf")
    )
    if gap_distance < 0.01 and route_distance >= 0.01:
        return {
            "source": "derived_route_gap",
            "source_route_id": pd.NA,
            "source_route_part_id": pd.NA,
            "circuit_id": pd.NA,
            "v_nom_kv": default_voltage_kv,
        }

    route = route_parts.loc[route_index]
    voltage = route.get("v_nom_kv", default_voltage_kv)
    if pd.isna(voltage):
        voltage = default_voltage_kv
    return {
        "source": "provided_transmission_geometry",
        "source_route_id": str(route["route_id"]),
        "source_route_part_id": str(route["route_part_id"]),
        "circuit_id": str(route["route_part_id"]),
        "v_nom_kv": float(voltage),
    }


def _split_graph_at_substations(
    linework,
    substations: gpd.GeoDataFrame,
    route_parts: gpd.GeoDataFrame,
    gap_connectors: list[LineString],
    *,
    default_voltage_kv: float,
) -> tuple[nx.MultiGraph, dict[str, Point]]:
    bus_points = {str(row.bus_id): nearest_points(row.geometry, linework)[1] for row in substations.itertuples()}
    connector_points = [
        Point(coordinate) for connector in gap_connectors for coordinate in (connector.coords[0], connector.coords[-1])
    ]
    graph = nx.MultiGraph()
    for segment in _segments(linework):
        cut_distances = []
        # GEOS can leave a connector endpoint a few floating-point units off
        # the route it was projected onto. Explicit cuts make the intended
        # junction survive noding and the millimetre-rounded graph keys.
        for point in [*bus_points.values(), *connector_points]:
            if segment.distance(point) >= 0.02:
                continue
            distance = float(segment.project(point))
            if 0.02 < distance < segment.length - 0.02:
                cut_distances.append(round(distance, 3))
        distances = [0.0, *sorted(set(cut_distances)), float(segment.length)]
        source = _segment_source(
            segment,
            route_parts,
            gap_connectors,
            default_voltage_kv=default_voltage_kv,
        )
        for start, end in pairwise(distances):
            if end - start < 0.001:
                continue
            piece = substring(segment, start, end)
            coordinates = list(piece.coords)
            node0 = _node_key(coordinates[0])
            node1 = _node_key(coordinates[-1])
            if node0 == node1:
                continue
            graph.add_edge(
                node0,
                node1,
                geometry=piece,
                length_km=float(piece.length / 1000),
                **source,
            )

    for bus_id, point in bus_points.items():
        node = _node_key((point.x, point.y))
        if node not in graph:
            node = min(
                graph,
                key=lambda candidate: (candidate[0] - point.x) ** 2 + (candidate[1] - point.y) ** 2,
            )
            distance = ((node[0] - point.x) ** 2 + (node[1] - point.y) ** 2) ** 0.5
            if distance > 0.01:
                raise ValueError(f"Could not place {bus_id} on derived route graph")
        existing_bus_id = graph.nodes[node].get("bus_id")
        if existing_bus_id is not None and existing_bus_id != bus_id:
            raise ValueError(f"Substations {existing_bus_id} and {bus_id} occupy one node")
        graph.nodes[node]["bus_id"] = bus_id
    return graph, bus_points


def _prune_unserved_route_ends(graph: nx.MultiGraph) -> None:
    for component in list(nx.connected_components(graph)):
        if not any(graph.nodes[node].get("bus_id") for node in component):
            graph.remove_nodes_from(component)
    while True:
        leaves = [node for node in graph if len(graph[node]) <= 1 and not graph.nodes[node].get("bus_id")]
        if not leaves:
            return
        graph.remove_nodes_from(leaves)


def derive_base_topology(
    snapped_substations: gpd.GeoDataFrame,
    transmission_routes: gpd.GeoDataFrame,
    *,
    route_gap_tolerance_m: float = 75,
    default_voltage_kv: float = 66,
    topology_capacity_mva: float = 10_000,
) -> DerivedBaseTopology:
    """Create a connected, topology-only base network from provided geometry.

    Short gaps between route components are retained as explicit derived
    connectors. Line ratings use a deliberately non-binding topology proxy
    until reviewed engineering ratings are available.
    """
    _require_columns(snapped_substations, {"bus_id", "geometry"}, "substations")
    _require_columns(
        transmission_routes,
        {"route_id", "geometry"},
        "transmission_routes",
    )
    if default_voltage_kv <= 0:
        raise ValueError("default_voltage_kv must be greater than zero")
    if topology_capacity_mva <= 0:
        raise ValueError("topology_capacity_mva must be greater than zero")

    substations = snapped_substations.to_crs(METRIC_CRS).copy()
    route_parts = _prepared_route_parts(transmission_routes)
    original_linework = unary_union(list(route_parts.geometry))
    station_connectors, station_pairs = _station_gap_connectors(
        original_linework,
        substations,
        STATION_JOIN_TOLERANCE_M,
    )
    local_connectors = _route_gap_connectors(
        original_linework,
        route_gap_tolerance_m,
        excluded_component_pairs=station_pairs,
    )
    reviewed_connectors = _reviewed_route_gap_connectors(route_parts)
    gap_connectors = _deduplicate_connectors([*station_connectors, *local_connectors, *reviewed_connectors])
    linework = unary_union([original_linework, *gap_connectors])
    graph, _bus_points = _split_graph_at_substations(
        linework,
        substations,
        route_parts,
        gap_connectors,
        default_voltage_kv=default_voltage_kv,
    )
    _prune_unserved_route_ends(graph)

    bus_nodes = {
        str(attrs["bus_id"]): node for node, attrs in graph.nodes(data=True) if attrs.get("bus_id") is not None
    }
    missing_buses = sorted(set(substations["bus_id"].astype(str)) - set(bus_nodes))
    if missing_buses:
        raise ValueError(f"Derived topology omitted substations: {missing_buses}")

    node_ids = {node: bus_id for bus_id, node in bus_nodes.items()}
    junction_nodes = sorted(node for node in graph if node not in node_ids)
    node_ids.update({node: f"JUNCTION_{number:03d}" for number, node in enumerate(junction_nodes, start=1)})

    source_substation_names = (
        substations.set_index(substations["bus_id"].astype(str))["name"].to_dict() if "name" in substations else {}
    )
    substation_names = {}
    for bus_id in bus_nodes:
        source_name = source_substation_names.get(bus_id)
        if pd.isna(source_name) or str(source_name).strip() in {"", bus_id, "Substation"}:
            substation_names[bus_id] = CEB_SUBSTATION_NAMES.get(bus_id, bus_id)
        else:
            substation_names[bus_id] = str(source_name)
    bus_rows = []
    for node, bus_id in sorted(node_ids.items(), key=lambda item: item[1]):
        is_junction = node not in bus_nodes.values()
        bus_rows.append(
            {
                "bus_id": bus_id,
                "name": bus_id if is_junction else substation_names.get(bus_id, bus_id),
                "kind": "junction" if is_junction else "substation",
                "v_nom_kv": default_voltage_kv,
                "source": "derived_route_junction" if is_junction else "provided_substation_snapped",
                "geometry": Point(*node),
            }
        )
    buses = gpd.GeoDataFrame(bus_rows, geometry="geometry", crs=METRIC_CRS).to_crs("EPSG:4326")

    line_rows = []
    sorted_edges = sorted(
        graph.edges(keys=True, data=True),
        key=lambda edge: (
            tuple(sorted((node_ids[edge[0]], node_ids[edge[1]]))),
            str(edge[2]),
        ),
    )
    for number, (node0, node1, _edge_key, attrs) in enumerate(sorted_edges, start=1):
        line_rows.append(
            {
                "line_id": f"BASE_LINE_{number:03d}",
                "bus0": node_ids[node0],
                "bus1": node_ids[node1],
                "v_nom_kv": float(attrs["v_nom_kv"]),
                "length_km": float(attrs["length_km"]),
                "s_nom_mva": topology_capacity_mva,
                "source_route_id": attrs["source_route_id"],
                "source_route_part_id": attrs["source_route_part_id"],
                "circuit_id": attrs["circuit_id"],
                "source": attrs["source"],
                "derived": True,
                "inferred": False,
                "stage": "topology_only",
                "rating_basis": "non_binding_topology_proxy",
                "geometry": attrs["geometry"],
            }
        )
    lines = gpd.GeoDataFrame(
        line_rows,
        geometry="geometry",
        crs=METRIC_CRS,
    ).to_crs("EPSG:4326")
    cycle_rank = graph.number_of_edges() - graph.number_of_nodes() + nx.number_connected_components(graph)
    meaningful_cycle_count = _meaningful_cycle_count(graph)
    source_route_length_km = float(route_parts.geometry.length.sum() / 1000)
    retained_source_length_km = float(
        sum(attrs["length_km"] for *_, attrs in sorted_edges if attrs["source"] == "provided_transmission_geometry")
    )
    endpoint_pairs = {frozenset((node0, node1)) for node0, node1, _, _ in sorted_edges if node0 != node1}
    parallel_edge_count = sum(
        max(0, graph.number_of_edges(*tuple(pair)) - 1) for pair in endpoint_pairs if len(pair) == 2
    )

    return DerivedBaseTopology(
        buses=buses,
        lines=lines,
        route_gap_count=len(gap_connectors),
        route_gap_length_km=sum(connector.length for connector in gap_connectors) / 1000,
        connected_components=nx.number_connected_components(graph),
        substation_count=len(bus_nodes),
        junction_count=len(junction_nodes),
        cycle_rank=cycle_rank,
        meaningful_cycle_count=meaningful_cycle_count,
        source_route_length_km=source_route_length_km,
        retained_source_length_km=retained_source_length_km,
        parallel_edge_count=parallel_edge_count,
    )
