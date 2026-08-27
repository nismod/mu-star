import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from energy.distribution_network import (
    DEFAULT_MAX_ANCHOR_DISTANCE_M,
    assign_proxy_demand_to_graph,
    build_inferred_distribution_graph,
    geodesic_length_km,
    topology_disconnection_impacts,
    write_inferred_distribution_tables,
)


def test_inferred_distribution_graph_is_anchored_and_labelled(tmp_path):
    substations = gpd.GeoDataFrame(
        {"bus_id": ["SUB_001"], "geometry": [Point(57.5, -20.2)]},
        crs="EPSG:4326",
    )
    precomputed = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(57.5001, -20.2), (57.501, -20.2)]),
            ]
        },
        crs="EPSG:4326",
    )

    graph = build_inferred_distribution_graph(
        substations,
        precomputed_lines=precomputed,
        max_anchor_distance_m=100,
    )
    outputs = write_inferred_distribution_tables(graph, tmp_path)

    assert graph.graph["inferred"] is True
    assert graph.graph["stage"] == "connectivity_only"
    assert graph.nodes["bus::SUB_001"]["anchor_status"] == "anchored"
    assert outputs.nodes.is_file()
    assert outputs.edges.is_file()
    assert outputs.metadata.is_file()


def test_topology_disconnection_counts_only_demand_without_substation_root():
    substations = gpd.GeoDataFrame(
        {"bus_id": ["SUB_001"], "geometry": [Point(57.5, -20.2)]},
        crs="EPSG:4326",
    )
    precomputed = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(57.5001, -20.2), (57.501, -20.2)]),
            ]
        },
        crs="EPSG:4326",
    )
    demand_points = gpd.GeoDataFrame(
        {"demand_mw": [3.0], "geometry": [Point(57.501, -20.2)]},
        crs="EPSG:4326",
    )
    graph = build_inferred_distribution_graph(
        substations,
        precomputed_lines=precomputed,
        max_anchor_distance_m=100,
    )
    graph = assign_proxy_demand_to_graph(graph, demand_points)

    supplied = topology_disconnection_impacts(graph)
    failed = topology_disconnection_impacts(graph, failed_bus_ids=["SUB_001"])

    assert supplied.empty
    assert failed["unserved_demand_mw"].sum() == 3.0


def test_proxy_demand_requires_distribution_nodes():
    substations = gpd.GeoDataFrame(
        {"bus_id": ["SUB_001"], "geometry": [Point(57.5, -20.2)]},
        crs="EPSG:4326",
    )
    demand_points = gpd.GeoDataFrame(
        {"demand_mw": [3.0], "geometry": [Point(57.501, -20.2)]},
        crs="EPSG:4326",
    )
    graph = build_inferred_distribution_graph(substations)

    with pytest.raises(ValueError, match="without distribution nodes"):
        assign_proxy_demand_to_graph(graph, demand_points)


def test_geodesic_graph_preserves_region_and_default_anchor_distance():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["ROD_SUB"],
            "source": ["provisional_road_centroid"],
            "region": ["rodrigues"],
            "provisional_root": [True],
            "geometry": [Point(63.4, -19.7)],
        },
        crs="EPSG:4326",
    )
    roads = gpd.GeoDataFrame(
        {
            "region": ["rodrigues"],
            "geometry": [LineString([(63.4, -19.7), (63.41, -19.7)])],
        },
        crs="EPSG:4326",
    )

    graph = build_inferred_distribution_graph(
        substations,
        osm_distribution_lines=roads,
    )
    road_edge = next(attrs for _, _, attrs in graph.edges(data=True) if attrs["source"] == "osm")

    assert graph.graph["coordinate_crs"] == "EPSG:4326"
    assert graph.graph["max_anchor_distance_m"] == DEFAULT_MAX_ANCHOR_DISTANCE_M
    assert graph.nodes["bus::ROD_SUB"]["region"] == "rodrigues"
    assert graph.nodes["bus::ROD_SUB"]["provisional_root"] is True
    assert road_edge["region"] == "rodrigues"
    assert road_edge["length_km"] == pytest.approx(geodesic_length_km(roads.geometry.iloc[0]))


def test_power_asset_can_join_supported_roads_to_provided_backbone():
    assets = gpd.GeoDataFrame(
        {
            "asset_id": ["SUB"],
            "asset_kind": ["substation"],
            "geometry": [Point(57.5, -20.2)],
        },
        crs="EPSG:4326",
    )
    roads = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(57.5001, -20.2), (57.501, -20.2)]),
            ]
        },
        crs="EPSG:4326",
    )
    backbone = gpd.GeoDataFrame(
        {
            "geometry": [
                LineString([(57.5, -20.2), (57.5, -20.19)]),
            ]
        },
        crs="EPSG:4326",
    )

    graph = build_inferred_distribution_graph(
        assets,
        osm_distribution_lines=roads,
        provided_backbone_lines=backbone,
        max_anchor_distance_m=100,
        anchor_to_each_line_source=True,
    )

    assert graph.degree["bus::SUB"] == 2
