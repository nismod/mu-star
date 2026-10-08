"""Helpers for the energy notebooks (``notebooks/energy/**``), imported as ``h``.

They find, load and map the pipeline's files; they never build or download anything, and neither the
``energy`` package nor the workflow uses them. Production maps come from the separate viewer
(nismod/irv-standalone), so notebook plotting code belongs here, not in ``src/energy/``.

Paths, read on import from ``data_root`` in ``config/config.yaml`` (default: this repository's ``data/``)
and ``energy.region`` and ``energy.model_data`` in ``config/energy/energy.yaml``. ``<pack>`` below is
``<data>/processed/energy/<model_data>``.

- ``DATA_ROOT``, ``MODEL_DATA``, ``REGION``, ``REGION_SLUG``
- ``PROVIDED_DIR``    <pack>/provided             cleaned CEB tables
- ``OSM_CACHE_DIR``   <data>/incoming/Infrastructure/Energy/OpenStreetMap/<region>  roads, power features, outline
- ``NIGHTLIGHT_DIR``  <pack>/nightlight/<region>  radiance composite and lit pixels
- ``NETWORKS_DIR``    <pack>/networks/<network>   PyPSA network, metadata, GeoParquet layers
- ``RESULTS_DIR``     <data>/results/energy/<model_data>/<network>  generators.csv, lines.csv, validation.json

Inputs (``00_inputs.ipynb``):

- ``load_provided()``, ``load_osm_cache()``: the cleaned CEB tables and OSM layers that exist.
- ``osm_paths()``, ``nightlight_paths()``, ``nightlight_monthly_tiles()``: where the input files are.
- ``load_nightlight_targets()``: the lit pixels as points.
- ``found(path, how)``: True if ``path`` exists, else prints the command ``how`` (``FETCH_OSM`` etc.).

Built networks (``01_networks.ipynb``):

- ``available_networks()``, ``list_networks()``: the built networks.
- ``load_layers(name)``: ``(nodes, edges)`` GeoDataFrames; ``power_nodes(nodes)`` drops the road nodes.
- ``load_validation(name)``, ``load_pypsa(name)``, ``summarise(name)``.
- ``explore_network(name)``: interactive Plotly map; ``plot_network(name)``, ``quick_map(**layers)``: static maps.
- ``MAURITIUS_BBOX``, ``RODRIGUES_BBOX``: boxes for the ``clip=`` argument.
"""

from __future__ import annotations

import json
import math
import shlex
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
MODEL_DATA = energy_paths.model_data()
PROVIDED_DIR = energy_paths.processed_energy_dir(DATA_ROOT) / "provided"
OSM_CACHE_DIR = DATA_ROOT / energy_osm.osm_cache_dir_relative(REGION)
NIGHTLIGHT_DIR = energy_paths.processed_energy_dir(DATA_ROOT) / "nightlight" / REGION_SLUG
NIGHTLIGHT_MONTHLY_DIR = DATA_ROOT / str(
    _setting(
        "nightlight",
        "source",
        "monthly_dir",
        default="incoming/Infrastructure/Energy/Nighttime Lights/viirs-2024-monthly",
    )
)
NETWORKS_DIR = energy_paths.network_output_dir(DATA_ROOT)
RESULTS_DIR = energy_paths.results_energy_dir(DATA_ROOT)

# Rough lon/lat boxes for zooming to one island (Mauritius and Rodrigues are about 560 km apart).
MAURITIUS_BBOX = (57.3, -20.6, 57.9, -19.9)
RODRIGUES_BBOX = (63.3, -19.8, 63.5, -19.6)

# --- Terminal commands that create the files (printed by ``found()``) ---------

BUILD_ALL = "snakemake -c1 build_energy_networks"


def build_command(path: Path) -> str:
    """Return ``snakemake -c1 <path>``, with ``path`` relative to the repository where possible."""
    path = Path(path)
    try:
        target = path.relative_to(REPO_ROOT)
    except ValueError:
        target = path
    return f"snakemake -c1 {shlex.quote(str(target))}"


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
    "generation_sites.csv",
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
    """Return the ``PROVIDED_FILES`` that exist in ``PROVIDED_DIR``, keyed by file name without extension.

    Parquet files load as GeoDataFrames and CSVs as DataFrames. The ``prepare_energy_assets`` rule
    writes them; ``PREPARE_PROVIDED`` is the command that runs it.
    """
    tables = {}
    for name in PROVIDED_FILES:
        path = PROVIDED_DIR / name
        if path.is_file():
            tables[path.stem] = _read_table(path)
    return tables


# --- Inputs: the OpenStreetMap cache ------------------------------------------


def osm_paths() -> dict[str, Path]:
    """Return the ``roads``, ``power`` and ``aoi`` paths that the ``fetch_energy_osm`` rule downloads.

    As in the workflow, a path set in ``energy.osm.roads`` or ``energy.osm.aoi`` replaces the download.
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


# Asking for the roads file downloads all three OpenStreetMap layers.
FETCH_OSM = build_command(DATA_ROOT / energy_osm.roads_cache_relative(REGION, NETWORK_TYPE))


# --- Inputs: the night lights -------------------------------------------------


def nightlight_monthly_tiles() -> list[Path]:
    """Return the monthly VIIRS tiles the workflow expects (cached or not), one per configured object id."""
    object_ids = _setting("nightlight", "source", "object_ids", default=list(range(120, 132)))
    return [NIGHTLIGHT_MONTHLY_DIR / tile_name(index, int(oid)) for index, oid in enumerate(object_ids, start=1)]


# Asking for the first tile downloads all twelve.
FETCH_NIGHTLIGHTS = build_command(nightlight_monthly_tiles()[0])


def nightlight_paths() -> dict[str, Path]:
    """Return the paths of the radiance ``composite``, the lit pixels (``targets``) and their ``metadata``.

    ``composite`` is the raster set in ``energy.nightlight.nightlights``, else the one built from the monthly tiles.
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
    """Return the lit pixels ("targets") inside the area of interest, one point each."""
    path = nightlight_paths()["targets"]
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist; run: {BUILD_NIGHTLIGHT_TARGETS}")
    return gpd.read_parquet(path)


# --- Built networks -----------------------------------------------------------


def _geoparquet_dir(name: str) -> Path:
    return NETWORKS_DIR / name / "geoparquet"


def available_networks() -> list[str]:
    """Return the names of the built networks, e.g. ``["base-mauritius", "inferred-osm-mauritius-rodrigues"]``.

    A network is built when its ``geoparquet/`` folder holds the spatial manifest and the node and edge layers.
    """
    names = []
    for manifest in sorted(NETWORKS_DIR.glob("*/geoparquet/*-spatial-manifest.json")):
        name = manifest.name.removesuffix("-spatial-manifest.json")
        layers = manifest.parent
        if (layers / f"{name}-nodes.geoparquet").is_file() and (layers / f"{name}-edges.geoparquet").is_file():
            names.append(name)
    return names


def load_manifest(name: str) -> dict:
    """Return a network's spatial manifest: layer checksums, counts, bounding boxes and the electrical-values note."""
    return json.loads((_geoparquet_dir(name) / f"{name}-spatial-manifest.json").read_text())


def load_layers(name: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Return ``(nodes, edges)`` GeoDataFrames of a built network (EPSG:4326)."""
    layers = _geoparquet_dir(name)
    nodes = gpd.read_parquet(layers / f"{name}-nodes.geoparquet")
    edges = gpd.read_parquet(layers / f"{name}-edges.geoparquet")
    return nodes, edges


def load_validation(name: str) -> dict:
    """Return a network's validation report (``RESULTS_DIR/<name>/validation.json``), or ``{}`` if absent."""
    path = RESULTS_DIR / name / "validation.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load_pypsa(name: str):
    """Return a network's PyPSA object (``pypsa`` is imported here so the other helpers do not need it)."""
    import pypsa

    return pypsa.Network(str(NETWORKS_DIR / name / f"{name}.nc"))


def summarise(name: str) -> dict:
    """Return a network's node and edge counts, line length (km), CRS, column names and manifest notes."""
    nodes, edges = load_layers(name)
    manifest = load_manifest(name)
    return {
        "network": name,
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


def list_networks() -> pd.DataFrame:
    """Return a table of the built networks: name, whether inferred, counts, line length (km), validation status."""
    rows = []
    for name in available_networks():
        manifest = load_manifest(name)
        totals = manifest.get("totals", {})
        rows.append(
            {
                "network": name,
                "inferred": manifest.get("inferred"),
                "nodes": totals.get("nodes"),
                "edges": totals.get("edges"),
                "line_length_km": round(float(totals.get("line_length_km", float("nan"))), 1),
                "validation": load_validation(name).get("status", "-"),
            }
        )
    return pd.DataFrame(rows, columns=["network", "inferred", "nodes", "edges", "line_length_km", "validation"])


def power_nodes(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the nodes not marked ``is_inferred``, or all nodes if there are none.

    In an inferred network this drops the road nodes and transformer buses, leaving substations and generators.
    """
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

    Each keyword is a legend label and a GeoDataFrame or ``(GeoDataFrame, style)``, where ``style`` holds
    matplotlib keyword arguments. ``None`` layers are skipped. Polygons are drawn as outlines, points on
    top. ``clip`` is a ``(minx, miny, maxx, maxy)`` box such as ``MAURITIUS_BBOX``.
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
    nodes="power",
    node_color_by="kind",
    edge_color_by="source",
    node_size=None,
    ax=None,
    title=None,
    figsize=(9, 11),
    clip=None,
):
    """Draw a static map of one network's edges and nodes.

    ``nodes``: ``"power"`` (see :func:`power_nodes`), ``"all"`` or ``"none"``. ``clip``: a
    ``(minx, miny, maxx, maxy)`` box to zoom to, such as ``MAURITIUS_BBOX``.
    """
    node_layer, edge_layer = load_layers(name)
    edge_layer = _clip(edge_layer, clip)
    node_layer = _clip(node_layer, clip)
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)

    # Fewer than 500 edges with several edge_color_by values: colour by it. Otherwise thin grey.
    few_edges = (
        edge_color_by in edge_layer.columns
        and len(edge_layer) < 500
        and edge_layer[edge_color_by].nunique(dropna=True) > 1
    )
    if few_edges:
        edge_layer.plot(ax=ax, column=edge_color_by, legend=True, categorical=True, linewidth=0.8)
    else:
        edge_layer.plot(ax=ax, color="0.75", linewidth=0.4)

    selection = {"power": power_nodes(node_layer), "all": node_layer}.get(nodes)
    if selection is not None and len(selection):
        size = node_size if node_size is not None else (36 if len(selection) < 500 else 6)
        # With grey edges, colour the nodes by kind so the legend shows them.
        if not few_edges and node_color_by in selection.columns and selection[node_color_by].nunique(dropna=True) > 1:
            selection.plot(ax=ax, column=node_color_by, categorical=True, legend=True, markersize=size, zorder=5)
        else:
            selection.plot(ax=ax, color="crimson", markersize=size, edgecolor="white", linewidth=0.3, zorder=5)

    shown = 0 if selection is None else len(selection)
    ax.set_axis_off()
    ax.set_title(title or f"{name}\n{len(edge_layer)} edges · {shown} nodes shown ({nodes})")
    return ax


def _line_coords(gdf: gpd.GeoDataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(lon, lat)`` arrays of every line part in ``gdf``, with a NaN after each part.

    Plotly breaks the line at a NaN, so one trace can draw a whole layer. Missing and empty
    geometries are skipped.
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
    """Return an interactive Plotly map of one network on an OpenStreetMap basemap (no API key needed).

    One trace per edge ``source`` in the viewer's tab10 colours, with the OSM roads drawn first, underneath.
    Substation and generator buses are marked, with their attributes on hover. ``roads=False`` hides the
    OSM roads; ``clip`` is a ``(minx, miny, maxx, maxy)`` box.
    """
    import plotly.graph_objects as go

    node_layer, edge_layer = load_layers(name)
    edge_layer = _clip(edge_layer, clip)
    node_layer = _clip(node_layer, clip)

    fig = go.Figure()
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

    # Mark only substation and generator buses, as the viewer does.
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
