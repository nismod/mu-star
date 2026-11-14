import numpy as np
import pandas as pd
import pytest

from damages.config import SHEETS, ConfigError, read_damage_config, validate_damage_config


def test_read_example_config(example_config_path):
    config = read_damage_config(example_config_path)
    assert validate_damage_config(config) == []
    assert config.intensity_metric("coastal_aqueduct") == "depth_m"
    assert config.is_vulnerable("road_major", "depth_m") is True
    assert config.is_vulnerable("road_major", "ws_10min") is False
    assert config.is_vulnerable("road_major", "landslide") is None
    curves, weights = config.curves_for("road_major", "depth_m")
    assert weights == {"nirandjan_F7.4": 0.5, "nirandjan_F7.5": 0.5}
    assert set(curves) == set(weights)
    assert curves["nirandjan_F7.4"].damage_fraction(np.array([0.0]))[0] == 0
    cost = config.cost("power_plant_thermal")
    assert cost.unit.per == "capacity_mw"
    assert cost.mean == 40_000_000


def _write(tmp_path, tables):
    path = tmp_path / "config.xlsx"
    with pd.ExcelWriter(path) as writer:
        for sheet, table in tables.items():
            table.to_excel(writer, sheet_name=sheet, index=False)
    return path


def _tables():
    return {
        "asset-type": pd.DataFrame({"asset_type": ["a", "b"]}),
        "hazard-metric": pd.DataFrame({"intensity_metric": ["depth_m"]}),
        "hazard": pd.DataFrame({"hazard": ["flood"], "intensity_metric": ["depth_m"]}),
        "asset-type-hazard-vulnerability": pd.DataFrame(
            {"asset_type": ["a", "b"], "hazard_intensity_metric": ["depth_m", "depth_m"], "vulnerable": [1, 1]}
        ),
        "asset-type-cost": pd.DataFrame(
            {
                "asset_type": ["a"],
                "cost_unit": ["usd"],
                "cost_mean": [10.0],
                "cost_systemic_cv": [np.nan],
                "cost_asset_cv": [np.nan],
            }
        ),
        "damage-curves": pd.DataFrame({"curve_id": ["c1", "c2"], "hazard_intensity_metric": ["depth_m", "wind"]}),
        "damage-curve-data": pd.DataFrame(
            {"curve_id": ["c1", "c1", "c1"], "hazard_intensity": [0, 1, 2], "damage_fraction": [0, 0.5, np.nan]}
        ),
        "asset-type-damage-curve": pd.DataFrame(
            {
                "asset_type": ["a", "a", "x"],
                "hazard_intensity_metric": ["depth_m", "depth_m", "depth_m"],
                "curve_id": ["c1", "c2", "c1"],
                "curve_weight": [0.3, 0.3, 1],
            }
        ),
    }


def test_validate_finds_problems(tmp_path):
    config = read_damage_config(_write(tmp_path, _tables()))
    problems = "\n".join(validate_damage_config(config))
    assert "'wind' not found in hazard-metric" in problems
    assert "asset_type 'x' not found" in problems
    assert "c2" in problems and "is for 'wind'" in problems
    assert "'c1' has 1 incomplete data points" in problems
    assert "no data points for curve 'c2'" in problems
    assert "sum to 0.6" in problems
    assert "b is vulnerable to depth_m but has no damage curves" in problems
    assert "No cost given for asset type 'b'" in problems


def test_cost_defaults_and_errors(tmp_path):
    config = read_damage_config(_write(tmp_path, _tables()))
    cost = config.cost("a")
    assert (cost.systemic_cv, cost.asset_cv) == (0.0, 0.0)
    with pytest.raises(ConfigError):
        config.cost("b")
    with pytest.raises(ConfigError, match="No complete data"):
        config.curve("c2")
    # incomplete points are dropped
    assert config.curve("c1").damage_fraction(np.array([2.0]))[0] == 0.5


def test_missing_sheet(tmp_path):
    tables = _tables()
    del tables[SHEETS["costs"][0]]
    with pytest.raises(ConfigError, match="missing sheet"):
        read_damage_config(_write(tmp_path, tables))
