"""Run the damage report script on example data, with planted problems."""

import shutil
import subprocess
import sys

import geopandas as gpd

from damages.tests.conftest import EXAMPLE, REPO

SCRIPT = REPO / "workflow" / "1-damage" / "damage_report.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, cwd=REPO)


def test_report_example(tmp_path):
    output = tmp_path / "report.md"
    result = _run(
        "--data-root",
        EXAMPLE,
        "--layers",
        EXAMPLE / "config" / "layer.csv",
        "--hazards",
        EXAMPLE / "config" / "hazard.csv",
        "--damage-config",
        EXAMPLE / "config" / "asset-type-cost-curves.xlsx",
        "--output",
        output,
        "--strict",
    )
    assert result.returncode == 0, result.stderr
    report = output.read_text()
    assert "Layers: 2 of 2 available" in report
    assert "4 will be calculated, 2 not vulnerable" in report
    assert "| road_major | 457 | calculated |" in report


def test_report_problems(tmp_path):
    shutil.copytree(EXAMPLE / "processed", tmp_path / "processed")
    power = tmp_path / "processed" / "asset" / "layer-power.gpq"
    plants = gpd.read_parquet(power)
    plants.loc[0, "asset_type"] = "power_plant_mystery"
    plants.loc[1, "capacity_mw"] = None
    plants.to_parquet(power)
    layers = tmp_path / "layer.csv"
    layers.write_text("layer,path\npower,processed/asset/layer-power.gpq\nwater,processed/asset/layer-water.gpq\n")
    hazards = tmp_path / "hazard.csv"
    hazards.write_text(
        "peril,subperil,path,variable,hazard\n"
        "flood,coastal,processed/hazard/rp/peril-flood/subperil-coastal.zarr,depth,coastal_aqueduct\n"
        "cyclone,wind,processed/hazard/rp/peril-cyclone/subperil-wind.zarr,wind_speed,wind_storm\n"
    )
    args = ["--data-root", tmp_path, "--layers", layers, "--hazards", hazards]
    config = ["--damage-config", EXAMPLE / "config" / "asset-type-cost-curves.xlsx"]

    result = _run(*args, *config)
    assert result.returncode == 0, result.stderr
    report = result.stdout
    assert "Layers: 1 of 2 available" in report
    assert "file not found" in report
    assert "no variable 'depth'" in report
    assert "asset types not in workbook 'asset-type' sheet: power_plant_mystery" in report
    assert "1 assets have missing, non-numeric or negative 'capacity_mw'" in report
    assert "blocked: layer missing" in report

    assert _run(*args, *config, "--strict").returncode != 0

    missing_config = _run(*args, "--damage-config", tmp_path / "nope.xlsx")
    assert missing_config.returncode == 0
    assert "workbook not found" in missing_config.stdout
