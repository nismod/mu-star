import numpy as np
import pandas as pd
import pytest

from transport.costs import disruption_deltas, generalised_cost, path_metrics


def test_generalised_cost():
    cost = generalised_cost(
        [10, 10], [5, 5], ["car", "bus"], per_minute=2.0, per_km={"car": 1.0, "bus": 0.0}, fare=[0, 3]
    )
    np.testing.assert_allclose(cost, [25, 23])


def test_path_metrics_and_deltas():
    edges = pd.DataFrame(
        {
            "edge_id": ["AB", "BC", "AC"],
            "time_min": [1.0, 1.0, 5.0],
            "length_km": [1.0, 1.0, 3.0],
            "cost": [2.0, 2.0, 10.0],
        }
    )
    base = pd.DataFrame(
        {"origin_id": ["A"], "destination_id": ["C"], "flow": [10.0], "edge_path": [["AB", "BC"]], "cost": [4.0]}
    )
    rerouted = pd.DataFrame(
        {"origin_id": ["A"], "destination_id": ["C"], "flow": [6.0], "edge_path": [["AC"]], "cost": [10.0]}
    )
    isolated = pd.DataFrame({"origin_id": ["A"], "destination_id": ["C"], "value": [4.0]})
    base_m = path_metrics(base, edges)
    assert base_m.loc[0, "time_min"] == 2.0
    deltas = disruption_deltas(base_m, path_metrics(rerouted, edges), isolated)
    row = deltas.iloc[0]
    assert row["affected_flow"] == 10
    assert row["rerouted_flow"] == 6
    assert row["isolated_flow"] == 4
    assert row["delta_time_min_total"] == pytest.approx(6 * (5 - 2))
    assert row["delta_length_km_total"] == pytest.approx(6 * (3 - 2))


def test_deep_merge_dotted_and_nested():
    from transport.land import deep_merge

    base = {"cost": {"per_minute": 1.0, "per_km": {"car": 6.0, "bus": 0.0}}, "period": {"hours": 2}}
    out = deep_merge(base, {"cost.per_km.car": 8.0, "period": {"hours": 3}})
    assert out["cost"]["per_km"] == {"car": 8.0, "bus": 0.0}
    assert out["cost"]["per_minute"] == 1.0
    assert out["period"]["hours"] == 3
    assert base["cost"]["per_km"]["car"] == 6.0  # base not modified


def test_read_config_scenario_and_island(tmp_path):
    import yaml

    from transport.land import read_config

    cfg = {
        "transport_land": {
            "islands": {"rodrigues": {"overrides": {"mode_split.method": "bands"}}, "mauritius": {}},
            "mode_split": {"method": "ridership", "ridership": {"commute_fraction": {"bus": 0.5}}},
            "scenarios": {"central": {}, "low": {"mode_split.ridership.commute_fraction.bus": 0.3}, "bad": {"road": 1}},
        }
    }
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cfg))
    assert read_config(path, island="rodrigues")["mode_split"]["method"] == "bands"
    low = read_config(path, island="mauritius", scenario="low")
    assert low["mode_split"]["ridership"]["commute_fraction"]["bus"] == 0.3
    with pytest.raises(ValueError):
        read_config(path, scenario="bad")
    with pytest.raises(ValueError):
        read_config(path, scenario="missing")


def test_apply_link_changes_recomputes_cost():
    from transport.costs import apply_link_changes

    links = pd.DataFrame(
        {
            "edge_id": ["a", "b"],
            "mode": ["bus", "car"],
            "time_min": [10.0, 5.0],
            "length_km": [2.0, 3.0],
            "fare": [1.0, 0.0],
            "cost": [0.0, 0.0],
        }
    )
    cfg = {"per_minute": 2.0, "per_km": {"bus": 0.0, "car": 1.0}}
    out = apply_link_changes(links, [{"edge_id": "a", "time_min": 20.0, "length_km": 4.0, "fare": 2.0}], cfg)
    assert out.loc[0, "cost"] == pytest.approx(2.0 * 20 + 2.0)
    assert out.loc[1, "cost"] == 0.0  # unchanged row untouched
    assert links.loc[0, "time_min"] == 10.0  # input not modified


def test_match_codes_by_name_within_district():
    from transport.census import match_codes

    lookup = pd.DataFrame(
        {
            "zone_id": ["1201", "1304", "1801", "2101"],
            "zone_name": ["Arsenal", "Goodlands", "Moka", "Port Mathurin"],
            "district_code": ["12", "13", "18", "21"],
        }
    )
    areas = pd.DataFrame(
        {
            "area_name": ["ARSENAL", "Goodlands ", "Moka", "Port-Mathurin", "Nowhere"],
            "district_name": ["Pamplemousses", "Rivière du Rempart", "Moka", "", "Flacq"],
        }
    )
    districts = {"12": "Pamplemousses", "13": "Rivière du Rempart", "18": "Moka", "14": "Flacq"}
    codes = match_codes(areas, lookup, districts, {"Nowhere|Flacq": "1499"})
    assert codes.tolist() == ["1201", "1304", "1801", "2101", "1499"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"bus.speed_factor": 0.5},
        {"bus": {"speed_factor": 0.5}},
        {"criticality.bus_detour.max_skipped_stops": 1},
        {"criticality": {"bus_detour": {"max_detour_factor": 2.0}}},
        {"criticality.bus_detour": {"max_skipped_stops": 1}},
    ],
)
def test_scenarios_cannot_change_bus_detour_settings(tmp_path, overrides):
    import yaml

    from transport.land import read_config

    cfg = {
        "transport_land": {
            "bus": {"speed_factor": 0.7, "vehicle_capacity": 70},
            "criticality": {"bus_detour": {"max_skipped_stops": 3}, "n_batches": 4},
            "scenarios": {"fixed": overrides, "ok": {"bus.vehicle_capacity": 60, "criticality": {"n_batches": 2}}},
        }
    }
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(cfg))
    assert read_config(path, scenario="ok")["bus"]["vehicle_capacity"] == 60
    with pytest.raises(ValueError, match="same for all scenarios"):
        read_config(path, scenario="fixed")
