"""Local, dev-only helpers for inspecting energy pipeline outputs.

These functions are NOT part of the packaged ``energy`` model or any Snakemake
rule. They only *read* the standard files the pipeline writes (GeoParquet,
PyPSA NetCDF, validation JSON) and render them for quick visual debugging.
Production visualisation lives in the separate viewer (nismod/irv-standalone),
so keep plotting code here rather than in ``src/energy/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt


def find_repo_root(start: Path | None = None) -> Path:
    base = Path(start or Path(__file__).resolve().parent)
    for candidate in (base, *base.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise FileNotFoundError("Could not locate repo root (no pyproject.toml found)")


REPO_ROOT = find_repo_root()
DATA_ROOT = REPO_ROOT / "data"
NETWORKS_DIR = DATA_ROOT / "processed" / "energy" / "networks"
OUT_DIR = DATA_ROOT / "out" / "energy"

PRODUCTS = (
    "base-mauritius",
    "inferred-osm-mauritius-rodrigues",
    "inferred-data-mauritius-rodrigues",
)


def _geoparquet_dir(name: str) -> Path:
    return NETWORKS_DIR / name / "geoparquet"


def available_products() -> list[str]:
    """Return the products that have been built (GeoParquet node layer on disk)."""
    return [p for p in PRODUCTS if (_geoparquet_dir(p) / f"{p}-nodes.geoparquet").exists()]


def load_layers(name: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return ``(nodes, edges)`` GeoDataFrames for a built network product."""
    gp = _geoparquet_dir(name)
    nodes = gpd.read_parquet(gp / f"{name}-nodes.geoparquet")
    edges = gpd.read_parquet(gp / f"{name}-edges.geoparquet")
    return nodes, edges


def load_validation(name: str) -> dict:
    """Return the human-review validation report for a product (``{}`` if absent)."""
    path = OUT_DIR / name / "validation.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load_pypsa(name: str):
    """Load the PyPSA network for a product (``pypsa`` imported lazily)."""
    import pypsa

    return pypsa.Network(str(NETWORKS_DIR / name / f"{name}.nc"))


def summarise(name: str) -> dict:
    """Return quick counts, CRS and column names for a product."""
    nodes, edges = load_layers(name)
    return {
        "product": name,
        "nodes": len(nodes),
        "edges": len(edges),
        "crs": str(edges.crs),
        "node_columns": list(nodes.columns),
        "edge_columns": list(edges.columns),
    }


def plot_network(name, *, color_by="source", ax=None, node_size=6, title=None):
    """Plot a product's edges (optionally coloured by a column) and its nodes."""
    nodes, edges = load_layers(name)
    if ax is None:
        _, ax = plt.subplots(figsize=(9, 11))
    plot_kwargs = {"linewidth": 0.6}
    if color_by and color_by in edges.columns and edges[color_by].nunique(dropna=True) > 1:
        plot_kwargs.update(column=color_by, legend=True, categorical=True)
    edges.plot(ax=ax, **plot_kwargs)
    nodes.plot(ax=ax, markersize=node_size, color="crimson", zorder=5)
    ax.set_axis_off()
    ax.set_title(title or f"{name}  \u00b7  {len(nodes)} nodes / {len(edges)} edges")
    return ax
