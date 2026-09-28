import geopandas as gpd
import networkx as nx
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


def _substation(x=57.5, y=-20.2, bus_id="SUB_001"):
    return gpd.GeoDataFrame({"bus_id": [bus_id], "geometry": [Point(x, y)]}, crs="EPSG:4326")


def _lines(*coords, **columns):
    return gpd.GeoDataFrame({**columns, "geometry": [LineString(c) for c in coords]}, crs="EPSG:4326")


def test_inferred_distribution_graph_is_anchored_and_labelled(tmp_path):
    precomputed = _lines([(57.5001, -20.2), (57.501, -20.2)])

    graph = build_inferred_distribution_graph(_substation(), precomputed_lines=precomputed, max_anchor_distance_m=100)
    outputs = write_inferred_distribution_tables(graph, tmp_path)

    assert isinstance(graph, nx.MultiGraph)
    assert graph.graph["inferred"] is True
    assert graph.graph["stage"] == "connectivity_only"
    assert graph.nodes["bus::SUB_001"]["anchor_status"] == "anchored"
    assert graph.nodes["bus::SUB_001"]["anchor_distance_m"] < 15
    assert outputs.nodes.is_file()
    assert outputs.edges.is_file()
    assert outputs.metadata.is_file()


def test_asset_is_connected_to_the_nearest_point_on_a_road_by_splitting_it():
    # A 1 km east-west road; the substation sits 50 m north of its middle.
    road = _lines([(57.5, -20.2), (57.5096, -20.2)], region=["mauritius"])
    substation = _substation(x=57.5048, y=-20.19955)

    graph = build_inferred_distribution_graph(substation, osm_distribution_lines=road, max_anchor_distance_m=100)

    road_edges = [(u, v, k, a) for u, v, k, a in graph.edges(keys=True, data=True) if a["source"] == "osm"]
    anchors = [a for _, _, a in graph.edges(data=True) if a["source"] == "substation_anchor"]
    assert len(road_edges) == 2, "the road is split at the connection point"
    assert {a["edge_id"] for *_, a in road_edges} == {"osm_000001::1", "osm_000001::2"}
    assert sum(a["length_km"] for *_, a in road_edges) == pytest.approx(geodesic_length_km(road.geometry[0]), rel=1e-3)
    assert len(anchors) == 1
    assert graph.nodes["bus::SUB_001"]["anchor_status"] == "anchored"
    assert 40 < graph.nodes["bus::SUB_001"]["anchor_distance_m"] < 60
    assert nx.number_connected_components(graph) == 1


def test_parallel_roads_between_the_same_junctions_are_both_kept():
    straight = [(57.5, -20.2), (57.51, -20.2)]
    bent = [(57.5, -20.2), (57.505, -20.201), (57.51, -20.2)]
    roads = _lines(straight, bent, region=["mauritius", "mauritius"])

    graph = build_inferred_distribution_graph(_substation(x=57.52, y=-20.21), osm_distribution_lines=roads)

    assert graph.number_of_edges() == 2
    assert graph.number_of_nodes() == 3  # two junctions plus the (unanchored) substation


def test_asset_beyond_the_anchor_distance_stays_unanchored_with_its_road_distance():
    road = _lines([(57.5, -20.2), (57.51, -20.2)])
    far = _substation(x=57.505, y=-20.15)  # about 5.5 km south of the road

    graph = build_inferred_distribution_graph(far, osm_distribution_lines=road, max_anchor_distance_m=1000)

    assert graph.nodes["bus::SUB_001"]["anchor_status"] == "unanchored"
    assert 5_000 < graph.nodes["bus::SUB_001"]["anchor_distance_m"] < 6_000
    assert graph.degree["bus::SUB_001"] == 0


def test_topology_disconnection_counts_only_demand_without_substation_root():
    precomputed = _lines([(57.5001, -20.2), (57.501, -20.2)])
    demand_points = gpd.GeoDataFrame({"demand_mw": [3.0], "geometry": [Point(57.501, -20.2)]}, crs="EPSG:4326")
    graph = build_inferred_distribution_graph(_substation(), precomputed_lines=precomputed, max_anchor_distance_m=100)
    graph = assign_proxy_demand_to_graph(graph, demand_points)

    supplied = topology_disconnection_impacts(graph)
    failed = topology_disconnection_impacts(graph, failed_bus_ids=["SUB_001"])

    assert supplied.empty
    assert failed["unserved_demand_mw"].sum() == 3.0


def test_proxy_demand_requires_distribution_nodes():
    demand_points = gpd.GeoDataFrame({"demand_mw": [3.0], "geometry": [Point(57.501, -20.2)]}, crs="EPSG:4326")
    graph = build_inferred_distribution_graph(_substation())

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
    roads = _lines([(63.4, -19.7), (63.41, -19.7)], region=["rodrigues"])

    graph = build_inferred_distribution_graph(substations, osm_distribution_lines=roads)
    road_edge = next(attrs for _, _, attrs in graph.edges(data=True) if attrs["source"] == "osm")

    assert graph.graph["coordinate_crs"] == "EPSG:4326"
    assert graph.graph["max_anchor_distance_m"] == DEFAULT_MAX_ANCHOR_DISTANCE_M
    assert graph.nodes["bus::ROD_SUB"]["region"] == "rodrigues"
    assert graph.nodes["bus::ROD_SUB"]["provisional_root"] is True
    assert road_edge["region"] == "rodrigues"
    assert road_edge["length_km"] == pytest.approx(geodesic_length_km(roads.geometry.iloc[0]))


def test_power_asset_can_join_supported_roads_to_provided_backbone():
    assets = gpd.GeoDataFrame(
        {"asset_id": ["SUB"], "asset_kind": ["substation"], "geometry": [Point(57.5, -20.2)]},
        crs="EPSG:4326",
    )
    roads = _lines([(57.5001, -20.2), (57.501, -20.2)])
    backbone = _lines([(57.5, -20.2), (57.5, -20.19)])

    graph = build_inferred_distribution_graph(
        assets,
        osm_distribution_lines=roads,
        provided_backbone_lines=backbone,
        max_anchor_distance_m=100,
        anchor_to_each_line_source=True,
    )

    assert graph.degree["bus::SUB"] == 2
    anchor_ids = sorted(a["edge_id"] for _, _, a in graph.edges(data=True) if a["source"] == "substation_anchor")
    assert anchor_ids == ["anchor::SUB::osm", "anchor::SUB::provided_transmission"]
