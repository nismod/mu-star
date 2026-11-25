import json

import pandas as pd
import pytest
from pywr.model import Model
from pywr.recorders import NumpyArrayNodeRecorder

from water import build_simplified_model, run_simplified

DEFAULT_COSTS = [
    {"component": "environmental_mrf", "cost": -100, "priority": 0, "description": "MRF"},
    {"component": "demand_municipal", "cost": -50, "priority": 1, "description": "Municipal"},
    {"component": "demand_industrial", "cost": -30, "priority": 2, "description": "Industrial"},
    {"component": "demand_irrigation", "cost": -15, "priority": 3, "description": "Irrigation"},
    {"component": "reservoir", "cost": -5, "priority": 4, "description": "Reservoir"},
    {"component": "hydropower", "cost": -4, "priority": 5, "description": "Hydropower"},
    {"component": "sea_outlet", "cost": 0, "priority": 6, "description": "Sea"},
    {"component": "interbasin_transfer", "cost": 1, "priority": 7, "description": "Transfer"},
]


def _write_water_inputs(
    base_dir,
    dates,
    basin_params,
    inflows,
    municipal,
    industrial,
    irrigation,
    transfers=None,
):
    data_dir = base_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(DEFAULT_COSTS).to_csv(data_dir / "costs.csv", index=False)
    pd.DataFrame(basin_params).to_csv(data_dir / "basin_parameters.csv", index=False)

    for fname, values in [
        ("inflow_monthly_m3.csv", inflows),
        ("demand_municipal_monthly_m3.csv", municipal),
        ("demand_industrial_monthly_m3.csv", industrial),
        ("demand_irrigation_monthly_m3.csv", irrigation),
    ]:
        df = pd.DataFrame(values, index=dates)
        df.index.name = "date"
        df.to_csv(data_dir / fname)

    if transfers is not None:
        cols = ["from_basin", "to_basin", "link_name", "max_capacity_m3"]
        pd.DataFrame(transfers, columns=cols).to_csv(
            data_dir / "interbasin_transfers.csv", index=False
        )


def test_unequal_month_lengths_and_leap_year_conversion(tmp_path):
    dates = ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"]
    _write_water_inputs(
        tmp_path,
        dates=dates,
        basin_params=[
            {
                "basin": "A",
                "reservoir_capacity_m3": 0.0,
                "dead_storage_m3": 0.0,
                "hydropower_max_m3": 0.0,
                "mrf_target_m3": 0.0,
                "initial_storage_m3": 0.0,
            }
        ],
        inflows={"A": [31e6, 29e6, 31e6, 30e6]},
        municipal={"A": [10e6, 10e6, 10e6, 10e6]},
        industrial={"A": [5e6, 5e6, 5e6, 5e6]},
        irrigation={"A": [5e6, 5e6, 5e6, 5e6]},
        transfers=[],
    )

    basins, schema = build_simplified_model(tmp_path)
    assert basins == ["A"]
    assert all("url" in tbl and "data" not in tbl for tbl in schema["tables"].values())

    cal = pd.read_csv(tmp_path / "data" / "calendar_days_in_month.csv")
    assert cal["days_in_month"].tolist() == [31.0, 29.0, 31.0, 30.0]

    json_path = tmp_path / "Pywr_simplified_model.json"
    json_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    model = Model.load(str(json_path))
    model.check()
    assert model.find_orphaned_parameters() == set()
    inflow_rec = NumpyArrayNodeRecorder(model, model.nodes["catchment_A"])
    model.run()
    assert inflow_rec.data[:, 0].tolist() == pytest.approx([1e6, 1e6, 1e6, 1e6])

    summary = run_simplified(tmp_path).set_index("basin")
    assert summary.loc["A", "inflow_Mm3_yr"] == pytest.approx(363.0)
    assert summary.loc["A", "demands_delivered_Mm3_yr"] == pytest.approx(240.0)
    assert summary.loc["A", "sea_discharge_Mm3_yr"] == pytest.approx(123.0)
    assert summary.loc["A", "hydropower_Mm3_yr"] == pytest.approx(0.0)
    assert summary.loc["A", "mrf_reliability_pct"] == pytest.approx(100.0)
    assert summary.loc["A", "municipal_reliability_pct"] == pytest.approx(100.0)


def test_reservoir_carryover_and_hydropower_spillway(tmp_path):
    dates = ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"]
    _write_water_inputs(
        tmp_path,
        dates=dates,
        basin_params=[
            {
                "basin": "A",
                "reservoir_capacity_m3": 100e6,
                "dead_storage_m3": 10e6,
                "hydropower_max_m3": 60e6,
                "mrf_target_m3": 10e6,
                "initial_storage_m3": 50e6,
            }
        ],
        inflows={"A": [80e6, 0.0, 0.0, 200e6]},
        municipal={"A": [20e6, 20e6, 20e6, 20e6]},
        industrial={"A": [10e6, 10e6, 10e6, 10e6]},
        irrigation={"A": [10e6, 10e6, 10e6, 10e6]},
        transfers=[],
    )

    summary = run_simplified(tmp_path).set_index("basin")
    row = summary.loc["A"]
    assert row["inflow_Mm3_yr"] == pytest.approx(840.0)
    assert row["municipal_Mm3_yr"] == pytest.approx(210.0)
    assert row["industrial_Mm3_yr"] == pytest.approx(90.0)
    assert row["irrigation_Mm3_yr"] == pytest.approx(90.0)
    assert row["demands_delivered_Mm3_yr"] == pytest.approx(390.0)
    assert row["sea_discharge_Mm3_yr"] == pytest.approx(300.0)
    assert row["hydropower_Mm3_yr"] == pytest.approx(540.0)
    assert row["mrf_reliability_pct"] == pytest.approx(100.0)
    assert row["municipal_reliability_pct"] == pytest.approx(75.0)
    assert row["industrial_reliability_pct"] == pytest.approx(75.0)
    assert row["irrigation_reliability_pct"] == pytest.approx(75.0)

    delta_storage_annual_mm3 = (100.0 - 50.0) / (4.0 / 12.0)
    assert row["inflow_Mm3_yr"] - row["demands_delivered_Mm3_yr"] - row["sea_discharge_Mm3_yr"] == pytest.approx(
        delta_storage_annual_mm3
    )


def test_interbasin_transfer_capacity_accounting_and_priorities(tmp_path):
    dates = ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"]
    _write_water_inputs(
        tmp_path,
        dates=dates,
        basin_params=[
            {
                "basin": "A",
                "reservoir_capacity_m3": 100e6,
                "dead_storage_m3": 10e6,
                "hydropower_max_m3": 60e6,
                "mrf_target_m3": 10e6,
                "initial_storage_m3": 50e6,
            },
            {
                "basin": "B",
                "reservoir_capacity_m3": 40e6,
                "dead_storage_m3": 10e6,
                "hydropower_max_m3": 30e6,
                "mrf_target_m3": 10e6,
                "initial_storage_m3": 20e6,
            },
        ],
        inflows={"A": [80e6, 50e6, 0.0, 200e6], "B": [50e6, 10e6, 5e6, 100e6]},
        municipal={"A": [20e6, 20e6, 30e6, 20e6], "B": [20e6, 20e6, 20e6, 20e6]},
        industrial={"A": [10e6, 10e6, 10e6, 10e6], "B": [10e6, 10e6, 10e6, 10e6]},
        irrigation={"A": [10e6, 10e6, 10e6, 10e6], "B": [10e6, 10e6, 10e6, 10e6]},
        transfers=[
            {
                "from_basin": "A",
                "to_basin": "B",
                "link_name": "canal_A_to_B",
                "max_capacity_m3": 20e6,
            }
        ],
    )

    summary = run_simplified(tmp_path).set_index("basin")
    assert summary.loc["A", "transfer_out_Mm3_yr"] == pytest.approx(90.0)
    assert summary.loc["B", "transfer_in_Mm3_yr"] == pytest.approx(90.0)
    assert summary.loc["A", "transfer_in_Mm3_yr"] == pytest.approx(0.0)
    assert summary.loc["B", "transfer_out_Mm3_yr"] == pytest.approx(0.0)

    assert summary.loc["A", "municipal_Mm3_yr"] == pytest.approx(270.0)
    assert summary.loc["A", "industrial_Mm3_yr"] == pytest.approx(90.0)
    assert summary.loc["A", "irrigation_Mm3_yr"] == pytest.approx(90.0)
    assert summary.loc["A", "sea_discharge_Mm3_yr"] == pytest.approx(300.0)
    assert summary.loc["A", "hydropower_Mm3_yr"] == pytest.approx(600.0)
    assert summary.loc["A", "mrf_reliability_pct"] == pytest.approx(100.0)
    assert summary.loc["A", "municipal_reliability_pct"] == pytest.approx(100.0)
    assert summary.loc["A", "industrial_reliability_pct"] == pytest.approx(75.0)
    assert summary.loc["A", "irrigation_reliability_pct"] == pytest.approx(75.0)

    assert summary.loc["B", "municipal_Mm3_yr"] == pytest.approx(195.0)
    assert summary.loc["B", "industrial_Mm3_yr"] == pytest.approx(90.0)
    assert summary.loc["B", "irrigation_Mm3_yr"] == pytest.approx(60.0)
    assert summary.loc["B", "sea_discharge_Mm3_yr"] == pytest.approx(180.0)
    assert summary.loc["B", "hydropower_Mm3_yr"] == pytest.approx(315.0)
    assert summary.loc["B", "mrf_reliability_pct"] == pytest.approx(100.0)
    assert summary.loc["B", "municipal_reliability_pct"] == pytest.approx(75.0)
    assert summary.loc["B", "industrial_reliability_pct"] == pytest.approx(75.0)
    assert summary.loc["B", "irrigation_reliability_pct"] == pytest.approx(50.0)

    out_csv = tmp_path / "outputs" / "simplified_water_balance_summary.csv"
    assert out_csv.is_file()


def test_mrf_shortfall_reliability_and_optional_transfers(tmp_path):
    dates = ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"]
    _write_water_inputs(
        tmp_path,
        dates=dates,
        basin_params=[
            {
                "basin": "A",
                "reservoir_capacity_m3": 0.0,
                "dead_storage_m3": 0.0,
                "hydropower_max_m3": 0.0,
                "mrf_target_m3": 10e6,
                "initial_storage_m3": 0.0,
            }
        ],
        inflows={"A": [30e6, 5e6, 30e6, 30e6]},
        municipal={"A": [10e6, 10e6, 10e6, 10e6]},
        industrial={"A": [5e6, 5e6, 5e6, 5e6]},
        irrigation={"A": [5e6, 5e6, 5e6, 5e6]},
        transfers=None,
    )

    summary = run_simplified(tmp_path).set_index("basin")
    assert summary.loc["A", "transfer_in_Mm3_yr"] == pytest.approx(0.0)
    assert summary.loc["A", "transfer_out_Mm3_yr"] == pytest.approx(0.0)
    assert summary.loc["A", "mrf_reliability_pct"] == pytest.approx(75.0)
    assert summary.loc["A", "municipal_reliability_pct"] == pytest.approx(75.0)
    assert summary.loc["A", "sea_discharge_Mm3_yr"] == pytest.approx(105.0)
