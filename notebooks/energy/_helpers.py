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
- ``NETWORKS_DIR``    <pack>/networks/<network>   PyPSA network, metadata, GeoParquet layers, generators.csv,
  lines.csv, validation.json

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

Criticality (``02_criticality.ipynb``):

- ``CRITICALITY_DIR``: <data>/results/energy/<date>-results/criticality/inferred-provided-<region>.
- ``load_criticality()``: ``(summary, by_area)``, the two tables of ``build_energy_criticality``.
- ``demand_levels()``: each island's peak and average demand, with its source.
- ``area_demand(summary)``: each substation area's share of its island's demand, and its demand in MW.
- ``unmet_by_cause(summary, by_area)``: the unmet demand when each component fails, per day and at peak, and at
  peak by cause (own area, areas cut off, areas partly supplied).
- ``plot_criticality_map(summary, quantity)``: transmission lines and substations, coloured by the unmet demand
  when each fails, in one of ``QUANTITIES``.
- ``plot_failure(asset_id)``: the distribution network left without supply when one component fails.
- ``plot_line_ratings()``: the transmission line ratings the analysis used, by design voltage and number of
  circuits.
"""

from __future__ import annotations

import functools
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
from matplotlib import patheffects
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D

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
    """Return a network's validation report (``NETWORKS_DIR/<name>/validation.json``), or ``{}`` if absent."""
    path = NETWORKS_DIR / name / "validation.json"
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


# --- Criticality ------------------------------------------------------------------

CRITICALITY_NETWORK = f"inferred-provided-{REGION_SLUG}"
CRITICALITY_DIR = energy_paths.results_energy_dir(DATA_ROOT) / "criticality" / CRITICALITY_NETWORK
RUN_CRITICALITY = build_command(CRITICALITY_DIR / "criticality.csv")
DEMAND_DIR = energy_paths.processed_energy_dir(DATA_ROOT) / "demand" / CRITICALITY_NETWORK

# Colours: a blue scale for amounts, greys for context and for assets whose failure leaves no demand unmet,
# red for the failed asset.
_SCALE = LinearSegmentedColormap.from_list("unmet", ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
_CONTEXT, _NO_LOSS, _TEXT, _FAILED = "#dddbd5", "#a3a29c", "#52514e", "#d03b3b"
_CLASS_COLOURS = ("#2a78d6", "#eb6834", "#1baf7a")  # one per design voltage
_HALO = [patheffects.withStroke(linewidth=3, foreground="white")]  # keeps labels readable over lines


def load_criticality() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(summary, by_area)``: ``criticality.csv``, one row per asset, and ``criticality_by_area.csv``."""
    summary = pd.read_csv(CRITICALITY_DIR / "criticality.csv")
    summary["lines"] = summary["lines"].fillna("")
    return summary, pd.read_csv(CRITICALITY_DIR / "criticality_by_area.csv")


@functools.lru_cache(maxsize=1)
def _criticality_layers() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    return load_layers(CRITICALITY_NETWORK)


@functools.lru_cache(maxsize=1)
def _node_areas() -> pd.Series:
    """Return the substation area (``area_bus_id``) of every node, by ``bus_id``."""
    return pd.read_csv(DEMAND_DIR / "service_weights_nodes.csv").set_index("bus_id")["area_bus_id"]


def _area_name(area_bus_id: str, summary: pd.DataFrame) -> str:
    """Return an area's name: its substation's, or on an island without substations, its power station's."""
    names = summary.set_index("asset_id")["name"]
    return str(names.get(area_bus_id.removeprefix("bus::"), area_bus_id.removeprefix("asset::")))


def _route_of_line(summary: pd.DataFrame) -> dict[str, str]:
    """Return the line asset (route) of each transmission line segment."""
    lines = summary[summary["asset_type"].eq("line")]
    return {
        segment: asset for asset, segments in zip(lines["asset_id"], lines["lines"]) for segment in segments.split()
    }


def _draw(frame: gpd.GeoDataFrame, ax, **style) -> None:
    """Plot ``frame`` on ``ax`` unless it is empty (geopandas cannot plot an empty layer)."""
    if len(frame):
        frame.plot(ax=ax, **style)


def _island_bounds(islands, margin: float = 0.01) -> tuple[float, float, float, float]:
    """Return a ``(minx, miny, maxx, maxy)`` box around all the network nodes on ``islands``."""
    nodes, _ = _criticality_layers()
    minx, miny, maxx, maxy = nodes[nodes["region"].isin(list(islands))].total_bounds
    return (minx - margin, miny - margin, maxx + margin, maxy + margin)


def _transmission(edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return edges[edges["source"].eq("provided_transmission")]


def _dot(colour: str, label: str, size: float = 8) -> Line2D:
    return Line2D(
        [], [], marker="o", linestyle="", markersize=size, markerfacecolor=colour, markeredgecolor="white", label=label
    )


# The two quantities of the criticality tables, with the words used for them in titles and colour bars.
QUANTITIES = {
    "unmet_mwh_per_day": ("per day of outage", "MWh per day"),
    "unmet_peak_mw": ("at peak demand", "MW"),
}


@functools.lru_cache(maxsize=1)
def _area_demand_mw() -> pd.DataFrame:
    """Return each substation area's island, share of its island's demand, and demand at peak and average (MW).

    Indexed by ``bus_id``: ``service_weights_substations.csv`` times ``demand_levels.csv``, both in ``DEMAND_DIR``.
    """
    weights = pd.read_csv(DEMAND_DIR / "service_weights_substations.csv").set_index("bus_id")
    levels = pd.read_csv(DEMAND_DIR / "demand_levels.csv").pivot(index="island", columns="level", values="demand_mw")
    return pd.DataFrame(
        {
            "island": weights["island"],
            "share_of_island": weights["service_weight"],
            "peak_mw": weights["service_weight"] * weights["island"].map(levels["peak"]),
            "average_mw": weights["service_weight"] * weights["island"].map(levels["average"]),
        }
    )


def demand_levels() -> pd.DataFrame:
    """Return each island's peak and average demand (MW) and its source: ``demand_levels.csv`` in ``DEMAND_DIR``."""
    return pd.read_csv(DEMAND_DIR / "demand_levels.csv")


def area_demand(summary: pd.DataFrame) -> pd.DataFrame:
    """Return each substation area's share of its island's demand and its demand at peak and average (MW)."""
    table = _area_demand_mw().reset_index()
    table.insert(0, "area", [_area_name(area, summary) for area in table.pop("bus_id")])
    return table.sort_values(["island", "peak_mw"], ascending=[True, False]).reset_index(drop=True)


def unmet_by_cause(summary: pd.DataFrame, by_area: pd.DataFrame) -> pd.DataFrame:
    """Return, for each asset whose failure leaves demand unmet, its two unmet quantities and where the unmet
    demand at peak falls.

    ``own_area_peak_mw``: a failed substation's own area. ``cut_off`` and ``cut_off_peak_mw``: the other areas
    that lose all their supply. ``partly_supplied_peak_mw``: unmet demand in areas that keep some supply, because
    the plants left cannot cover them or lines reach their ratings. ``line_limits_peak_mw``: the part of
    ``unmet_peak_mw`` due to line ratings. Raises ValueError if the parts do not add up to ``unmet_peak_mw``.
    """
    area_peak = _area_demand_mw()["peak_mw"]
    rows = []
    for asset_id, asset in summary.set_index("asset_id").iterrows():
        unmet = float(asset["unmet_peak_mw"])
        if unmet <= 0 and asset["unmet_mwh_per_day"] <= 0:
            continue
        areas = by_area[by_area["asset_id"].eq(asset_id)]
        own = areas["area_bus_id"].eq(f"bus::{asset_id}") & (asset["asset_type"] == "substation")
        whole = areas["unmet_peak_mw"].ge(0.999 * areas["area_bus_id"].map(area_peak)) & ~own
        parts = {
            "own_area_peak_mw": areas.loc[own, "unmet_peak_mw"].sum(),
            "cut_off_peak_mw": areas.loc[whole, "unmet_peak_mw"].sum(),
            "partly_supplied_peak_mw": areas.loc[~own & ~whole, "unmet_peak_mw"].sum(),
        }
        if abs(sum(parts.values()) - unmet) > 0.01:
            raise ValueError(f"{asset_id}: the areas add up to {sum(parts.values()):.3f} MW, not {unmet:.3f} MW")
        rows.append(
            {
                "asset_id": asset_id,
                "asset": asset["name"],
                "type": asset["asset_type"],
                "unmet_mwh_per_day": float(asset["unmet_mwh_per_day"]),
                "unmet_peak_mw": unmet,
                "own_area_peak_mw": parts["own_area_peak_mw"],
                "cut_off": ", ".join(_area_name(area, summary) for area in areas.loc[whole, "area_bus_id"]),
                "cut_off_peak_mw": parts["cut_off_peak_mw"],
                "partly_supplied_peak_mw": parts["partly_supplied_peak_mw"],
                "line_limits_peak_mw": float(asset["line_limits_peak_mw"]),
            }
        )
    table = pd.DataFrame(rows).sort_values(["unmet_mwh_per_day", "unmet_peak_mw"], ascending=False)
    return table.reset_index(drop=True)


def plot_criticality_map(
    summary: pd.DataFrame, quantity: str = "unmet_mwh_per_day", *, clip=None, ax=None, figsize=(9, 11)
):
    """Map the transmission lines and substations, coloured by the unmet demand when each fails.

    ``quantity``: ``"unmet_mwh_per_day"`` or ``"unmet_peak_mw"`` (``QUANTITIES``). Plants are not drawn: they sit
    at substations. ``clip``: a ``(minx, miny, maxx, maxy)`` box in degrees; the default is the islands with
    transmission lines.
    """
    what, unit = QUANTITIES[quantity]
    nodes, edges = _criticality_layers()
    unmet = summary.set_index("asset_id")[quantity]
    ceb = _transmission(edges).copy()
    ceb["unmet"] = ceb["line_id"].map(_route_of_line(summary)).map(unmet).fillna(0.0)
    subs = nodes[nodes["kind"].eq("substation")].copy()
    substation = subs["bus_id"].str.removeprefix("bus::")  # the asset_id of the criticality tables
    subs["unmet"] = substation.map(unmet).fillna(0.0)
    subs["name"] = substation.map(summary.set_index("asset_id")["name"]).fillna(substation)
    clip = clip or _island_bounds(set(ceb["region"]))
    roads, ceb, subs = (_clip(frame, clip) for frame in (edges[edges["source"].eq("osm")], ceb, subs))
    norm = Normalize(0, max(ceb["unmet"].max(), subs["unmet"].max(), 1.0))
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    _draw(roads, ax, color=_CONTEXT, linewidth=0.2, zorder=1)
    hit, sub_hit = ceb["unmet"].round(1) > 0, subs["unmet"].round(1) > 0
    _draw(ceb[~hit], ax, color=_NO_LOSS, linewidth=1.2, zorder=2)
    if hit.any():
        _draw(ceb[hit], ax, color=[_SCALE(norm(value)) for value in ceb.loc[hit, "unmet"]], linewidth=3, zorder=3)
    colours = [_SCALE(norm(value)) if drawn else _NO_LOSS for value, drawn in zip(subs["unmet"], sub_hit)]
    ax.scatter(subs.geometry.x, subs.geometry.y, s=70, c=colours, edgecolor="white", linewidth=1.5, zorder=4)
    for name, point in zip(subs["name"], subs.geometry):
        ax.annotate(
            name,
            (point.x, point.y),
            xytext=(6, 4),
            textcoords="offset points",
            fontsize=8,
            color=_TEXT,
            path_effects=_HALO,
        )
    plt.colorbar(
        ScalarMappable(norm, _SCALE), ax=ax, shrink=0.4, label=f"Unmet demand when the component fails ({unit})"
    )
    entries = [
        (hit.any(), Line2D([], [], color=_SCALE(0.6), linewidth=3, label="transmission line, unmet demand")),
        ((~hit).any(), Line2D([], [], color=_NO_LOSS, linewidth=1.2, label="transmission line, no unmet demand")),
        (sub_hit.any(), _dot(_SCALE(0.6), "substation, unmet demand")),
        ((~sub_hit).any(), _dot(_NO_LOSS, "substation, no unmet demand")),
        (len(roads) > 0, Line2D([], [], color=_CONTEXT, linewidth=1, label="estimated distribution network")),
    ]
    ax.legend(handles=[entry for drawn, entry in entries if drawn], loc="upper left", frameon=False, fontsize=8)
    ax.set_axis_off()
    ax.set_title(f"Unmet demand when each component fails: {what}", loc="left")
    return ax


def plot_failure(
    asset_id: str,
    *,
    summary: pd.DataFrame | None = None,
    by_area: pd.DataFrame | None = None,
    clip=None,
    ax=None,
    figsize=(9, 11),
):
    """Map the distribution lines left without supply when ``asset_id`` fails.

    Each line is coloured by the share of its substation area's peak demand that is unmet; a line between two
    areas takes the smaller share of its two ends. A box gives the unmet demand and, at peak, its causes (see
    :func:`unmet_by_cause`). ``clip``: a ``(minx, miny, maxx, maxy)`` box in degrees; the default is the
    component's island.
    """
    if summary is None or by_area is None:
        summary, by_area = load_criticality()
    asset = summary.set_index("asset_id").loc[asset_id]
    nodes, edges = _criticality_layers()
    hit = by_area[by_area["asset_id"].eq(asset_id)]
    share = (hit["unmet_peak_mw"] / hit["area_bus_id"].map(_area_demand_mw()["peak_mw"])).clip(upper=1.0)
    share.index = hit["area_bus_id"]
    areas = _node_areas()
    lines = edges[~edges["source"].eq("provided_transmission")].copy()
    ends = [lines[end].map(areas).map(share).fillna(0.0) for end in ("bus0", "bus1")]
    lines["share"] = np.minimum(*ends)
    clip = clip or _island_bounds([asset["island"]])
    lines = _clip(lines, clip)
    ceb = _transmission(edges)
    subs = _clip(nodes[nodes["kind"].eq("substation")], clip)

    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    unserved = lines["share"] > 0
    _draw(lines[~unserved], ax, color=_CONTEXT, linewidth=0.3, zorder=1)
    _draw(lines[unserved], ax, color=[_SCALE(value) for value in lines.loc[unserved, "share"]], linewidth=0.7, zorder=2)
    _draw(_clip(ceb, clip), ax, color=_NO_LOSS, linewidth=1.0, zorder=3)
    ax.scatter(subs.geometry.x, subs.geometry.y, s=25, color=_NO_LOSS, edgecolor="white", linewidth=1, zorder=4)
    if asset["asset_type"] == "line":
        failed = ceb[ceb["line_id"].isin(asset["lines"].split())]
        _draw(failed, ax, color=_FAILED, linewidth=3, zorder=5)
        where = failed.geometry.union_all().representative_point()
        failed_key = Line2D([], [], color=_FAILED, linewidth=3, label="failed line")
    else:
        bus_id = f"bus::{asset_id}" if asset["asset_type"] == "substation" else f"asset::{asset_id}"
        where = nodes.set_index("bus_id").geometry[bus_id]
        ax.scatter([where.x], [where.y], s=110, color=_FAILED, edgecolor="white", linewidth=1.5, zorder=5)
        failed_key = _dot(_FAILED, f"failed {asset['asset_type']}", size=10)
    ax.annotate(
        asset["name"], (where.x, where.y), xytext=(8, -12), textcoords="offset points", color=_TEXT, path_effects=_HALO
    )
    if unserved.any():
        plt.colorbar(
            ScalarMappable(Normalize(0, 1), _SCALE), ax=ax, shrink=0.4, label="Share of the area's peak demand unmet"
        )
    key = [failed_key, Line2D([], [], color=_CONTEXT, linewidth=1, label="distribution line, supplied")]
    if len(_clip(ceb, clip)):
        key.append(Line2D([], [], color=_NO_LOSS, linewidth=1, label="transmission line"))
    if len(subs):
        key.append(_dot(_NO_LOSS, "substation", size=6))
    ax.legend(handles=key, loc="lower left", frameon=False, fontsize=8)
    ax.text(0.01, 0.99, _unmet_breakdown(asset_id, asset, summary, by_area), transform=ax.transAxes, va="top")
    ax.set_axis_off()
    ax.set_title(
        f"Distribution network without supply when one component fails:\n{asset['name']} ({asset['asset_type']})",
        loc="left",
    )
    return ax


def _unmet_breakdown(asset_id, asset, summary, by_area) -> str:
    """Return the unmet demand when ``asset_id`` fails, and at peak its causes (own area, cut off, partly supplied)."""
    peak = float(asset["unmet_peak_mw"])
    lines = [
        f"Unmet demand per day of outage: {asset['unmet_mwh_per_day']:,.0f} MWh",
        f"Unmet demand at peak: {peak:.1f} MW",
    ]
    if peak <= 0:
        return "\n".join(lines)
    why = unmet_by_cause(summary, by_area).set_index("asset_id").loc[asset_id]
    if why["own_area_peak_mw"] > 0:
        lines.append(f"  own area: {why['own_area_peak_mw']:.1f} MW")
    if why["cut_off_peak_mw"] > 0:
        lines.append(f"  areas cut off: {why['cut_off_peak_mw']:.1f} MW ({why['cut_off']})")
    if why["partly_supplied_peak_mw"] > 0:
        lines.append(f"  areas partly supplied: {why['partly_supplied_peak_mw']:.1f} MW")
    return "\n".join(lines)


def plot_line_ratings(*, clip=None, ax=None, figsize=(9, 11)):
    """Map the transmission line ratings, by design voltage (colour) and number of circuits (width).

    From ``line_ratings.csv`` of ``build_energy_criticality``, the ratings the analysis used. The legend gives
    each class's rating at the voltage the lines run at.
    ``clip``: a ``(minx, miny, maxx, maxy)`` box in degrees; the default is the islands with transmission lines.
    """
    ratings = pd.read_csv(CRITICALITY_DIR / "line_ratings.csv")
    _, edges = _criticality_layers()
    lines = _transmission(edges).merge(ratings, on="line_id", how="inner")
    clip = clip or _island_bounds(set(lines["region"]))
    lines = _clip(lines, clip)
    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    _draw(_clip(edges[edges["source"].eq("osm")], clip), ax, color=_CONTEXT, linewidth=0.2, zorder=1)
    voltages = sorted(lines["built_for_kv"].unique())
    if len(voltages) > len(_CLASS_COLOURS):
        raise ValueError(f"{len(voltages)} design voltages; colours for {len(_CLASS_COLOURS)} only")
    colour_of = dict(zip(voltages, _CLASS_COLOURS))
    for (kv, count, rating), block in lines.groupby(["built_for_kv", "circuits", "rating_mva"], sort=True):
        circuit_word = "circuit" if count == 1 else "circuits"
        label = f"{kv:g} kV, {count:g} {circuit_word}: {rating:.0f} MVA, {block['length_km'].sum():.0f} km"
        _draw(block, ax, color=colour_of[kv], linewidth=1.2 * count, label=label, zorder=2)
    running = "/".join(f"{kv:g}" for kv in sorted(lines["voltage_kv"].unique()))
    title = f"Design voltage, circuits: rating at {running} kV, length"
    ax.legend(loc="upper left", frameon=False, title=title, alignment="left")
    ax.set_axis_off()
    ax.set_title("Transmission line ratings by design voltage and number of circuits", loc="left")
    return ax
