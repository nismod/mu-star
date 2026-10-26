import json
import math

import pandas as pd
import pypsa
import pytest

from energy.criticality import (
    build_criticality,
    criticality,
    demand_by_supply_point,
    failure_assets,
    read_line_circuits,
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


def test_model_rates_ceb_lines_by_circuits_and_puts_demand_on_the_66kv_buses():
    model = transmission_model(_network(), _circuits(), ISLANDS)

    assert list(model.lines.index) == ["ceb_ab", "ceb_bc"]  # the road line is not part of the model
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
    doubled = transmission_model(_network(), two_circuits, ISLANDS)
    assert doubled.lines.at["ceb_ab", "s_nom"] == pytest.approx(2 * model.lines.at["ceb_ab", "s_nom"])
    assert doubled.lines.at["ceb_ab", "x"] == pytest.approx(model.lines.at["ceb_ab", "x"] / 2)


def test_model_needs_every_ceb_line_in_the_circuits_table():
    with pytest.raises(ValueError, match="missing \\['ceb_bc'\\]"):
        transmission_model(_network(), _circuits().loc[["ceb_ab"]], ISLANDS)


def test_demand_cut_off_from_thermal_and_hydro_plants_is_unmet():
    model = transmission_model(_network(), _circuits(), ISLANDS)
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
    model = transmission_model(network, _circuits(), ISLANDS)
    assets = failure_assets(model, network, _circuits(), pd.Series({"SUB_B": "Bravo"}))

    assert assets.groupby("asset_type").size().to_dict() == {"line": 2, "plant": 4, "substation": 3}
    assert assets.set_index("asset_id").at["SUB_B", "name"] == "Bravo"
    assert assets.set_index("asset_id").at["R2", "lines"] == ("ceb_bc",)

    # Without the solar farm, A - B must carry 60 MW at peak: more than its 53.7 MVA. At average demand
    # (C at 30 MW) it carries 50 MW, within its rating, so the per-day value misses this shortfall.
    summary, by_area, intact = criticality(model, assets, _demand(c=40.0), _demand())

    row = summary.set_index("asset_id").loc["solar_c"]
    assert row["unmet_peak_mw"] == pytest.approx(60 - math.sqrt(3) * 66 * 0.47, abs=1e-3)
    assert row["line_limits_peak_mw"] == pytest.approx(row["unmet_peak_mw"], abs=1e-3)
    assert row["unmet_mwh_per_day"] == pytest.approx(0.0, abs=1e-6)
    # Losing B - C cuts off C all day: 30 MW at average demand, 40 MW at peak.
    r2 = summary.set_index("asset_id").loc["R2", ["unmet_mwh_per_day", "unmet_peak_mw", "line_limits_peak_mw"]]
    assert r2.tolist() == pytest.approx([24 * 30.0, 40.0, 0.0])
    assert intact == {"unmet_mwh_per_day": pytest.approx(0.0), "unmet_peak_mw": pytest.approx(0.0)}
    assert set(by_area.loc[by_area["asset_id"].eq("SUB_B"), "area_bus_id"]) == {"bus::SUB_B", "bus::SUB_C"}
    assert list(summary["asset_type"].drop_duplicates()) == ["substation", "line", "plant"]


def test_build_criticality_writes_the_tables(tmp_path):
    _network().export_to_netcdf(tmp_path / "network.nc")
    _circuits().reset_index().to_csv(tmp_path / "circuits.csv", index=False)
    pd.DataFrame({"substation": ["SUB_A"], "name": ["Alpha"]}).to_csv(tmp_path / "names.csv", index=False)
    weights = pd.DataFrame(
        {
            "bus_id": ISLANDS.index,
            "island": ISLANDS.to_numpy(),
            "service_weight": [10 / 60, 20 / 60, 30 / 60, 0.5, 0.5],
        }
    )
    weights.to_csv(tmp_path / "weights.csv", index=False)
    levels = pd.DataFrame(
        {
            "level": ["peak", "peak", "average", "average"],
            "island": ["mauritius", "rodrigues", "mauritius", "rodrigues"],
            "demand_mw": [60.0, 8.0, 40.0, 5.0],
        }
    )
    levels.to_csv(tmp_path / "levels.csv", index=False)
    assert demand_by_supply_point(weights, levels, "peak").to_dict() == pytest.approx(_demand().to_dict())

    outputs = build_criticality(
        network_path=tmp_path / "network.nc",
        line_circuits_path=tmp_path / "circuits.csv",
        substation_names_path=tmp_path / "names.csv",
        substation_weights_path=tmp_path / "weights.csv",
        demand_levels_path=tmp_path / "levels.csv",
        output_dir=tmp_path / "criticality",
    )

    summary = pd.read_csv(outputs.criticality).set_index("asset_id")
    assert summary.at["SUB_A", "name"] == "Alpha"
    assert summary.at["R2", "unmet_peak_mw"] == pytest.approx(30.0)
    assert summary.at["R2", "unmet_mwh_per_day"] == pytest.approx(24 * 20.0)
    by_area = pd.read_csv(outputs.by_area)
    assert list(by_area.columns) == ["asset_id", "area_bus_id", "unmet_mwh_per_day", "unmet_peak_mw"]
    assert json.loads(outputs.metadata.read_text())["unmet_with_nothing_failed"] == {
        "unmet_mwh_per_day": 0.0,
        "unmet_peak_mw": 0.0,
    }
    assert len(read_line_circuits(tmp_path / "circuits.csv")) == 2
