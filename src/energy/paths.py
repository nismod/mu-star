"""Paths of the energy data folders, all under one data root:

    <data_root>/incoming/Infrastructure/Energy/...   source data, laid out as on the shared drive
    <data_root>/processed/energy/...                 files written by the workflow
    <data_root>/out/energy/...                       tables and reports for people to read

The data root is ``data_root`` in ``config/config.yaml`` (default ``<repo>/data``). The
Snakemake rules pass every path; these helpers are for calling the Python code directly.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The mu-star checkout this package was installed from (editable install)."""


def repo_root() -> Path:
    """Return the repository root."""
    return REPO_ROOT


def data_root() -> Path:
    """Return the data root from ``config/config.yaml`` (default ``<repo>/data``)."""
    config_path = REPO_ROOT / "config" / "config.yaml"
    configured = "data"
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text()) or {}
        configured = str(loaded.get("data_root") or "data")
    root = Path(configured).expanduser()
    return root if root.is_absolute() else REPO_ROOT / root


INCOMING_ENERGY_RELATIVE = Path("incoming") / "Infrastructure" / "Energy"
"""Energy source data, relative to the data root (``Incoming Data/Infrastructure/Energy`` on the shared drive)."""


def incoming_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / INCOMING_ENERGY_RELATIVE


def processed_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "processed" / "energy"


def network_output_dir(root: Path | None = None) -> Path:
    return processed_energy_dir(root) / "networks"


def output_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "out" / "energy"
