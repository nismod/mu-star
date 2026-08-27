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

# Rough WGS84 bounding boxes to zoom a multi-island product to a single island
# (Mauritius and Rodrigues are ~560 km apart, so the full extent is mostly ocean).
MAURITIUS_BBOX = (57.3, -20.6, 57.9, -19.9)
RODRIGUES_BBOX = (63.3, -19.8, 63.5, -19.6)


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


def list_products() -> "pd.DataFrame":
    """Return a table of built products with node/edge counts (for the selector cell)."""
    import pandas as pd

    rows = []
    for name in available_products():
        nodes, edges = load_layers(name)
        rows.append({"product": name, "nodes": len(nodes), "edges": len(edges)})
    return pd.DataFrame(rows)


def anchor_nodes(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep observed/reviewed power terminals, dropping inferred road vertices."""
    if "is_inferred" in nodes.columns:
        keep = ~nodes["is_inferred"].fillna(False).astype(bool)
        if keep.any():
            return nodes.loc[keep]
    return nodes


def plot_network(
    name,
    *,
    nodes="anchors",
    node_color_by="kind",
    edge_color_by="source",
    node_size=None,
    ax=None,
    title=None,
    figsize=(9, 11),
    clip=None,
):
    """Map a single product's edges and nodes.

    nodes: ``"anchors"`` (observed power terminals only), ``"all"``, or ``"none"``.
    clip: optional ``(minx, miny, maxx, maxy)`` bbox to zoom to (e.g. ``MAURITIUS_BBOX``).
    """
    node_layer, edge_layer = load_layers(name)
    if clip is not None:
        minx, miny, maxx, maxy = clip
        edge_layer = edge_layer.cx[minx:maxx, miny:maxy]
        node_layer = node_layer.cx[minx:maxx, miny:maxy]
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    # Colour a handful of edges by source; draw dense road networks as thin grey.
    few_edges = (
        edge_color_by in edge_layer.columns
        and len(edge_layer) < 500
        and edge_layer[edge_color_by].nunique(dropna=True) > 1
    )
    if few_edges:
        edge_layer.plot(ax=ax, column=edge_color_by, legend=True, categorical=True, linewidth=0.8)
    else:
        edge_layer.plot(ax=ax, color="0.75", linewidth=0.4)

    selection = {"anchors": anchor_nodes(node_layer), "all": node_layer}.get(nodes)
    if selection is not None and len(selection):
        size = node_size if node_size is not None else (36 if len(selection) < 500 else 6)
        # With dense grey edges, colour the terminals by kind to carry the legend.
        if not few_edges and node_color_by in selection.columns and selection[node_color_by].nunique(dropna=True) > 1:
            selection.plot(ax=ax, column=node_color_by, categorical=True, legend=True, markersize=size, zorder=5)
        else:
            selection.plot(ax=ax, color="crimson", markersize=size, edgecolor="white", linewidth=0.3, zorder=5)

    shown = 0 if selection is None else len(selection)
    ax.set_axis_off()
    ax.set_title(title or f"{name}\n{len(edge_layer)} edges \u00b7 {shown} nodes shown ({nodes})")
    return ax
