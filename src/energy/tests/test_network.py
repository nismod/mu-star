import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.network import (
    attach_demand,
    build_operational_network,
    build_topology_network,
)


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
    demand = pd.DataFrame(
        {"demand_mw": [40.0, 60.0]},
        index=pd.date_range("2025-01-01", periods=2, freq="h"),
    )
    service_weights = pd.DataFrame({"bus_id": ["A", "B"], "service_weight": [0.25, 0.75]})
    return buses, lines, generators, demand, service_weights


def test_build_topology_network_has_no_demand_components():
    buses, lines, generators, _demand, _service_weights = _network_inputs()

    network = build_topology_network(buses, lines, generators)

    assert len(network.buses) == 2
    assert len(network.lines) == 1
    assert len(network.generators) == 1
    assert network.loads.empty
    assert "load_shedding" not in network.generators.carrier.unique()


def test_build_topology_network_preserves_review_provenance():
    buses, lines, generators, _demand, _service_weights = _network_inputs()
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
    buses, lines, generators, _demand, _service_weights = _network_inputs()
    generators = generators.rename(columns={"output_capacity_mw": "capacity_mw"})

    with pytest.raises(ValueError, match="renamed to output_capacity_mw"):
        build_topology_network(buses, lines, generators)


def test_attach_demand_adds_snapshots_loads_and_load_shedding_on_copy():
    buses, lines, generators, demand, service_weights = _network_inputs()
    topology = build_topology_network(buses, lines, generators)

    network = attach_demand(topology, demand, service_weights)

    assert topology.loads.empty
    assert network.snapshots.equals(demand.index)
    assert network.loads.index.tolist() == ["load::A", "load::B"]
    assert network.loads_t.p_set["load::A"].tolist() == [10.0, 15.0]
    assert network.loads_t.p_set["load::B"].tolist() == [30.0, 45.0]
    assert set(network.generators.query("carrier == 'load_shedding'").index) == {
        "load_shedding::A",
        "load_shedding::B",
    }


def test_attach_demand_omits_zero_weight_junction_buses():
    buses, lines, generators, demand, _service_weights = _network_inputs()
    junction = gpd.GeoDataFrame(
        {"bus_id": ["J"], "geometry": [Point(57.55, -20.2)]},
        crs="EPSG:4326",
    )
    buses = pd.concat([buses, junction], ignore_index=True)
    service_weights = pd.DataFrame({"bus_id": ["A", "B"], "service_weight": [0.25, 0.75]})
    topology = build_topology_network(buses, lines, generators)

    network = attach_demand(topology, demand, service_weights)

    assert "load::J" not in network.loads.index
    assert "load_shedding::J" not in network.generators.index


def test_operational_wrapper_matches_topology_plus_attach_demand():
    buses, lines, generators, demand, service_weights = _network_inputs()

    wrapper = build_operational_network(
        buses,
        lines,
        generators,
        demand,
        service_weights,
    )
    split = attach_demand(
        build_topology_network(buses, lines, generators),
        demand,
        service_weights,
    )

    assert wrapper.loads_t.p_set.equals(split.loads_t.p_set)
    assert wrapper.generators[["bus", "carrier", "p_nom"]].equals(split.generators[["bus", "carrier", "p_nom"]])
