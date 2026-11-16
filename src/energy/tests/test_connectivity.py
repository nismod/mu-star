import json

import pandas as pd
import pypsa
import pytest

from energy.connectivity import build_connectivity_criticality, distribution_failures, meshed_unmet_demand
from energy.criticality import transmission_model, unmet_demand
from energy.tests.test_criticality import ISLANDS, _circuits, _demand, _network


def test_meshed_connectivity_gives_the_power_flow_results_where_no_rating_binds():
    model = transmission_model(_network(), ISLANDS, line_circuits=_circuits())
    demand = _demand()

    for failure in ({}, {"lines": ("ceb_bc",)}, {"bus": "hv::B"}, {"plants": ("thermal_a",)}, {"plants": ("r1",)}):
        expected = unmet_demand(model, demand, **failure)
        assert meshed_unmet_demand(model, demand, **failure).to_dict() == pytest.approx(expected.to_dict(), abs=1e-6)


def _feeders():
    """Substation S feeds d1 to d4 through a loop d1 - d2 - d3 and a spur d3 - d4; T feeds t1; d4 - t1 joins the
    two areas."""
    network = pypsa.Network()
    names = ["bus::S", "d1", "d2", "d3", "d4", "bus::T", "t1"]
    buses = pd.DataFrame({"v_nom": 11.0, "x": range(7), "y": 0.0}, index=names)
    network.import_components_from_dataframe(buses, "Bus")
    lines = pd.DataFrame(
        [
            ("anchor::S::osm", "bus::S", "d1", 0.01, "substation_anchor"),
            ("l12", "d1", "d2", 1.0, "osm"),
            ("l13", "d1", "d3", 1.0, "osm"),
            ("l23", "d2", "d3", 1.5, "osm"),
            ("l34", "d3", "d4", 1.0, "osm"),
            ("tie", "d4", "t1", 1.0, "osm"),
            ("anchor::T::osm", "bus::T", "t1", 0.01, "substation_anchor"),
        ],
        columns=["name", "bus0", "bus1", "length", "source"],
    ).set_index("name")
    network.import_components_from_dataframe(lines.assign(x=0.1), "Line")
    weights = pd.DataFrame(
        {
            "bus_id": names,
            "service_weight": [0.0, 0.1, 0.1, 0.2, 0.2, 0.0, 0.4],
            "area_bus_id": ["bus::S"] * 5 + ["bus::T"] * 2,
            "island": "mauritius",
        }
    )
    return network, weights


def test_distribution_lines_cut_off_the_nodes_beyond_them_radially_and_only_as_bridges_after_switching():
    network, weights = _feeders()
    demand = {"peak": pd.Series({"mauritius": 100.0}), "average": pd.Series({"mauritius": 50.0})}

    table, not_joined, counts = distribution_failures(network, weights, demand)

    # Radially, l23 is open: l12 feeds d2 (10 MW) and l13 feeds d3 and d4 (40 MW). After switching, only the spur
    # l34 cuts off demand. The tie between the areas is open, and the build's connections fail with their substation.
    assert table["unmet_peak_mw"].to_dict() == pytest.approx(
        {"l12": 10.0, "l13": 40.0, "l23": 0.0, "l34": 20.0, "tie": 0.0}
    )
    assert table["unmet_after_switching_peak_mw"].to_dict() == pytest.approx(
        {"l12": 0.0, "l13": 0.0, "l23": 0.0, "l34": 20.0, "tie": 0.0}
    )
    assert table.at["l13", "unmet_average_mw"] == pytest.approx(20.0)
    assert table.at["tie", "area_bus_id"] == ""
    assert not_joined == {"peak": 0.0, "average": 0.0}
    assert counts["connections_added_by_the_build"] == 2

    # A 30 MW thermal plant at d4 keeps d4 supplied, and supplies 30 of the 40 MW beyond l13.
    network.import_components_from_dataframe(
        pd.DataFrame({"bus": ["d4"], "p_nom": [30.0], "carrier": ["thermal"]}, index=["plant_d4"]), "Generator"
    )
    with_plant, _, _ = distribution_failures(network, weights, demand)
    assert with_plant.loc[["l13", "l34"], "unmet_peak_mw"].tolist() == pytest.approx([10.0, 0.0])


def test_build_connectivity_criticality_writes_the_tables(tmp_path):
    _network().export_to_netcdf(tmp_path / "network.nc")
    _circuits().reset_index().to_csv(tmp_path / "circuits.csv", index=False)
    shares = [10 / 60, 20 / 60, 30 / 60, 0.5, 0.5]
    pd.DataFrame({"bus_id": ISLANDS.index, "island": ISLANDS.to_numpy(), "service_weight": shares}).to_csv(
        tmp_path / "weights.csv", index=False
    )
    pd.DataFrame(
        {"bus_id": ISLANDS.index, "service_weight": shares, "area_bus_id": ISLANDS.index, "island": ISLANDS.to_numpy()}
    ).to_csv(tmp_path / "nodes.csv", index=False)
    pd.DataFrame(
        {
            "level": ["peak", "peak", "average", "average"],
            "island": ["mauritius", "rodrigues", "mauritius", "rodrigues"],
            "demand_mw": [60.0, 8.0, 40.0, 5.0],
        }
    ).to_csv(tmp_path / "levels.csv", index=False)

    outputs = build_connectivity_criticality(
        network_path=tmp_path / "network.nc",
        substation_weights_path=tmp_path / "weights.csv",
        node_weights_path=tmp_path / "nodes.csv",
        demand_levels_path=tmp_path / "levels.csv",
        output_dir=tmp_path / "connectivity",
        line_circuits_path=tmp_path / "circuits.csv",
    )

    summary = pd.read_csv(outputs.criticality).set_index("asset_id")
    assert summary.groupby("asset_type").size().to_dict() == {
        "distribution_line": 1,
        "line": 2,
        "plant": 4,
        "substation": 3,
    }
    # As in the power flow: losing B - C cuts off C, 30 MW at peak and 20 MW at average demand.
    assert summary.loc["R2", ["unmet_peak_mw", "unmet_after_switching_peak_mw"]].tolist() == pytest.approx([30.0, 30.0])
    assert summary.at["R2", "unmet_mwh_per_day"] == pytest.approx(24 * 20.0)
    assert summary.at["road_ab", "unmet_peak_mw"] == 0.0  # a line between two areas, so open
    by_area = pd.read_csv(outputs.by_area)
    assert list(by_area.columns) == [
        "asset_id",
        "area_bus_id",
        "unmet_mwh_per_day",
        "unmet_peak_mw",
        "unmet_after_switching_mwh_per_day",
        "unmet_after_switching_peak_mw",
    ]
    metadata = json.loads(outputs.metadata.read_text())
    assert metadata["unmet_with_nothing_failed"] == {"unmet_mwh_per_day": 0.0, "unmet_peak_mw": 0.0}
    assert metadata["max_radial_line_voltage_kv"] == 35.0
