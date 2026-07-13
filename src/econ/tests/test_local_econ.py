import os
import shutil
import subprocess
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "workflow" / "0-preprocess" / "econ.py"
SCRIPT_SPEC = spec_from_file_location("workflow_local_econ", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)
preprocess_local_econ = SCRIPT_MODULE.preprocess_local_econ


def _write_accounts_workbook(path):
    accounts = pd.DataFrame(
        [
            ["", "Gross output at basic prices"],
            ["Crop and animal production", 47.12],
            ["Source", None],
        ],
        columns=["Kind of economic activity", "2024"],
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for index in range(17):
            pd.DataFrame({"placeholder": []}).to_excel(writer, sheet_name=f"Sheet {index}", index=False)
        accounts.to_excel(writer, sheet_name="National accounts", index=False, startrow=3)


def _install_fake_model_modules(monkeypatch, boundaries):
    class GidColumn:
        @staticmethod
        def isin(values):
            return values

    class Selector:
        gid_0 = GidColumn()

    class Relation:
        def __init__(self, frame):
            self.frame = frame

        def filter(self, country_codes):
            return Relation(self.frame[self.frame.gid_0.isin(country_codes)])

        def execute(self):
            return self.frame.copy()

    class Connection:
        @staticmethod
        def read_parquet(_path):
            return Relation(boundaries)

    ibis = types.ModuleType("ibis")
    ibis._ = Selector()

    scalenav = types.ModuleType("scalenav")
    scalenav.__path__ = []
    scalenav_oop = types.ModuleType("scalenav.oop")
    scalenav_oop.connect = Connection
    scalenav.oop = scalenav_oop

    globdata = types.ModuleType("globdata")
    globdata.__path__ = []
    globdata.ISIC_CODES = {"Section A": {"description": "Crop and animal production\nAdditional detail"}}
    globdata.REGION_INDEX = pd.DataFrame(
        {
            "country": ["Mauritius"],
            "gid_0": ["MUS"],
            "sub_continent": ["Eastern Africa"],
            "continent": ["Africa"],
        }
    )
    globdata_parameters = types.ModuleType("globdata.parameters")
    globdata_parameters.load_catalogue = lambda **_: {"custom_bounds": "catalogue-boundaries"}

    monkeypatch.setitem(sys.modules, "ibis", ibis)
    monkeypatch.setitem(sys.modules, "scalenav", scalenav)
    monkeypatch.setitem(sys.modules, "scalenav.oop", scalenav_oop)
    monkeypatch.setitem(sys.modules, "globdata", globdata)
    monkeypatch.setitem(sys.modules, "globdata.parameters", globdata_parameters)


def test_preprocess_local_econ_regression(tmp_path, monkeypatch):
    accounts_path = tmp_path / "accounts.xlsx"
    output_path = tmp_path / "mus.parquet"
    _write_accounts_workbook(accounts_path)

    boundaries = gpd.GeoDataFrame(
        {"gid_0": ["MUS"], "name": ["Mauritius"]},
        geometry=[box(57.0, -21.0, 58.0, -19.0)],
        crs="EPSG:4326",
    )
    _install_fake_model_modules(monkeypatch, boundaries)

    result = preprocess_local_econ(
        raw_data_path=accounts_path,
        output_path=output_path,
        catalogue_root="../",
        country_code="MUS",
        year=2024,
        rupees_per_usd=47.12,
    )

    assert output_path.exists()
    assert result.loc[0, "gid_0"] == "MUS"
    assert result.loc[0, "A"] == pytest.approx(1_000_000)


def test_snakemake_local_econ_rule_dry_run(tmp_path):
    snakemake = shutil.which("snakemake")
    if snakemake is None:
        pytest.skip("snakemake is not installed")

    repository = Path(__file__).resolve().parents[3]
    accounts_path = tmp_path / "accounts.xlsx"
    output_path = tmp_path / "mus.parquet"
    config_path = tmp_path / "config.yaml"
    accounts_path.touch()
    config_path.write_text(
        "paths:\n"
        f"  raw_local_econ_data: {str(accounts_path)!r}\n"
        "  local_econ_catalogue_root: '../'\n"
        f"  processed_local_catalogue_root: {str(tmp_path / 'catalogue')!r}\n"
        f"  processed_local_econ_data: {str(output_path)!r}\n"
        "local_econ:\n"
        "  country_code: 'MUS'\n"
        "  year: 2024\n"
        "  rupees_per_usd: 47.12\n"
    )

    environment = os.environ.copy()
    environment["XDG_CACHE_HOME"] = str(tmp_path / ".cache")
    completed = subprocess.run(
        [
            snakemake,
            "--snakefile",
            str(repository / "workflow" / "Snakefile"),
            "--configfile",
            str(config_path),
            "--cores",
            "1",
            "--dry-run",
            str(output_path),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "preprocess_local_econ" in completed.stdout + completed.stderr
