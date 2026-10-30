"""Fetch and cache OpenStreetMap roads, power features and region outlines (AOI).

Each is downloaded once, from Overpass and Nominatim, to::

    <data_root>/incoming/Infrastructure/Energy/OpenStreetMap/<region>/roads.parquet
    <data_root>/incoming/Infrastructure/Energy/OpenStreetMap/<region>/power.parquet
    <data_root>/incoming/Infrastructure/Energy/OpenStreetMap/<region>/aoi.parquet

The ``fetch_energy_osm`` rule downloads missing files. From Python, a missing
file raises :class:`OSMDownloadRequired` unless ``allow_download=True``.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from energy.paths import INCOMING_ENERGY_RELATIVE
from energy.paths import data_root as configured_data_root

GEOGRAPHIC_CRS = "EPSG:4326"


class OSMDownloadRequired(RuntimeError):
    """Raised when an OSM file is missing and ``allow_download`` is False."""


# Region keys and their Nominatim queries; any other string is queried as written.
# "mauritius" is the main island only; the query "Mauritius" would add the outer islands.
REGIONS: dict[str, str] = {
    "mauritius": "Mauritius Island, Mauritius",
    "rodrigues": "Rodrigues, Mauritius",
    "agalega": "Agalega, Mauritius",
    "st_brandon": "Saint Brandon, Mauritius",
}

# A group is fetched one member at a time and combined into one file.
REGION_GROUPS: dict[str, tuple[str, ...]] = {
    "mauritius-rodrigues": ("mauritius", "rodrigues"),
}


def region_query(region: str) -> str:
    """Return the Nominatim query for ``region``: its ``REGIONS`` entry, or the string as given."""
    return REGIONS.get(region.strip().lower(), region.strip())


def region_members(region: str) -> tuple[str, ...]:
    """Return the members of a group in ``REGION_GROUPS``, or ``(region,)``."""
    normalised = region.strip().lower()
    return REGION_GROUPS.get(normalised, (region.strip(),))


def region_slug(region: str) -> str:
    """Return a file-name-safe key for ``region``, used in cache folders and network names.

    Group names are kept as they are; ``"Rodrigues, Mauritius"`` becomes ``rodrigues_mauritius``.
    """
    normalised = region.strip().lower()
    if normalised in REGION_GROUPS:
        return normalised
    slug = re.sub(r"[^a-z0-9]+", "_", normalised).strip("_")
    return slug or "region"


def _require_region(region: str) -> str:
    if not str(region).strip():
        raise ValueError("region must be a non-empty OSM/Nominatim query")
    return str(region).strip()


# --- Cache locations ----------------------------------------------------------
# *_relative: paths relative to the data root, which the workflow rules prefix with {data}.
# osm_*_path: the same paths under the data root, for Python callers.


def osm_cache_dir_relative(region: str) -> Path:
    return INCOMING_ENERGY_RELATIVE / "OpenStreetMap" / region_slug(region)


def roads_cache_relative(region: str, network_type: str = "drive") -> Path:
    suffix = "" if network_type == "drive" else f"-{region_slug(network_type)}"
    return osm_cache_dir_relative(region) / f"roads{suffix}.parquet"


def power_cache_relative(region: str) -> Path:
    return osm_cache_dir_relative(region) / "power.parquet"


def aoi_cache_relative(region: str) -> Path:
    return osm_cache_dir_relative(region) / "aoi.parquet"


def _data_root_or_default(data_root: Path | None) -> Path:
    return Path(data_root) if data_root is not None else configured_data_root()


def osm_roads_path(region: str, network_type: str = "drive", data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / roads_cache_relative(region, network_type)


def osm_power_path(region: str, data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / power_cache_relative(region)


def osm_aoi_path(region: str, data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / aoi_cache_relative(region)


# --- Reading and tidying ------------------------------------------------------


def read_vector(path: Path) -> gpd.GeoDataFrame:
    """Read GeoParquet (``.parquet``, ``.geoparquet``, ``.gpq``, ``.pq``) or any vector file GDAL reads."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Vector file does not exist: {path}")
    if path.suffix.lower() in {".parquet", ".geoparquet", ".gpq", ".pq"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def deduplicate_two_way_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop repeated road geometries, counting a line and its reverse as the same road.

    osmnx stores a two-way street twice, in opposite directions; keeping both doubles its length.
    """
    if roads.empty:
        return roads
    forward = shapely.to_wkb(roads.geometry.to_numpy())
    backward = shapely.to_wkb(shapely.reverse(roads.geometry.to_numpy()))
    keys = [min(a, b) for a, b in zip(forward, backward, strict=True)]
    keep = ~pd.Series(keys, index=roads.index).duplicated()
    return roads.loc[keep].reset_index(drop=True)


def _primary_highway_class(value: object) -> str | None:
    """Return an OSM ``highway`` tag as one lower-case string, or None if missing.

    osmnx gives a list for an edge merged from several ways; the first entry is used.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().lower()
    return text or None


def _empty_roads() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"source": [], "region": [], "highway": [], "geometry": []},
        geometry="geometry",
        crs=GEOGRAPHIC_CRS,
    )


# OSM tags kept on power features (tag -> column). Free text, used to match plants
# to the CEB annual report by name.
POWER_FEATURE_TAGS = {
    "name": "name",
    "operator": "operator",
    "plant:source": "plant_source",
    "plant:output:electrical": "plant_output_electrical",
    "voltage": "voltage",
}


def _empty_power_features() -> gpd.GeoDataFrame:
    columns = {"source": [], "region": [], "bus_id": [], "power": []}
    columns.update({column: [] for column in POWER_FEATURE_TAGS.values()})
    columns["geometry"] = []
    return gpd.GeoDataFrame(columns, geometry="geometry", crs=GEOGRAPHIC_CRS)


def _download_help(what: str, region: str, path: Path) -> str:
    return (
        f"OSM {what} for {region!r} are not cached at {path}.\n"
        "The fetch_energy_osm workflow rule downloads them (needs internet); from "
        "Python, call this function with allow_download=True."
    )


def _configure_osmnx():
    import osmnx as ox  # only needed for downloads

    # osmnx's download cache goes in the system temp folder, not the (possibly shared) data folders.
    ox.settings.cache_folder = str(Path(tempfile.gettempdir()) / "mu-star-osmnx-cache")
    try:
        from osmnx._errors import InsufficientResponseError
    except Exception:  # pragma: no cover - version-dependent import
        InsufficientResponseError = Exception  # type: ignore[assignment]
    return ox, InsufficientResponseError


def _resolve_cache(
    *,
    what: str,
    region: str,
    default_path: Path,
    path: Path | None,
    overwrite: bool,
) -> tuple[Path, bool]:
    """Return ``(path, available)``. A given ``path`` must exist; it is not downloaded or overwritten."""
    if path is not None:
        explicit = Path(path)
        if explicit.is_file():
            return explicit, True
        raise FileNotFoundError(
            f"Configured {what} file {explicit} does not exist; "
            "a user-supplied path is never downloaded or overwritten."
        )
    return default_path, default_path.is_file() and not overwrite


# --- Fetchers -----------------------------------------------------------------


def fetch_osm_roads(
    region: str,
    *,
    network_type: str = "drive",
    overwrite: bool = False,
    allow_download: bool = False,
    path: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    """Return the path of the cached OSM roads (LineStrings) for ``region``, downloading if allowed.

    ``network_type="drive"`` is trunk to residential roads and links; ``"all"`` adds footpaths and
    tracks, which the estimated network should not follow. Roads keep their ``highway`` class; two-way
    streets are stored once. ``path`` replaces the cache file.
    """
    region = _require_region(region)
    target, available = _resolve_cache(
        what="roads",
        region=region,
        default_path=osm_roads_path(region, network_type, data_root),
        path=path,
        overwrite=overwrite,
    )
    if available:
        return target

    members = region_members(region)
    if len(members) > 1:
        member_paths = [
            fetch_osm_roads(
                member,
                network_type=network_type,
                overwrite=overwrite,
                allow_download=allow_download,
                data_root=data_root,
            )
            for member in members
        ]
        roads = gpd.GeoDataFrame(
            pd.concat([gpd.read_parquet(member_path) for member_path in member_paths], ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        roads.to_parquet(target)
        return target
    if not allow_download:
        raise OSMDownloadRequired(_download_help("roads", region, target))

    ox, InsufficientResponseError = _configure_osmnx()
    try:
        graph = ox.graph_from_place(region_query(region), network_type=network_type)
        # osmnx graphs hold both directions of a two-way road; keep one.
        graph = ox.convert.to_undirected(graph)
        edges = ox.graph_to_gdfs(graph, nodes=False).reset_index()
        roads = edges[["geometry"]].copy()
        roads["source"] = "osm_roads"
        roads["region"] = region_slug(region)
        roads["highway"] = edges["highway"].map(_primary_highway_class) if "highway" in edges else None
        roads = deduplicate_two_way_roads(roads[["source", "region", "highway", "geometry"]])
    except InsufficientResponseError:
        roads = _empty_roads()

    target.parent.mkdir(parents=True, exist_ok=True)
    roads.to_parquet(target)
    return target


def fetch_osm_power_features(
    region: str,
    *,
    overwrite: bool = False,
    allow_download: bool = False,
    path: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    """Return the path of the cached OSM substations, plants and generators, downloading if allowed.

    Each is a point (its centroid, EPSG:4326) with id ``<REGION>_SUB_<nnn>`` and the
    ``POWER_FEATURE_TAGS``. A group file is written only once every member is available.
    """
    region = _require_region(region)
    target, available = _resolve_cache(
        what="power features",
        region=region,
        default_path=osm_power_path(region, data_root),
        path=path,
        overwrite=overwrite,
    )
    if available:
        return target

    members = region_members(region)
    if len(members) > 1:
        member_paths = [
            fetch_osm_power_features(
                member,
                overwrite=overwrite,
                allow_download=allow_download,
                data_root=data_root,
            )
            for member in members
        ]
        power = gpd.GeoDataFrame(
            pd.concat([gpd.read_parquet(member_path) for member_path in member_paths], ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        power.to_parquet(target)
        return target
    if not allow_download:
        raise OSMDownloadRequired(_download_help("power features", region, target))

    slug = region_slug(region)
    ox, InsufficientResponseError = _configure_osmnx()
    try:
        features = ox.features_from_place(
            region_query(region),
            tags={"power": ["substation", "plant", "generator"]},
        )
        features = features[features.geometry.notna()].reset_index(drop=True) if not features.empty else features
        if features.empty:
            power = _empty_power_features()
        else:
            if features.crs is None:
                features = features.set_crs(GEOGRAPHIC_CRS)
            metric = features.to_crs(features.estimate_utm_crs())
            power_values = features["power"].astype(str).to_numpy() if "power" in features else [""] * len(metric)
            columns = {
                "source": "osm_power",
                "region": slug,
                "bus_id": [f"{slug.upper()}_SUB_{number:03d}" for number in range(1, len(metric) + 1)],
                "power": power_values,
            }
            for tag, column in POWER_FEATURE_TAGS.items():
                columns[column] = (
                    features[tag].astype("string").to_numpy() if tag in features else pd.array([pd.NA] * len(metric))
                )
            power = gpd.GeoDataFrame(
                columns,
                geometry=metric.geometry.centroid.reset_index(drop=True),
                crs=metric.crs,
            ).to_crs(GEOGRAPHIC_CRS)
    except InsufficientResponseError:
        power = _empty_power_features()

    target.parent.mkdir(parents=True, exist_ok=True)
    power.to_parquet(target)
    return target


def fetch_osm_aoi(
    region: str,
    *,
    overwrite: bool = False,
    allow_download: bool = False,
    path: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    """Return the path of the cached Nominatim outline (AOI) of ``region``, downloading if allowed.

    One row per region member, with the query and retrieval time; used to clip the night-light raster.
    """
    region = _require_region(region)
    target, available = _resolve_cache(
        what="area of interest",
        region=region,
        default_path=osm_aoi_path(region, data_root),
        path=path,
        overwrite=overwrite,
    )
    if available:
        return target

    members = region_members(region)
    if len(members) > 1:
        member_paths = [
            fetch_osm_aoi(member, overwrite=overwrite, allow_download=allow_download, data_root=data_root)
            for member in members
        ]
        aoi = gpd.GeoDataFrame(
            pd.concat([gpd.read_parquet(member_path) for member_path in member_paths], ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        aoi.to_parquet(target)
        return target
    if not allow_download:
        raise OSMDownloadRequired(_download_help("area of interest", region, target))

    ox, _ = _configure_osmnx()
    geocoded = ox.geocode_to_gdf(region_query(region)).to_crs(GEOGRAPHIC_CRS)
    aoi = gpd.GeoDataFrame(
        {
            "source": ["osm_nominatim"] * len(geocoded),
            "region": [region_slug(region)] * len(geocoded),
            "source_query": [region_query(region)] * len(geocoded),
            "retrieved_at_utc": [datetime.now(timezone.utc).isoformat(timespec="minutes")] * len(geocoded),
            "osmnx_version": [str(ox.__version__)] * len(geocoded),
        },
        geometry=geocoded.geometry.reset_index(drop=True),
        crs=GEOGRAPHIC_CRS,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    aoi.to_parquet(target)
    return target
