import json
import math

import pandas as pd
import pypsa
import pytest

from energy.criticality import (
    build_criticality,
    demand_by_supply_point,
    failure_assets,
    power_flow_failures,
    read_line_circuits,
    transmission_lines,
    transmission_model,
    unmet_demand,
)

ISLANDS = pd.Series(
    {
        "bus::SUB_A": "mauritius",
        "bus::SUB_B": "mauritius",
        "bus::SUB_C": "mauritius",
        "asset::r1": "rodrigues",
        "asset::r2": "rodrigues",
    }
)


def _network():
    """Substations A - B - C on a 66 kV line, a thermal plant at A, a solar farm at C; two plants on Rodrigues."""
    network = pypsa.Network()
    buses = pd.DataFrame(
        {
            "x": [57.0, 57.1, 57.2, 57.0, 57.1, 57.2, 63.40, 63.45],
            "y": [-20.0] * 6 + [-19.7, -19.7],
            "v_nom": [11.0] * 3 + [66.0] * 3 + [11.0] * 2,
            "region": ["mauritius"] * 6 + ["rodrigues"] * 2,
            "kind": ["substation"] * 3 + ["transformer"] * 3 + ["generator"] * 2,
            "asset_id": ["SUB_A", "SUB_B", "SUB_C", "", "", "", "r1", "r2"],
            "name": ["Alpha", "", "", "", "", "", "", ""],
        },
        index=["bus::SUB_A", "bus::SUB_B", "bus::SUB_C", "hv::A", "hv::B", "hv::C", "asset::r1", "asset::r2"],
    )
    network.import_components_from_dataframe(buses, "Bus")
    lines = pd.DataFrame(
        {
            "bus0": ["hv::A", "hv::B", "bus::SUB_A"],
            "bus1": ["hv::B", "hv::C", "bus::SUB_B"],
            "length": [10.0, 10.0, 12.0],
            "x": [1.0, 1.0, 1.0],
            "s_nom": [50.0, 50.0, 5.0],
            "source": ["provided_transmission", "provided_transmission", "osm"],
        },
        index=["ceb_ab", "ceb_bc", "road_ab"],
    )
    network.import_components_from_dataframe(lines, "Line")
    generators = pd.DataFrame(
        {
            "bus": ["bus::SUB_A", "bus::SUB_C", "asset::r1", "asset::r2"],
            "p_nom": [100.0, 10.0, 6.0, 6.0],
            "carrier": ["thermal", "solar", "thermal", "thermal"],
        },
        index=["thermal_a", "solar_c", "r1", "r2"],
    )
    network.import_components_from_dataframe(generators, "Generator")
    return network


def _circuits():
    return pd.DataFrame(
        {"route": ["R1", "R2"], "ends": ["A - B", "B - C"], "built_for_kv": [66, 66], "circuits": [1, 1]},
        index=pd.Index(["ceb_ab", "ceb_bc"], name="line_id"),
    )


def _demand(c=30.0):
    return pd.Series({"bus::SUB_A": 10.0, "bus::SUB_B": 20.0, "bus::SUB_C": c, "asset::r1": 4.0, "asset::r2": 4.0})


def test_model_rates_lines_by_circuits_and_puts_demand_on_the_transmission_buses():
    model = transmission_model(_network(), ISLANDS, line_circuits=_circuits())

    assert list(model.lines.index) == ["ceb_ab", "ceb_bc"]  # the 11 kV road line is a distribution line
    assert model.lines.at["ceb_ab", "s_nom"] == pytest.approx(math.sqrt(3) * 66 * 0.47)  # 149-AL1/24-ST1A
    assert model.lines.at["ceb_ab", "x"] == pytest.approx(0.41 * 10)
    assert model.supply_points["bus"].to_dict() == {
        "bus::SUB_A": "hv::A",
        "bus::SUB_B": "hv::B",
        "bus::SUB_C": "hv::C",
        "asset::r1": "island::rodrigues",
        "asset::r2": "island::rodrigues",
    }
    assert model.plants["bus"].to_dict() == {
        "thermal_a": "hv::A",
        "solar_c": "hv::C",
        "r1": "island::rodrigues",
        "r2": "island::rodrigues",
    }
    two_circuits = _circuits().assign(circuits=[2, 1])
    doubled = transmission_model(_network(), ISLANDS, line_circuits=two_circuits)
    assert doubled.lines.at["ceb_ab", "s_nom"] == pytest.approx(2 * model.lines.at["ceb_ab", "s_nom"])
    assert doubled.lines.at["ceb_ab", "x"] == pytest.approx(model.lines.at["ceb_ab", "x"] / 2)


def test_model_needs_every_transmission_line_in_the_circuits_table():
    with pytest.raises(ValueError, match="missing \\['ceb_bc'\\]"):
        transmission_model(_network(), ISLANDS, line_circuits=_circuits().loc[["ceb_ab"]])


def test_model_takes_only_the_lines_above_the_radial_voltage():
    network = _network()
    network.buses.loc[["hv::A", "hv::B", "hv::C"], "v_nom"] = 33.0
    line_types = {33: "149-AL1/24-ST1A 110.0"}

    # At the default of 35 kV, 33 kV lines are distribution lines, so Mauritius is one bus.
    radial = transmission_model(network, ISLANDS, line_types=line_types)
    assert radial.lines.empty
    assert set(radial.supply_points["bus"]) == {"island::mauritius", "island::rodrigues"}
    meshed = transmission_model(network, ISLANDS, line_types=line_types, max_radial_line_voltage_kv=22.0)
    assert list(meshed.lines.index) == ["ceb_ab", "ceb_bc"]


def test_model_without_a_circuits_table_joins_segments_between_substations_at_the_network_voltage():
    network = _network()
    network.buses.loc[["hv::A", "hv::B", "hv::C"], "v_nom"] = 45.0
    # B - C in two segments, joined at a bend where nothing else meets.
    bend = pd.DataFrame({"x": [57.15], "y": [-20.0], "v_nom": [45.0], "region": ["mauritius"]}, index=["hv::bend"])
    network.import_components_from_dataframe(bend, "Bus")
    network.lines.loc["ceb_bc", "bus1"] = "hv::bend"
    second = pd.DataFrame(
        {"bus0": ["hv::bend"], "bus1": ["hv::C"], "length": [5.0], "x": [1.0], "source": ["provided_transmission"]},
        index=["ceb_bc_2"],
    )
    network.import_components_from_dataframe(second, "Line")

    model = transmission_model(network, ISLANDS, line_types={45: "149-AL1/24-ST1A 110.0"})

    assert model.lines["route"].to_dict() == {"ceb_ab": "ceb_ab", "ceb_bc": "ceb_bc", "ceb_bc_2": "ceb_bc"}
    assert model.lines["circuits"].tolist() == [1, 1, 1]
    assert model.lines.at["ceb_ab", "s_nom"] == pytest.approx(math.sqrt(3) * 45 * 0.47)
    assets = failure_assets(model, network).set_index("asset_id")
    assert assets.at["ceb_bc", "lines"] == ("ceb_bc", "ceb_bc_2")
    assert assets.at["SUB_B", "name"] == "SUB_B"
    assert unmet_demand(model, _demand(), lines=assets.at["ceb_bc", "lines"])["bus::SUB_C"] == pytest.approx(30.0)
    with pytest.raises(ValueError, match="no line type for lines built for \\[45\\] kV"):
        transmission_model(network, ISLANDS)


def test_demand_cut_off_from_thermal_and_hydro_plants_is_unmet():
    model = transmission_model(_network(), ISLANDS, line_circuits=_circuits())
    demand = _demand()

    assert unmet_demand(model, demand).sum() == pytest.approx(0.0)
    # C keeps only its solar farm, which cannot run without the grid.
    assert unmet_demand(model, demand, lines=("ceb_bc",)).to_dict() == pytest.approx(
        {"bus::SUB_A": 0.0, "bus::SUB_B": 0.0, "bus::SUB_C": 30.0, "asset::r1": 0.0, "asset::r2": 0.0}
    )
    with_solar = unmet_demand(model, demand, lines=("ceb_bc",), island_carriers=("thermal", "solar"))
    assert with_solar["bus::SUB_C"] == pytest.approx(20.0)
    # Losing B also cuts off C.
    assert unmet_demand(model, demand, bus="hv::B").sum() == pytest.approx(50.0)
    # Losing the only thermal plant leaves Mauritius with the solar farm alone.
    assert unmet_demand(model, demand, plants=("thermal_a",)).sum() == pytest.approx(60.0)
    # Rodrigues has 6 MW left for 8 MW, shared between its two areas.
    rodrigues = unmet_demand(model, demand, plants=("r1",))
    assert rodrigues[["asset::r1", "asset::r2"]].tolist() == pytest.approx([1.0, 1.0])


def test_criticality_separates_unmet_demand_due_to_line_ratings():
    network = _network()
    model = transmission_model(network, ISLANDS, line_circuits=_circuits())
    network.buses.loc["bus::SUB_B", "name"] = "Bravo"
    assets = failure_assets(model, network, _circuits())

    assert assets.groupby("asset_type").size().to_dict() == {"line": 2, "plant": 4, "substation": 3}
    assert assets.set_index("asset_id").at["SUB_B", "name"] == "Bravo"
    assert assets.set_index("asset_id").at["R2", "lines"] == ("ceb_bc",)

    # Without the solar farm, A - B must carry 60 MW at peak: more than its 53.7 MVA. At average demand
    # (C at 30 MW) it carries 50 MW, within its rating, so the per-day value misses this shortfall.
    checked = assets[assets["asset_id"].isin(["SUB_B", "R2", "solar_c"])]  # fail only the components checked
    summary, by_area, intact = power_flow_failures(model, checked, _demand(c=40.0), _demand())

    row = summary.set_index("asset_id").loc["solar_c"]
    assert row["unmet_peak_mw"] == pytest.approx(60 - math.sqrt(3) * 66 * 0.47, abs=1e-3)
    assert row["line_limits_peak_mw"] == pytest.approx(row["unmet_peak_mw"], abs=1e-3)
    assert row["unmet_mwh_per_day"] == pytest.approx(0.0, abs=1e-6)
    # Losing B - C cuts off C all day: 30 MW at average demand, 40 MW at peak.
    r2 = summary.set_index("asset_id").loc["R2", ["unmet_mwh_per_day", "unmet_peak_mw", "line_limits_peak_mw"]]
    assert r2.tolist() == pytest.approx([24 * 30.0, 40.0, 0.0])
    assert intact["peak"].sum() == pytest.approx(0.0)
    assert intact["average"].sum() == pytest.approx(0.0)
    assert set(by_area.loc[by_area["asset_id"].eq("SUB_B"), "area_bus_id"]) == {"bus::SUB_B", "bus::SUB_C"}
    assert list(summary["asset_type"].drop_duplicates()) == ["substation", "line", "plant"]


def test_unmet_demand_of_a_failure_is_the_rise_in_the_total():
    network = _network()
    network.generators.loc["thermal_a", "p_nom"] = 45.0  # with the 10 MW solar farm, 5 MW short of 60 MW
    model = transmission_model(network, ISLANDS, line_circuits=_circuits())
    assets = failure_assets(model, network, _circuits())

    summary, _, intact = power_flow_failures(model, assets[assets["asset_id"].eq("R2")], _demand(), _demand())

    assert intact["peak"].sum() == pytest.approx(5.0)
    # Losing B - C cuts off C (30 MW; its solar farm cannot run alone) and leaves A and B 45 MW for 30 MW, so the
    # total rises by 25 MW, wherever the power flow shed the 5 MW with nothing failed.
    assert summary.set_index("asset_id").at["R2", "unmet_peak_mw"] == pytest.approx(25.0)


def _inputs(tmp_path):
    """Write the network, circuits, weights and demand levels; return them as build_criticality arguments.

    Each supply point is a node of its own service area, so the 11 kV road line between A and B joins two areas.
    """
    _network().export_to_netcdf(tmp_path / "network.nc")
    _circuits().reset_index().to_csv(tmp_path / "circuits.csv", index=False)
    shares = [10 / 60, 20 / 60, 30 / 60, 0.5, 0.5]
    weights = pd.DataFrame({"bus_id": ISLANDS.index, "island": ISLANDS.to_numpy(), "service_weight": shares})
    weights.to_csv(tmp_path / "weights.csv", index=False)
    weights.assign(area_bus_id=ISLANDS.index).to_csv(tmp_path / "nodes.csv", index=False)
    levels = pd.DataFrame(
        {
            "level": ["peak", "peak", "average", "average"],
            "island": ["mauritius", "rodrigues", "mauritius", "rodrigues"],
            "demand_mw": [60.0, 8.0, 40.0, 5.0],
        }
    )
    levels.to_csv(tmp_path / "levels.csv", index=False)
    assert demand_by_supply_point(weights, levels, "peak").to_dict() == pytest.approx(_demand().to_dict())
    return {
        "network_path": tmp_path / "network.nc",
        "substation_weights_path": tmp_path / "weights.csv",
        "node_weights_path": tmp_path / "nodes.csv",
        "demand_levels_path": tmp_path / "levels.csv",
    }


def test_build_criticality_writes_one_table_by_power_flow_and_connectivity(tmp_path):
    inputs = _inputs(tmp_path)

    outputs = build_criticality(
        **inputs,
        line_circuits_path=tmp_path / "circuits.csv",
        output_dir=tmp_path / "criticality",
    )

    summary = pd.read_csv(outputs.criticality).set_index("asset_id")
    assert list(summary.columns) == [
        "asset_type",
        "name",
        "island",
        "lines",
        "unmet_mwh_per_day",
        "unmet_peak_mw",
        "line_limits_peak_mw",
        "in_service",
        "method",
    ]
    assert summary.groupby(["asset_type", "method"]).size().to_dict() == {
        ("distribution_line", "connectivity"): 1,
        ("line", "power_flow"): 2,
        ("plant", "power_flow"): 4,
        ("substation", "power_flow"): 3,
    }
    assert summary.at["SUB_A", "name"] == "Alpha"
    # Losing B - C cuts off C: 30 MW at peak and 20 MW at average demand.
    assert summary.at["R2", "unmet_peak_mw"] == pytest.approx(30.0)
    assert summary.at["R2", "unmet_mwh_per_day"] == pytest.approx(24 * 20.0)
    # The road line between two areas is open, so it cuts off nothing; the power flow does not rate it.
    road = summary.loc["road_ab"]
    assert (road["unmet_peak_mw"], road["in_service"]) == (0.0, False)
    assert math.isnan(road["line_limits_peak_mw"])
    assert summary.loc[summary["method"].eq("power_flow"), "in_service"].all()
    by_area = pd.read_csv(outputs.by_area)
    assert list(by_area.columns) == ["asset_id", "area_bus_id", "area_name", "unmet_mwh_per_day", "unmet_peak_mw"]
    # Areas take the name the network gives their substation, or the substation's id.
    assert by_area.set_index(["asset_id", "area_bus_id"]).loc[("SUB_A", "bus::SUB_A"), "area_name"] == "Alpha"
    assert set(by_area.loc[by_area["asset_id"].eq("R2"), "area_name"]) == {"SUB_C"}
    metadata = json.loads(outputs.metadata.read_text())
    assert metadata["unmet_with_nothing_failed"] == {"unmet_mwh_per_day": 0.0, "unmet_peak_mw": 0.0}
    assert metadata["max_radial_line_voltage_kv"] == 35.0
    assert metadata["power_flow"] == {"buses": 4, "lines": 2, "failures": 9}
    assert metadata["warnings"] == []
    assert len(read_line_circuits(tmp_path / "circuits.csv")) == 2
    ratings = pd.read_csv(outputs.line_ratings)
    assert list(ratings.columns) == ["line_id", "asset_id", "built_for_kv", "circuits", "voltage_kv", "rating_mva"]
    assert ratings["asset_id"].tolist() == ["R1", "R2"]


def test_transmission_lines_stop_when_the_setting_takes_in_distribution_lines():
    network = _network()

    assert list(transmission_lines(network, 35.0).index) == ["ceb_ab", "ceb_bc"]
    assert transmission_lines(network, 70.0).empty  # a warning in build_criticality
    # Below 11 kV the road line would enter the power flow.
    with pytest.raises(ValueError, match="puts 1 of the estimated distribution network's lines into the power flow"):
        transmission_lines(network, 10.0)
    # So would lines at two voltages, which only transformers join.
    network.lines.loc["road_ab", "source"] = "provided_transmission"
    with pytest.raises(ValueError, match="lines at 11 and 66 kV are above"):
        transmission_lines(network, 10.0)
