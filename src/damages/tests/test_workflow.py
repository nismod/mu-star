"""Integration test: run the damage workflow on the example data."""

import shutil
import subprocess

import pandas as pd
import pytest
import xarray as xr

from damages.tests.conftest import EXAMPLE, REPO


@pytest.mark.integration
def test_example_workflow(tmp_path):
    if shutil.which("snakemake") is None:
        pytest.skip("snakemake not installed")
    data = tmp_path / "data"
    shutil.copytree(EXAMPLE / "processed", data / "processed")
    target = data / "results" / "damage" / "summary.csv"
    subprocess.run(
        ["snakemake", "--cores", "2", "--nolock", str(target), "--configfile", "example/config.yaml"],
        cwd=REPO,
        check=True,
    )

    summary = pd.read_csv(target)
    assert set(summary.layer) == {"road", "power"}
    assert set(summary.subperil) == {"coastal", "wind"}
    assert (summary.status == "calculated").any()

    roads_wind = summary[(summary.layer == "road") & (summary.subperil == "wind")]
    assert (roads_wind.status == "not_vulnerable").all()
    assert (roads_wind.q_50 == 0).all()

    power_wind = summary[(summary.layer == "power") & (summary.subperil == "wind") & (summary.metric == "ead")]
    assert (power_wind.q_5 > 0).all()
    assert (power_wind.q_5 <= power_wind.q_50).all() and (power_wind.q_50 <= power_wind.q_95).all()

    damage = data / "results" / "damage" / "layer-power" / "rp" / "peril-flood" / "subperil-coastal" / "damage.zarr"
    split = xr.open_zarr(damage, group="split", consolidated=False)
    assert split.damage_fraction.dims == ("split", "rcp", "epoch", "return_period", "quantile")
    fraction = split.damage_fraction.load()
    assert float(fraction.max()) > 0
    assert float(fraction.max()) <= 1
    # rcp scenarios have no 2010 baseline epoch in the source data
    assert fraction.sel(rcp="rcp8p5", epoch=2010).isnull().all()
