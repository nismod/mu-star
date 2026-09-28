"""Where the data lives.

Everything the energy pipeline reads or writes sits under one *data root*:

    <data_root>/incoming/energy/...    source data as received, never edited by hand
    <data_root>/processed/energy/...   intermediate files written by the workflow
    <data_root>/out/energy/...         human-readable tables and reports

The data root is ``data_root`` in ``config/config.yaml`` (default ``data``,
relative to the repository). The Snakemake rules pass every path explicitly, so
these helpers only matter when you call the Python functions yourself, for
example from a notebook.
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


def incoming_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "incoming" / "energy"


def processed_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "processed" / "energy"


def network_output_dir(root: Path | None = None) -> Path:
    return processed_energy_dir(root) / "networks"


def output_energy_dir(root: Path | None = None) -> Path:
    return Path(root or data_root()) / "out" / "energy"
