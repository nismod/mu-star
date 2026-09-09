"""Project data path conventions aligned with mu-star.

Defaults follow the repository's unnumbered ``data/{incoming,processed,out}``
layout. Snakemake rules pass explicit directories into the build functions, so
these helpers only supply defaults (and the OSM/nightlight cache locations used
by :mod:`energy.osm` and :mod:`energy.network_source`).
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Return the repository root, allowing an explicit environment override."""
    override = os.environ.get("MU_STAR_ENERGY_REPO")
    if override:
        return Path(override).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "workflow").is_dir():
            return candidate
    raise RuntimeError("Could not locate the mu-star repository root")


def data_root() -> Path:
    """Return the shared data root, allowing OneDrive or another external location."""
    override = os.environ.get("MU_STAR_DATA_ROOT")
    return Path(override).expanduser().resolve() if override else repo_root() / "data"


def incoming_energy_dir() -> Path:
    return data_root() / "incoming" / "energy"


def processed_energy_dir() -> Path:
    return data_root() / "processed" / "energy"


def output_energy_dir() -> Path:
    return data_root() / "out" / "energy"


def network_output_dir() -> Path:
    """Built network bundles sit alongside their checksum-linked sidecars."""
    return processed_energy_dir() / "networks"
