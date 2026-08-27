from pathlib import Path

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import LineString, Point

from energy.base_topology import CEB_SUBSTATION_NAMES, derive_base_topology


def test_base_topology_connects_short_route_gaps_and_keeps_substations():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["A", "B"],
            "name": ["A", "B"],
            "geometry": [Point(100, 0), Point(1_900, 0)],
        },
        crs="EPSG:32740",
    )
    routes = gpd.GeoDataFrame(
        {
            "route_id": ["R1", "R2"],
            "v_nom_kv": [66.0, None],
            "geometry": [
                LineString([(0, 0), (1_000, 0)]),
                LineString([(1_050, 0), (2_000, 0)]),
            ],
        },
        crs="EPSG:32740",
    )

    result = derive_base_topology(
        transmission_routes=routes,
        snapped_substations=substations,
    )

    assert result.substation_count == 2
    assert result.route_gap_count == 1
    assert result.route_gap_length_km == pytest.approx(0.05)
    assert result.connected_components == 1
    assert set(result.buses.query("kind == 'substation'")["bus_id"]) == {"A", "B"}
    assert result.lines["length_km"].sum() == pytest.approx(1.8)
    assert result.lines["s_nom_mva"].eq(10_000).all()
    assert result.lines["source"].eq("derived_route_gap").any()


def test_base_topology_uses_station_as_one_three_way_junction():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["CENTER", "WEST", "EAST", "NORTH"],
            "name": ["CENTER", "WEST", "EAST", "NORTH"],
            "geometry": [
                Point(0, 0),
                Point(-900, 0),
                Point(900, 80),
                Point(80, 900),
            ],
        },
        crs="EPSG:32740",
    )
    routes = gpd.GeoDataFrame(
        {
            "route_id": ["WEST", "EAST", "NORTH"],
            "v_nom_kv": [66.0, 66.0, 66.0],
            "geometry": [
                LineString([(-1_000, 0), (0, 0)]),
                LineString([(80, 0), (1_000, 80)]),
                LineString([(0, 80), (80, 1_000)]),
            ],
        },
        crs="EPSG:32740",
    )

    result = derive_base_topology(substations, routes, route_gap_tolerance_m=75)

    center_lines = result.lines[result.lines["bus0"].eq("CENTER") | result.lines["bus1"].eq("CENTER")]
    assert len(center_lines) == 3
    assert result.route_gap_count == 2
    assert result.cycle_rank == 0
    assert result.meaningful_cycle_count == 0


def test_base_topology_preserves_distinct_parallel_source_routes():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["A", "B"],
            "name": ["A", "B"],
            "geometry": [Point(0, 0), Point(2_000, 0)],
        },
        crs="EPSG:32740",
    )
    routes = gpd.GeoDataFrame(
        {
            "route_id": ["UPPER", "LOWER"],
            "v_nom_kv": [66.0, 66.0],
            "geometry": [
                LineString([(0, 0), (1_000, 1_500), (2_000, 0)]),
                LineString([(0, 0), (1_000, -1_500), (2_000, 0)]),
            ],
        },
        crs="EPSG:32740",
    )

    result = derive_base_topology(substations, routes)

    assert len(result.lines) == 2
    assert set(result.lines["source_route_id"]) == {"UPPER", "LOWER"}
    assert result.parallel_edge_count == 1
    assert result.cycle_rank == 1
    assert result.meaningful_cycle_count == 1
    assert result.retained_source_length_km == pytest.approx(result.source_route_length_km)


def test_base_topology_applies_ceb_names_without_overwriting_reviewed_names():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["SUB_001", "SUB_004"],
            "name": ["SUB_001", "Reviewed Amaury"],
            "geometry": [Point(100, 0), Point(900, 0)],
        },
        crs="EPSG:32740",
    )
    routes = gpd.GeoDataFrame(
        {
            "route_id": ["R1"],
            "v_nom_kv": [66.0],
            "geometry": [LineString([(0, 0), (1_000, 0)])],
        },
        crs="EPSG:32740",
    )

    result = derive_base_topology(substations, routes)
    names = result.buses.set_index("bus_id")["name"]

    assert names["SUB_001"] == CEB_SUBSTATION_NAMES["SUB_001"]
    assert names["SUB_004"] == "Reviewed Amaury"
    assert result.buses["v_nom_kv"].eq(66).all()


def test_ceb_base_regression_restores_reviewed_loops_and_amaury_junction():
    root = Path(__file__).parents[1]
    substation_path = root / "data/1-processed/energy/provided/snapped_substations.parquet"
    route_path = root / "data/1-processed/energy/provided/transmission_routes.parquet"
    if not substation_path.exists() or not route_path.exists():
        pytest.skip("CEB prepared source data is not available")

    result = derive_base_topology(
        gpd.read_parquet(substation_path),
        gpd.read_parquet(route_path),
        route_gap_tolerance_m=75,
    )
    graph = nx.MultiGraph(result.lines[["bus0", "bus1"]].itertuples(index=False, name=None))
    retained_parts = result.lines.groupby("source_route_part_id", dropna=True)["length_km"].sum()

    assert result.connected_components == 1
    assert len(graph["SUB_004"]) >= 3
    assert len(graph["SUB_010"]) >= 2
    assert len(graph["SUB_011"]) >= 2
    assert retained_parts["ROUTE_001_PART_008"] == pytest.approx(9.1000645)
    assert retained_parts["ROUTE_001_PART_012"] == pytest.approx(12.6027510)
    assert result.meaningful_cycle_count == 6
    assert result.cycle_rank == 7
    assert result.parallel_edge_count == 1
    assert result.retained_source_length_km == pytest.approx(303.9765146)
    assert result.buses["v_nom_kv"].eq(66).all()
    names = result.buses.set_index("bus_id")["name"]
    assert names["SUB_004"] == "Amaury"
    assert names["SUB_015"] == "Union Vale"
