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


# tab10 palette (matches the mauritius-kestrel viewer's line colours).
_TAB10 = (
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
)


def _line_coords(gdf):
    """Flatten (Multi)LineStrings to lon/lat lists with None gaps between parts."""
    lons, lats = [], []
    for geom in gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        parts = geom.geoms if geom.geom_type.startswith("Multi") else (geom,)
        for part in parts:
            xs, ys = part.xy
            lons.extend(xs)
            lats.extend(ys)
            lons.append(None)
            lats.append(None)
    return lons, lats


def explore_network(name, *, roads=True, clip=None, map_style="open-street-map"):
    """Interactive Plotly map of a product for debugging (matches the viewer style).

    Pan/zoom/hover on an OpenStreetMap basemap (no API key). Draws every edge,
    one trace per source coloured with the tab10 palette (as in the viewer): for
    the inferred products the dense OSM road mesh *is* the distribution network,
    drawn underneath the transmission / backbone / anchor layers. Only substation
    and generator buses are marked (with attribute hover). ``roads=False`` hides
    the OSM mesh for a power-only view; ``clip`` restricts to a bbox.
    """
    import math

    import pandas as pd
    import plotly.graph_objects as go

    node_layer, edge_layer = load_layers(name)
    if clip is not None:
        minx, miny, maxx, maxy = clip
        edge_layer = edge_layer.cx[minx:maxx, miny:maxy]
        node_layer = node_layer.cx[minx:maxx, miny:maxy]

    fig = go.Figure()
    # tab10 palette, one stable colour per source category (matches the viewer).
    if "source" in edge_layer.columns:
        categories = sorted(str(s) for s in edge_layer["source"].dropna().unique())
        colour = {c: _TAB10[i % len(_TAB10)] for i, c in enumerate(categories)}
        for src in sorted(categories, key=lambda c: c != "osm"):  # draw osm first
            if src == "osm" and not roads:
                continue
            grp = edge_layer[edge_layer["source"].astype(str).eq(src)]
            lons, lats = _line_coords(grp)
            width = 1.5 if src == "osm" else 2.5
            fig.add_trace(go.Scattermap(lon=lons, lat=lats, mode="lines", name=src,
                                        line={"width": width, "color": colour[src]}, hoverinfo="skip"))
    elif len(edge_layer):
        lons, lats = _line_coords(edge_layer)
        fig.add_trace(go.Scattermap(lon=lons, lat=lats, mode="lines", name="edges",
                                    line={"width": 2}, hoverinfo="skip"))

    # Mark only the meaningful buses (substation / generator); the road/junction
    # vertices stay as line geometry, as in the viewer.
    node_styles = {"substation": ("#111827", 8), "generator": ("#7c3aed", 11)}
    if "kind" in node_layer.columns:
        kinds = node_layer["kind"].astype("string")
        cols = [c for c in ("bus_id", "name", "kind", "source", "v_nom_kv", "model_v_nom_kv") if c in node_layer.columns]
        for kind_val, (colour_hex, size) in node_styles.items():
            grp = node_layer[kinds.eq(kind_val)]
            if grp.empty:
                continue
            hover = grp[cols].apply(lambda r: "<br>".join(f"{c}: {r[c]}" for c in cols if pd.notna(r[c])), axis=1) if cols else None
            fig.add_trace(go.Scattermap(
                lon=grp.geometry.x, lat=grp.geometry.y, mode="markers", name=f"{kind_val} bus",
                marker={"size": size, "color": colour_hex}, text=hover, hoverinfo="text" if cols else "skip"))

    bounds = (edge_layer if len(edge_layer) else node_layer).total_bounds
    center = {"lon": float((bounds[0] + bounds[2]) / 2), "lat": float((bounds[1] + bounds[3]) / 2)}
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1e-3)
    zoom = min(max(math.log2(360 / span) - 1, 3), 12)
    fig.update_layout(
        map={"style": map_style, "center": center, "zoom": zoom},
        margin={"l": 0, "r": 0, "t": 30, "b": 0},
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        title=name, height=650,
    )
    return fig
