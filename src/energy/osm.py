"""Fetch and cache OpenStreetMap inputs for a region: roads, power features and the area of interest.

The inferred distribution network follows OpenStreetMap roads, uses OSM power
features (substations, plants, generators) as connection points, and clips the
night-light raster to the region's outline. All three come from the OSM
Overpass and Nominatim services, so they are fetched **once** and cached under::

    <data_root>/incoming/energy/osm/<region>/roads.parquet
    <data_root>/incoming/energy/osm/<region>/power.parquet
    <data_root>/incoming/energy/osm/<region>/aoi.parquet

Nothing here downloads unless you pass ``allow_download=True`` (from Python) or
run the ``fetch_energy_osm`` workflow rule with ``energy.osm.allow_download``
enabled in ``config/energy/energy.yaml``. A missing cache raises
:class:`OSMDownloadRequired` with instructions instead of silently contacting
the internet.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

from energy.paths import incoming_energy_dir

GEOGRAPHIC_CRS = "EPSG:4326"


class OSMDownloadRequired(RuntimeError):
    """Raised when OSM data is needed but downloading was not permitted."""


# Shortcuts from a short region key to the OSM/Nominatim query that geocodes it.
# Any other string is passed to Nominatim as written (e.g. "Rodrigues, Mauritius").
# "mauritius" targets the main island only; the bare country name would also
# pull in the outer islands.
REGIONS: dict[str, str] = {
    "mauritius": "Mauritius Island, Mauritius",
    "rodrigues": "Rodrigues, Mauritius",
    "agalega": "Agalega, Mauritius",
    "st_brandon": "Saint Brandon, Mauritius",
}

# A region group is fetched member by member and combined into one cache.
REGION_GROUPS: dict[str, tuple[str, ...]] = {
    "mauritius-rodrigues": ("mauritius", "rodrigues"),
}


def region_query(region: str) -> str:
    """Resolve a region to an OSM/Nominatim query: a REGIONS shortcut if one
    matches, otherwise the string as given."""
    return REGIONS.get(region.strip().lower(), region.strip())


def region_members(region: str) -> tuple[str, ...]:
    """Return the independently fetched places represented by ``region``."""
    normalised = region.strip().lower()
    return REGION_GROUPS.get(normalised, (region.strip(),))


def region_slug(region: str) -> str:
    """Filesystem-safe key for cache folders and product names.

    Group names are kept as they are (``mauritius-rodrigues``); any other query
    is lower-cased with runs of punctuation and spaces replaced by ``_``, e.g.
    ``"Rodrigues, Mauritius"`` becomes ``rodrigues_mauritius``. Use this same
    function wherever a path is derived from a region so that the workflow rules
    and the Python helpers always agree.
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
# The *_relative functions return paths relative to the data root; the workflow
# rules use them with their ``{data}`` wildcard. The osm_*_path functions return
# absolute paths for direct Python use.


def osm_cache_dir_relative(region: str) -> Path:
    return Path("incoming") / "energy" / "osm" / region_slug(region)


def roads_cache_relative(region: str, network_type: str = "drive") -> Path:
    suffix = "" if network_type == "drive" else f"-{region_slug(network_type)}"
    return osm_cache_dir_relative(region) / f"roads{suffix}.parquet"


def power_cache_relative(region: str) -> Path:
    return osm_cache_dir_relative(region) / "power.parquet"


def aoi_cache_relative(region: str) -> Path:
    return osm_cache_dir_relative(region) / "aoi.parquet"


def _data_root_or_default(data_root: Path | None) -> Path:
    return Path(data_root) if data_root is not None else incoming_energy_dir().parent.parent


def osm_roads_path(region: str, network_type: str = "drive", data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / roads_cache_relative(region, network_type)


def osm_power_path(region: str, data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / power_cache_relative(region)


def osm_aoi_path(region: str, data_root: Path | None = None) -> Path:
    return _data_root_or_default(data_root) / aoi_cache_relative(region)


# --- Reading and tidying ------------------------------------------------------


def read_vector(path: Path) -> gpd.GeoDataFrame:
    """Read a vector file by extension: GeoParquet (``.parquet``/``.geoparquet``) or anything GDAL reads."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Vector file does not exist: {path}")
    if path.suffix.lower() in {".parquet", ".geoparquet", ".gpq", ".pq"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def deduplicate_two_way_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep one row per road geometry, treating a line and its reverse as the same road.

    osmnx stores a two-way street as two directed edges with the same geometry
    drawn in opposite directions. For a distribution-line proxy that doubles
    every length and count, so the cache keeps a single undirected copy.
    """
    if roads.empty:
        return roads
    forward = shapely.to_wkb(roads.geometry.to_numpy())
    backward = shapely.to_wkb(shapely.reverse(roads.geometry.to_numpy()))
    keys = [min(a, b) for a, b in zip(forward, backward, strict=True)]
    keep = ~pd.Series(keys, index=roads.index).duplicated()
    return roads.loc[keep].reset_index(drop=True)


def _primary_highway_class(value: object) -> str | None:
    """Normalise an OSM ``highway`` tag to a single lowercase class string.

    osmnx returns ``highway`` as a plain string for most ways, but simplified
    edges that merge several ways carry a list of values. Collapse either form
    to one representative class (the first entry) so the column stays filterable
    and Parquet-friendly. Missing tags become ``None``.
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


# OpenStreetMap tags kept on cached power features (tag -> column). They are
# free text and only there to help match a feature to the CEB report by name.
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
        "Fetch them once (needs internet): set energy.osm.allow_download: true in "
        "config/energy/energy.yaml and run the fetch_energy_osm rule, or call this "
        "function with allow_download=True."
    )


def _configure_osmnx(data_root: Path | None):
    import osmnx as ox  # imported lazily: only needed when downloading

    # Keep the Overpass/Nominatim response cache inside the (git-ignored) data tree.
    ox.settings.cache_folder = str(_data_root_or_default(data_root) / "incoming" / "energy" / "osm" / ".cache")
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
    """Return (path, already_available) for a cache file, honouring an explicit user path."""
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
    """Return the cached OSM road network for ``region`` as LineStrings, fetching it if allowed.

    ``network_type`` is passed to osmnx: ``"drive"`` keeps the drivable network
    (trunk to residential roads and their links), ``"all"`` would also include
    footpaths and tracks, which a distribution-line proxy should not follow.
    Each feature keeps its OSM ``highway`` class. Two-way streets are stored
    once (see :func:`deduplicate_two_way_roads`).

    Pass ``path`` to use a user-supplied roads file instead of the cache.
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

    ox, InsufficientResponseError = _configure_osmnx(data_root)
    try:
        graph = ox.graph_from_place(region_query(region), network_type=network_type)
        # One edge per street: osmnx graphs are directed and hold both directions of two-way roads.
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
    """Return cached OSM power features (substations, plants, generators) as points, fetching if allowed.

    For a region group every member must be available; nothing is written until
    all of them are, so a partial cache can never be mistaken for the full one.
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
    ox, InsufficientResponseError = _configure_osmnx(data_root)
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
            # Descriptive tags, kept as text so a plant can be matched to the CEB report by name.
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
    """Return the cached area-of-interest polygon(s) for ``region`` from OSM/Nominatim, fetching if allowed.

    The night-light target step clips the VIIRS raster to this outline. One
    polygon per region member is stored with the query and retrieval time.
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

    ox, _ = _configure_osmnx(data_root)
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
