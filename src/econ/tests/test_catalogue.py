import os
import shutil
import subprocess
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "workflow" / "0-preprocess" / "catalogue.py"
SCRIPT_SPEC = spec_from_file_location("workflow_catalogue", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
SCRIPT_MODULE = module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)
preprocess_local_catalogue = SCRIPT_MODULE.preprocess_local_catalogue


def _install_fake_catalogue_modules(monkeypatch):
    class Expression:
        def isin(self, values):
            return values

        def point(self, _other):
            return self

        def intersects(self, _other):
            return self

    class Selector:
        gid_0 = Expression()

        def __getitem__(self, _name):
            return Expression()

    class Geometry:
        @staticmethod
        def execute():
            class ExecutedGeometry:
                @staticmethod
                def set_crs(_crs):
                    return ["mauritius-boundary"]

            return ExecutedGeometry()

    class Relation:
        def __init__(self, name):
            self.name = name
            self.geometry = Geometry()

        def filter(self, _condition):
            return self

        def to_parquet(self, path, overwrite=False):
            assert overwrite is True
            Path(path).touch()

    class Connection:
        @staticmethod
        def read_parquet(path):
            return Relation(path)

    ibis = types.ModuleType("ibis")
    ibis._ = Selector()

    scalenav = types.ModuleType("scalenav")
    scalenav.__path__ = []
    scalenav_oop = types.ModuleType("scalenav.oop")
    scalenav_oop.connect = Connection

    def coords_columns(layer):
        if layer.name != "roads":
            raise ValueError
        return "longitude", "latitude"

    scalenav_oop.coords_columns = coords_columns
    scalenav.oop = scalenav_oop

    globdata = types.ModuleType("globdata")
    globdata.__path__ = []
    globdata_parameters = types.ModuleType("globdata.parameters")
    globdata_parameters.load_catalogue = lambda **_: {
        "custom_bounds": "boundaries",
        "roads": "roads",
    }

    monkeypatch.setitem(sys.modules, "ibis", ibis)
    monkeypatch.setitem(sys.modules, "scalenav", scalenav)
    monkeypatch.setitem(sys.modules, "scalenav.oop", scalenav_oop)
    monkeypatch.setitem(sys.modules, "globdata", globdata)
    monkeypatch.setitem(sys.modules, "globdata.parameters", globdata_parameters)


def test_preprocess_local_catalogue_regression(tmp_path, monkeypatch):
    _install_fake_catalogue_modules(monkeypatch)

    missed_layers = preprocess_local_catalogue(
        catalogue_root="../",
        output_root=tmp_path,
        country_code="MUS",
    )

    assert (tmp_path / "MUS_roads" / "roads.parquet").exists()
    assert missed_layers == ["custom_bounds"]


def test_snakemake_local_catalogue_rule_dry_run(tmp_path):
    snakemake = shutil.which("snakemake")
    if snakemake is None:
        pytest.skip("snakemake is not installed")

    repository = Path(__file__).resolve().parents[3]
    raw_econ_path = tmp_path / "accounts.xlsx"
    catalogue_output_root = tmp_path / "catalogue"
    econ_output_path = tmp_path / "mus.parquet"
    complete_path = catalogue_output_root / ".local_catalogue_complete"
    config_path = tmp_path / "config.yaml"
    raw_econ_path.touch()
    config_path.write_text(
        "paths:\n"
        f"  raw_local_econ_data: {str(raw_econ_path)!r}\n"
        "  local_econ_catalogue_root: '../'\n"
        f"  processed_local_catalogue_root: {str(catalogue_output_root)!r}\n"
        f"  processed_local_econ_data: {str(econ_output_path)!r}\n"
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
            str(complete_path),
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "preprocess_local_catalogue" in completed.stdout + completed.stderr
