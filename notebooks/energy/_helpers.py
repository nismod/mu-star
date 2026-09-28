"""Helpers for the energy developer notebooks (``notebooks/energy/**``).

Import this module as ``h`` from a notebook. It knows where the energy pipeline
keeps its files, loads them into GeoPandas / pandas objects and draws quick maps.
It never builds or downloads anything, and it is not part of the packaged
``energy`` model or of any Snakemake rule: production visualisation lives in the
separate viewer (nismod/irv-standalone), so plotting code belongs here rather
than in ``src/energy/``.

Where the files are
-------------------
The data root is ``data_root`` in ``config/config.yaml`` (default: the ``data/``
folder of this repository) and the region is ``energy.region`` in
``config/energy/energy.yaml``. Both are read once, when this module is imported:

- ``DATA_ROOT``, ``REGION``, ``REGION_SLUG``
- ``PROVIDED_DIR``    <data>/processed/energy/provided            cleaned CEB tables
- ``OSM_CACHE_DIR``   <data>/incoming/energy/osm/<region>         roads, power features, area of interest
- ``NIGHTLIGHT_DIR``  <data>/processed/energy/nightlight/<region> radiance composite and target points
- ``NETWORKS_DIR``    <data>/processed/energy/networks/<product>  PyPSA network, metadata, GeoParquet layers
- ``OUT_DIR``         <data>/out/energy/<product>                 generators.csv, lines.csv, validation.json

Inputs (notebook 00)
--------------------
- ``load_provided()``            dict of the cleaned CEB tables that exist.
- ``load_osm_cache()``           dict(roads, power, aoi) of the cached OSM layers that exist.
- ``nightlight_paths()``         dict(composite, targets, metadata) of Paths.
- ``load_nightlight_targets()``  the night-light target points.
- ``found(path, how)``           True if ``path`` exists, otherwise prints what to run.

Built products (notebook 01)
----------------------------
- ``available_products()`` / ``list_products()``  which products are built.
- ``load_layers(name)``                            ``(nodes, edges)`` GeoDataFrames of one product.
- ``load_validation(name)`` / ``load_pypsa(name)`` / ``summarise(name)``.
- ``explore_network(name)``                        interactive Plotly map on an OpenStreetMap basemap.
- ``plot_network(name)``                           static matplotlib map.
- ``quick_map(**layers)``                          static map of any GeoDataFrames (used for the inputs).
- ``MAURITIUS_BBOX`` / ``RODRIGUES_BBOX``          bounding boxes for the ``clip=`` argument.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
import yaml

from energy import osm as energy_osm
from energy import paths as energy_paths
from energy.nightlights import tile_name

# --- Settings -----------------------------------------------------------------

REPO_ROOT = energy_paths.repo_root()
CONFIG_PATH = REPO_ROOT / "config" / "energy" / "energy.yaml"


def _load_energy_config() -> dict:
    """Return the ``energy:`` section of config/energy/energy.yaml (``{}`` when the file is missing)."""
    if not CONFIG_PATH.is_file():
        return {}
    loaded = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return loaded.get("energy") or {}


ENERGY_CONFIG = _load_energy_config()


def _setting(*keys: str, default=None):
    """Read a nested setting from the energy config, e.g. ``_setting("osm", "network_type")``."""
    value = ENERGY_CONFIG
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


REGION = str(_setting("region", default="mauritius-rodrigues")).strip()
REGION_SLUG = energy_osm.region_slug(REGION)
NETWORK_TYPE = str(_setting("osm", "network_type", default="drive")).strip()

DATA_ROOT = energy_paths.data_root()
PROVIDED_DIR = energy_paths.processed_energy_dir(DATA_ROOT) / "provided"
OSM_CACHE_DIR = DATA_ROOT / energy_osm.osm_cache_dir_relative(REGION)
NIGHTLIGHT_DIR = energy_paths.processed_energy_dir(DATA_ROOT) / "nightlight" / REGION_SLUG
NIGHTLIGHT_MONTHLY_DIR = DATA_ROOT / str(
    _setting("nightlight", "source", "monthly_dir", default="incoming/energy/nightlights/viirs-2024-monthly")
)
NETWORKS_DIR = energy_paths.network_output_dir(DATA_ROOT)
OUT_DIR = energy_paths.output_energy_dir(DATA_ROOT)

# Rough WGS84 bounding boxes to zoom a multi-island layer to a single island
# (Mauritius and Rodrigues are ~560 km apart, so the full extent is mostly ocean).
MAURITIUS_BBOX = (57.3, -20.6, 57.9, -19.9)
RODRIGUES_BBOX = (63.3, -19.8, 63.5, -19.6)

# --- Terminal commands that create the files (printed by ``found()``) ---------

FETCH_OSM = "snakemake -c1 fetch_energy_osm  (needs energy.osm.allow_download: true in config/energy/energy.yaml)"
FETCH_NIGHTLIGHTS = (
    "snakemake -c1 fetch_energy_nightlights  "
    "(needs energy.nightlight.source.allow_download: true in config/energy/energy.yaml)"
)
BUILD_ALL = "snakemake -c1 build_energy_networks"


def build_command(path: Path) -> str:
    """Return the terminal command that builds ``path``: a Snakemake target named by the file it writes."""
    path = Path(path)
    try:
        target = path.relative_to(REPO_ROOT)
    except ValueError:
        target = path
    return f"snakemake -c1 {target}"


def found(path: Path, how: str) -> bool:
    """Return True if ``path`` exists; otherwise print one line saying what to run and return False."""
    path = Path(path)
    if path.exists():
        return True
    print(f"not found: {path} -- run: {how}")
    return False


# --- Inputs: the cleaned CEB data ---------------------------------------------

PROVIDED_FILES = (
    "substations.parquet",
    "snapped_substations.parquet",
    "transmission_routes.parquet",
    "generation_points.parquet",
    "generation_areas.parquet",
    "generators.csv",
    "monthly_peak_demand_mw.csv",
    "annual_sector_demand_gwh.csv",
    "service_weights.csv",
    "substation_snap_distances.csv",
)
PREPARE_PROVIDED = build_command(PROVIDED_DIR / "generators.csv")


def _read_table(path: Path) -> pd.DataFrame | gpd.GeoDataFrame:
    """Read a CSV as a DataFrame or a (Geo)Parquet file as a GeoDataFrame."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return gpd.read_parquet(path)


def load_provided() -> dict[str, pd.DataFrame]:
    """Return the cleaned CEB tables that exist in ``PROVIDED_DIR``, keyed by file name without extension.

    The GeoParquet layers come back as GeoDataFrames (``substations``,
    ``snapped_substations``, ``transmission_routes``, ``generation_points``,
    ``generation_areas``) and the CSV files as DataFrames (``generators``,
    ``monthly_peak_demand_mw``, ``annual_sector_demand_gwh``, ``service_weights``,
    ``substation_snap_distances``). The ``prepare_energy_assets`` rule writes them;
    ``PREPARE_PROVIDED`` is the command that runs it.
    """
    tables = {}
    for name in PROVIDED_FILES:
        path = PROVIDED_DIR / name
        if path.is_file():
            tables[path.stem] = _read_table(path)
    return tables


# --- Inputs: the OpenStreetMap cache ------------------------------------------


def osm_paths() -> dict[str, Path]:
    """Return the ``roads``, ``power`` and ``aoi`` files for the configured region.

    These are the cache files the ``fetch_energy_osm`` rule writes. As in the
    workflow, a file named in ``energy.osm.roads`` or ``energy.osm.aoi`` replaces
    the corresponding cache file.
    """
    roads_override = _setting("osm", "roads")
    aoi_override = _setting("osm", "aoi")
    return {
        "roads": DATA_ROOT / (roads_override or energy_osm.roads_cache_relative(REGION, NETWORK_TYPE)),
        "power": DATA_ROOT / energy_osm.power_cache_relative(REGION),
        "aoi": DATA_ROOT / (aoi_override or energy_osm.aoi_cache_relative(REGION)),
    }


def load_osm_cache() -> dict[str, gpd.GeoDataFrame]:
    """Return dict(roads, power, aoi) GeoDataFrames for the files of ``osm_paths()`` that exist."""
    return {key: energy_osm.read_vector(path) for key, path in osm_paths().items() if path.is_file()}


# --- Inputs: the night lights -------------------------------------------------


def nightlight_monthly_tiles() -> list[Path]:
    """Return the monthly VIIRS tiles the workflow expects (cached or not), one per configured object id."""
    object_ids = _setting("nightlight", "source", "object_ids", default=list(range(120, 132)))
    return [NIGHTLIGHT_MONTHLY_DIR / tile_name(index, int(oid)) for index, oid in enumerate(object_ids, start=1)]


def nightlight_paths() -> dict[str, Path]:
    """Return dict(composite, targets, metadata) of Paths.

    ``composite`` is the radiance composite built from the monthly tiles, or the
    ready-made raster named in ``energy.nightlight.nightlights`` when that is set.
    ``targets`` and ``metadata`` are the night-light target points and the
    ``metadata.json`` written next to them.
    """
    override = _setting("nightlight", "nightlights")
    return {
        "composite": DATA_ROOT / override if override else NIGHTLIGHT_DIR / "viirs-composite.tif",
        "targets": NIGHTLIGHT_DIR / "targets.geoparquet",
        "metadata": NIGHTLIGHT_DIR / "metadata.json",
    }


BUILD_NIGHTLIGHT_COMPOSITE = build_command(nightlight_paths()["composite"])
BUILD_NIGHTLIGHT_TARGETS = build_command(nightlight_paths()["targets"])


def load_nightlight_targets() -> gpd.GeoDataFrame:
    """Return the night-light target points: one per bright pixel inside the area of interest."""
    path = nightlight_paths()["targets"]
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist; run: {BUILD_NIGHTLIGHT_TARGETS}")
    return gpd.read_parquet(path)


# --- Built products -----------------------------------------------------------


def _geoparquet_dir(name: str) -> Path:
    return NETWORKS_DIR / name / "geoparquet"


def available_products() -> list[str]:
    """Return the names of the products that are built, e.g. ``["base-mauritius", "inferred-osm-mauritius-rodrigues"]``.

    A product counts as built when its ``geoparquet/`` folder holds the spatial
    manifest together with the node and edge layers.
    """
    names = []
    for manifest in sorted(NETWORKS_DIR.glob("*/geoparquet/*-spatial-manifest.json")):
        name = manifest.name.removesuffix("-spatial-manifest.json")
        layers = manifest.parent
        if (layers / f"{name}-nodes.geoparquet").is_file() and (layers / f"{name}-edges.geoparquet").is_file():
            names.append(name)
    return names


def load_manifest(name: str) -> dict:
    """Return a product's spatial manifest: layer checksums, counts, bounding boxes and the electrical-values note."""
    return json.loads((_geoparquet_dir(name) / f"{name}-spatial-manifest.json").read_text())


def load_layers(name: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return ``(nodes, edges)`` GeoDataFrames of a built product (EPSG:4326)."""
    layers = _geoparquet_dir(name)
    nodes = gpd.read_parquet(layers / f"{name}-nodes.geoparquet")
    edges = gpd.read_parquet(layers / f"{name}-edges.geoparquet")
    return nodes, edges


def load_validation(name: str) -> dict:
    """Return a product's validation report (``data/out/energy/<name>/validation.json``), or ``{}`` if absent."""
    path = OUT_DIR / name / "validation.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load_pypsa(name: str):
    """Return a product's PyPSA network (``pypsa`` is imported here so the other helpers do not need it)."""
    import pypsa

    return pypsa.Network(str(NETWORKS_DIR / name / f"{name}.nc"))


def summarise(name: str) -> dict:
    """Return quick facts about a product: counts, line length, CRS, column names and the manifest's notes."""
    nodes, edges = load_layers(name)
    manifest = load_manifest(name)
    return {
        "product": name,
        "methodology": manifest.get("methodology"),
        "nodes": len(nodes),
        "edges": len(edges),
        "line_length_km": round(float(edges["length_km"].sum()), 1),
        "crs": edges.crs.to_string() if edges.crs is not None else None,
        "node_kinds": nodes["kind"].value_counts(dropna=False).to_dict(),
        "edge_sources": edges["source"].value_counts(dropna=False).to_dict(),
        "node_columns": list(nodes.columns),
        "edge_columns": list(edges.columns),
        "electrical_values_note": manifest.get("electrical_values_note"),
    }


def list_products() -> pd.DataFrame:
    """Return a table of the built products: name, whether inferred, counts, line length and validation status."""
    rows = []
    for name in available_products():
        manifest = load_manifest(name)
        totals = manifest.get("totals", {})
        rows.append(
            {
                "product": name,
                "inferred": manifest.get("inferred"),
                "nodes": totals.get("nodes"),
                "edges": totals.get("edges"),
                "line_length_km": round(float(totals.get("line_length_km", float("nan"))), 1),
                "validation": load_validation(name).get("status", "-"),
            }
        )
    return pd.DataFrame(rows, columns=["product", "inferred", "nodes", "edges", "line_length_km", "validation"])


def anchor_nodes(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep the power terminals (substations, generators, transformers), dropping inferred road vertices."""
    if "is_inferred" in nodes.columns:
        keep = ~nodes["is_inferred"].fillna(False).astype(bool)
        if keep.any():
            return nodes.loc[keep]
    return nodes


# --- Maps ---------------------------------------------------------------------

# tab10 palette (matches the viewer's line colours).
_TAB10 = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)


def _clip(frame: gpd.GeoDataFrame, clip) -> gpd.GeoDataFrame:
    """Crop a layer to a ``(minx, miny, maxx, maxy)`` box (``None`` leaves it unchanged)."""
    if clip is None:
        return frame
    minx, miny, maxx, maxy = clip
    return frame.cx[minx:maxx, miny:maxy]


def quick_map(*, clip=None, ax=None, title=None, figsize=(9, 9), **layers):
    """Draw GeoDataFrames on one static map, e.g. ``quick_map(roads=roads, power=power, aoi=aoi)``.

    Each keyword names a layer (the name goes in the legend) and gives either a
    GeoDataFrame or a ``(GeoDataFrame, style)`` pair, where ``style`` is a dict of
    matplotlib keyword arguments that override the defaults. ``None`` layers are
    skipped. Polygons are drawn as outlines, lines thin and points on top, each
    layer in its own colour. ``clip`` is a ``(minx, miny, maxx, maxy)`` box such as
    ``MAURITIUS_BBOX``: layers are cropped to it before drawing.
    """
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    drawn = 0
    for index, (label, layer) in enumerate(layers.items()):
        frame, style = layer if isinstance(layer, tuple) else (layer, {})
        if frame is None:
            continue
        frame = _clip(frame, clip)
        if frame.empty:
            continue
        colour = _TAB10[index % len(_TAB10)]
        geometry_type = str(frame.geometry.geom_type.iloc[0])
        if geometry_type.endswith("Polygon"):
            # Outlines only: the boundary is a line layer, which also gives a legend entry.
            frame = frame.set_geometry(frame.geometry.boundary)
            options = {"color": colour, "linewidth": 1.0, "zorder": 1}
        elif geometry_type.endswith("LineString"):
            options = {"color": colour, "linewidth": 0.6, "zorder": 2}
        else:
            options = {"color": colour, "markersize": 24, "edgecolor": "white", "linewidth": 0.3, "zorder": 5}
        options.update(style)
        frame.plot(ax=ax, label=label, **options)
        drawn += 1
    ax.set_axis_off()
    if drawn:
        ax.legend(loc="upper left")
    if title:
        ax.set_title(title)
    return ax


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
    """Static map of one product's edges and nodes.

    ``nodes``: ``"anchors"`` (power terminals only), ``"all"``, or ``"none"``.
    ``clip``: optional ``(minx, miny, maxx, maxy)`` box to zoom to (e.g. ``MAURITIUS_BBOX``).
    """
    node_layer, edge_layer = load_layers(name)
    edge_layer = _clip(edge_layer, clip)
    node_layer = _clip(node_layer, clip)
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
    ax.set_title(title or f"{name}\n{len(edge_layer)} edges · {shown} nodes shown ({nodes})")
    return ax


def _line_coords(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(lon, lat)`` arrays of every line in ``gdf``, one NaN between lines.

    Plotly draws a gap at a NaN instead of joining one line to the next, so all
    lines of a layer can go into a single trace. (Multi)LineStrings are split
    into their parts; missing and empty geometries are skipped.
    """
    parts = shapely.get_parts(gdf.geometry.to_numpy())
    parts = parts[~shapely.is_empty(parts)]
    counts = shapely.get_num_coordinates(parts)
    coords = shapely.get_coordinates(parts)
    stacked = np.full((len(coords) + len(parts), 2), np.nan)
    is_coordinate = np.ones(len(stacked), dtype=bool)
    is_coordinate[np.cumsum(counts + 1) - 1] = False  # the NaN row after each part
    stacked[is_coordinate] = coords
    return stacked[:, 0], stacked[:, 1]


def explore_network(name, *, roads=True, clip=None, map_style="open-street-map"):
    """Interactive Plotly map of one product (pan, zoom, hover) on an OpenStreetMap basemap, no API key.

    Every edge is drawn, one trace per ``source`` coloured with the tab10 palette
    as in the viewer: for the inferred products the dense OSM road mesh *is* the
    distribution network, drawn underneath the transmission / backbone / anchor
    layers. Only substation and generator buses are marked, with their
    attributes on hover. ``roads=False`` hides the OSM mesh for a power-only
    view; ``clip`` restricts the map to a ``(minx, miny, maxx, maxy)`` box.
    """
    import plotly.graph_objects as go

    node_layer, edge_layer = load_layers(name)
    edge_layer = _clip(edge_layer, clip)
    node_layer = _clip(node_layer, clip)

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
            fig.add_trace(
                go.Scattermap(
                    lon=lons,
                    lat=lats,
                    mode="lines",
                    name=src,
                    line={"width": width, "color": colour[src]},
                    hoverinfo="skip",
                )
            )
    elif len(edge_layer):
        lons, lats = _line_coords(edge_layer)
        fig.add_trace(
            go.Scattermap(lon=lons, lat=lats, mode="lines", name="edges", line={"width": 2}, hoverinfo="skip")
        )

    # Mark only the meaningful buses (substation / generator); the road/junction
    # vertices stay as line geometry, as in the viewer.
    node_styles = {"substation": ("#111827", 8), "generator": ("#7c3aed", 11)}
    if "kind" in node_layer.columns:
        kinds = node_layer["kind"].astype("string")
        cols = [
            c for c in ("bus_id", "name", "kind", "source", "v_nom_kv", "model_v_nom_kv") if c in node_layer.columns
        ]
        for kind_val, (colour_hex, size) in node_styles.items():
            grp = node_layer[kinds.eq(kind_val)]
            if grp.empty:
                continue
            hover = (
                grp[cols].apply(lambda r: "<br>".join(f"{c}: {r[c]}" for c in cols if pd.notna(r[c])), axis=1)
                if cols
                else None
            )
            fig.add_trace(
                go.Scattermap(
                    lon=grp.geometry.x,
                    lat=grp.geometry.y,
                    mode="markers",
                    name=f"{kind_val} bus",
                    marker={"size": size, "color": colour_hex},
                    text=hover,
                    hoverinfo="text" if cols else "skip",
                )
            )

    bounds = (edge_layer if len(edge_layer) else node_layer).total_bounds
    center = {"lon": float((bounds[0] + bounds[2]) / 2), "lat": float((bounds[1] + bounds[3]) / 2)}
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1e-3)
    zoom = min(max(math.log2(360 / span) - 1, 3), 12)
    fig.update_layout(
        map={"style": map_style, "center": center, "zoom": zoom},
        margin={"l": 0, "r": 0, "t": 30, "b": 0},
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        title=name,
        height=650,
    )
    return fig
