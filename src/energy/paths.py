"""Paths of the energy data folders, all under one data root:

    <data_root>/incoming/Infrastructure/Energy/...   source data, laid out as on the shared drive
    <data_root>/processed/energy/<model_data>/...    files written by the workflow
    <data_root>/out/energy/<model_data>/...          tables and reports for people to read

The data root is ``data_root`` in ``config/config.yaml`` (default ``<repo>/data``), and
``model_data`` is ``energy.model_data`` in ``config/energy/energy.yaml``: the name of a
model-data pack. The Snakemake rules pass every path; these helpers are for calling the
Python code directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The mu-star checkout this package was installed from (editable install)."""
ENERGY_CONFIG_PATH = REPO_ROOT / "config" / "energy" / "energy.yaml"


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


def model_data(energy_config: dict | None = None) -> str:
    """Return ``energy.model_data``, from ``config/energy/energy.yaml`` unless ``energy_config`` is given.

    Raises ValueError unless it is a single folder name, such as ``20261006-model-data``.
    """
    if energy_config is None:
        loaded = yaml.safe_load(ENERGY_CONFIG_PATH.read_text()) or {}
        energy_config = loaded.get("energy") or {}
    name = str(energy_config.get("model_data") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in {".", ".."}:
        raise ValueError(f"energy.model_data must be a folder name such as 20261006-model-data, got {name!r}")
    return name


INCOMING_ENERGY_RELATIVE = Path("incoming") / "Infrastructure" / "Energy"
"""Energy source data, relative to the data root (``Incoming Data/Infrastructure/Energy`` on the shared drive)."""


def incoming_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / INCOMING_ENERGY_RELATIVE


def processed_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "processed" / "energy" / model_data()


def network_output_dir(root: Path | None = None) -> Path:
    return processed_energy_dir(root) / "networks"


def output_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "out" / "energy" / model_data()
