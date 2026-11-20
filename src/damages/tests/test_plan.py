import pandas as pd
import pytest

from damages.config import read_damage_config
from damages.plan import CALCULATED, NOT_CONFIGURED, NOT_VULNERABLE, plan_asset_type


@pytest.fixture
def config(example_config_path):
    return read_damage_config(example_config_path)


def test_calculated(config):
    plan = plan_asset_type(config, "road_major", "depth_m")
    assert plan.status == CALCULATED
    assert plan.weights == {"nirandjan_F7.4": 0.5, "nirandjan_F7.5": 0.5}
    assert plan.cost.unit.per == "km"


def test_not_vulnerable(config):
    plan = plan_asset_type(config, "road_major", "ws_10min")
    assert plan.status == NOT_VULNERABLE
    assert plan.cost is None


@pytest.mark.parametrize(
    "asset_type, metric, reason",
    [
        ("unknown_type", "depth_m", "not listed in the workbook"),
        ("road_major", "landslide", "no vulnerability entry"),
    ],
)
def test_not_configured(config, asset_type, metric, reason):
    plan = plan_asset_type(config, asset_type, metric)
    assert plan.status == NOT_CONFIGURED
    assert reason in plan.reason


def test_checks_assets(config):
    points = pd.DataFrame({"geom_type": ["Point"]})
    plan = plan_asset_type(config, "road_major", "depth_m", points)
    assert plan.status == NOT_CONFIGURED
    assert "needs line geometries" in plan.reason

    lines = pd.DataFrame({"geom_type": ["LineString", "MultiLineString"]})
    assert plan_asset_type(config, "road_major", "depth_m", lines).status == CALCULATED

    plants = pd.DataFrame({"geom_type": ["Point", "Point"], "capacity_mw": [10.0, None]})
    plan = plan_asset_type(config, "power_plant_thermal", "depth_m", plants)
    assert plan.status == NOT_CONFIGURED
    assert "1 assets have missing" in plan.reason

    no_capacity = pd.DataFrame({"geom_type": ["Point"]})
    plan = plan_asset_type(config, "power_plant_thermal", "depth_m", no_capacity)
    assert "no 'capacity_mw' column" in plan.reason
