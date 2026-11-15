"""End to end: every land transport script on the synthetic network (transport.testing)."""

import json

import pytest

pytest.importorskip("transport_flow_model")
pytest.importorskip("geopandas")
pytest.importorskip("h3")

import pandas as pd

from transport.testing import run_chain, write_config, write_inputs


@pytest.fixture(scope="module")
def toy(tmp_path_factory):
    root = tmp_path_factory.mktemp("toy")
    inputs = write_inputs(root / "in")
    config = write_config(root / "cfg.yaml")
    out = root / "out"
    run_chain(config, inputs, out, n_batches=2)
    return out


def test_pipeline(toy):
    out = toy

    # Baseline: all demand assigned.
    base = json.loads((out / "base.json").read_text())
    assert base["demand"] > 0
    assert base["assigned"] == pytest.approx(base["demand"])
    assert not base["unassigned_by_mode"]

    # The car base network (for the skim) has the same road and connector links as the
    # multimodal network; every toy zone is near a road.
    columns = ["edge_id", "edge_from", "edge_to", "time_min", "length_km"]
    car = pd.read_parquet(out / "car-links.parquet").sort_values("edge_id").reset_index(drop=True)
    multimodal = pd.read_parquet(out / "links.parquet")
    multimodal = multimodal[multimodal.layer.isin(["road", "car_connector"])].sort_values("edge_id")
    pd.testing.assert_frame_equal(car[columns], multimodal[columns].reset_index(drop=True))
    assert pd.read_csv(out / "zones-without-car-access.csv").empty

    # Failure sets cover every asset type; at least one road failure has a bus detour.
    sets = pd.read_parquet(out / "fs.parquet")
    assert {"road", "metro", "metro_station"} <= set(sets.asset_type)
    roads = sets[sets.asset_type == "road"]
    assert roads["link_changes"].map(len).gt(0).any()

    crit = pd.read_csv(out / "crit.csv")
    assert crit["evaluated"].all()

    # Buses detour or skip stops: no bus trip is isolated by a road failure.
    assert crit.loc[crit.asset_type == "road", "isolated_flow_bus"].fillna(0).eq(0).all()

    # A metro station closure moves trips to bus or car, isolating none.
    stations = crit[crit.asset_type == "metro_station"]
    assert len(stations) > 0
    assert (stations["shifted_to_bus"] + stations["shifted_to_car"]).gt(0).all()
    assert stations["isolated_flow"].eq(0).all()

    # Splits by purpose add up to the totals.
    purposes = sorted(base["demand_by_purpose"])
    for measure in ("affected_flow", "isolated_flow", "delta_time_min_total", "delta_cost_total"):
        split = crit[[f"{measure}_{p}" for p in purposes]].fillna(0).sum(axis=1)
        assert split.to_numpy() == pytest.approx(crit[measure].fillna(0).to_numpy(), abs=1e-6)

    # Exact metro fares come from the stage matrix (Rs 32-52).
    journeys = pd.read_parquet(out / "odj.parquet")
    metro = journeys[journeys["mode"] == "metro"]
    assert len(metro) > 0
    assert metro["fare"].between(32, 52).all()
