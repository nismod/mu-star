import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.network import build_topology_network


def _network_inputs():
    buses = gpd.GeoDataFrame(
        {
            "bus_id": ["A", "B"],
            "geometry": [Point(57.5, -20.2), Point(57.6, -20.2)],
        },
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {
            "line_id": ["AB"],
            "bus0": ["A"],
            "bus1": ["B"],
            "v_nom_kv": [66],
            "length_km": [10.0],
            "s_nom_mva": [100.0],
            "geometry": [LineString([(57.5, -20.2), (57.6, -20.2)])],
        },
        crs="EPSG:4326",
    )
    generators = pd.DataFrame(
        {
            "generator_id": ["plant"],
            "bus_id": ["A"],
            "carrier": ["thermal"],
            "output_capacity_mw": [100.0],
            "capacity_basis": ["electrical_output"],
            "marginal_cost": [20.0],
        }
    )
    return buses, lines, generators


def test_build_topology_network_has_no_demand_components():
    buses, lines, generators = _network_inputs()

    network = build_topology_network(buses, lines, generators)

    assert len(network.buses) == 2
    assert len(network.lines) == 1
    assert len(network.generators) == 1
    assert network.loads.empty
    assert "load_shedding" not in network.generators.carrier.unique()


def test_build_topology_network_preserves_review_provenance():
    buses, lines, generators = _network_inputs()
    buses["name"] = ["Alpha", "Beta"]
    buses["kind"] = "substation"
    lines["source_route_part_id"] = "ROUTE_001_PART_001"
    lines["circuit_id"] = "ROUTE_001_PART_001"

    network = build_topology_network(buses, lines, generators)

    assert network.buses.loc["A", "name"] == "Alpha"
    assert network.buses["kind"].eq("substation").all()
    assert network.lines.loc["AB", "source_route_part_id"] == "ROUTE_001_PART_001"
    assert network.lines.loc["AB", "circuit_id"] == "ROUTE_001_PART_001"


def test_build_topology_network_explains_legacy_capacity_column():
    buses, lines, generators = _network_inputs()
    generators = generators.rename(columns={"output_capacity_mw": "capacity_mw"})

    with pytest.raises(ValueError, match="renamed to output_capacity_mw"):
        build_topology_network(buses, lines, generators)
