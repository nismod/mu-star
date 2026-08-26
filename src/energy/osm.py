"""Fetch OSM road networks for a region as inferred distribution-line geometry.

OSM roads are a proxy for where the low-voltage network runs; they are not
confirmed engineering data, so any network built from them stays labelled as
inferred. Fetching needs internet (the OSM Overpass API), so driving-network
results are cached under ``data/incoming/energy/osm/<region>/roads.parquet``;
other network types use a type-specific file in the same folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

from energy.paths import incoming_energy_dir


class OSMDownloadRequired(RuntimeError):
    """Raised when OSM data is needed but downloading was not permitted."""


# Convenience shortcuts: a short key maps to a full OSM/Nominatim query. These
# are optional -- the fetch functions accept any query string (e.g.
# "Rodrigues, Mauritius"), so the workflow is not limited to the entries below.
# For Mauritius, "mauritius" targets the main island only; the bare country name
# would also pull in the outer islands. Add a shortcut for places you fetch often.
REGIONS: dict[str, str] = {
    "mauritius": "Mauritius Island, Mauritius",
    "rodrigues": "Rodrigues, Mauritius",
    "agalega": "Agalega, Mauritius",
    "st_brandon": "Saint Brandon, Mauritius",
}

REGION_GROUPS: dict[str, tuple[str, ...]] = {
    "mauritius-rodrigues": ("mauritius", "rodrigues"),
}

GEOGRAPHIC_CRS = "EPSG:4326"


def region_query(region: str) -> str:
    """Resolve a region to an OSM/Nominatim query: a REGIONS shortcut if one
    matches, otherwise the string as given."""
    return REGIONS.get(region.strip().lower(), region.strip())


def region_members(region: str) -> tuple[str, ...]:
    """Return the independently fetched places represented by ``region``."""
    normalised = region.strip().lower()
    return REGION_GROUPS.get(normalised, (region.strip(),))


def region_slug(region: str) -> str:
    """Filesystem-safe key for cache folders and output names, e.g.
    "Rodrigues, Mauritius" -> "rodrigues_mauritius"."""
    normalised = region.strip().lower()
    if normalised in REGION_GROUPS:
        return normalised
    slug = re.sub(r"[^a-z0-9]+", "_", normalised).strip("_")
    return slug or "region"


def _require_region(region: str) -> str:
    if not str(region).strip():
        raise ValueError("region must be a non-empty OSM/Nominatim query")
    return str(region).strip()


@dataclass(frozen=True)
class OSMRoadsOutput:
    region: str
    path: Path
    edge_count: int


def osm_roads_path(region: str, network_type: str = "drive") -> Path:
    suffix = "" if network_type == "drive" else f"-{region_slug(network_type)}"
    return incoming_energy_dir() / "osm" / region_slug(region) / f"roads{suffix}.parquet"


def osm_power_path(region: str) -> Path:
    return incoming_energy_dir() / "osm" / region_slug(region) / "power.parquet"


def _empty_roads(region: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"source": [], "region": [], "highway": [], "geometry": []},
        geometry="geometry",
        crs=GEOGRAPHIC_CRS,
    )


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


def _empty_power_features() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"source": [], "region": [], "bus_id": [], "power": [], "geometry": []},
        geometry="geometry",
        crs=GEOGRAPHIC_CRS,
    )


def fetch_osm_roads(
    region: str,
    *,
    network_type: str = "drive",
    overwrite: bool = False,
    allow_download: bool = False,
) -> OSMRoadsOutput:
    """Fetch the OSM road network for a region and cache it as LineStrings.

    ``region`` is any OSM/Nominatim query (e.g. "Rodrigues, Mauritius"); the
    REGIONS shortcuts expand to full queries. ``network_type`` sets the road
    detail and is passed straight to osmnx: "drive" keeps the drivable road
    network (trunk/primary/secondary/tertiary/unclassified/residential and their
    links), while "all" also pulls in footpaths, tracks, steps and cycleways --
    which the distribution-line proxy should not follow. Each cached feature
    keeps its OSM ``highway`` class so the classification stays inspectable and
    filterable downstream. The cached file is reused unless ``overwrite`` is set.
    When the data is not cached and ``allow_download`` is False, this raises
    ``OSMDownloadRequired`` instead of contacting OSM, so a run never downloads
    without being asked. Regions with no mapped roads (e.g. St Brandon) cache an
    empty layer.
    """
    region = _require_region(region)
    slug = region_slug(region)

    path = osm_roads_path(region, network_type)
    if path.exists() and not overwrite:
        return OSMRoadsOutput(region, path, len(gpd.read_parquet(path)))
    members = region_members(region)
    if len(members) > 1:
        member_frames = [
            gpd.read_parquet(
                fetch_osm_roads(
                    member,
                    network_type=network_type,
                    overwrite=overwrite,
                    allow_download=allow_download,
                ).path
            )
            for member in members
        ]
        roads = gpd.GeoDataFrame(
            pd.concat(member_frames, ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        roads.to_parquet(path)
        return OSMRoadsOutput(region, path, len(roads))
    if not allow_download:
        raise OSMDownloadRequired(
            f"OSM roads for {region!r} are not cached at {path}. "
            "Set allow_download=True (notebook: ALLOW_DOWNLOAD = True) to fetch them."
        )

    import osmnx as ox  # imported lazily; needs network access

    # Keep the Overpass response cache inside the (ignored) data tree.
    ox.settings.cache_folder = str(incoming_energy_dir() / "osm" / ".cache")

    try:
        from osmnx._errors import InsufficientResponseError
    except Exception:  # pragma: no cover - version-dependent import
        InsufficientResponseError = Exception  # type: ignore[assignment]

    try:
        graph = ox.graph_from_place(region_query(region), network_type=network_type)
        edges = ox.graph_to_gdfs(graph, nodes=False).reset_index()
        highway = edges["highway"].map(_primary_highway_class) if "highway" in edges else None
        roads = edges[["geometry"]].copy()
        roads["source"] = "osm_roads"
        roads["region"] = slug
        roads["highway"] = highway
        roads = roads[["source", "region", "highway", "geometry"]]
    except InsufficientResponseError:
        roads = _empty_roads(region)

    path.parent.mkdir(parents=True, exist_ok=True)
    roads.to_parquet(path)
    return OSMRoadsOutput(region, path, len(roads))


def fetch_osm_power_features(region: str, *, overwrite: bool = False, allow_download: bool = False) -> Path:
    """Fetch OSM power features for a region and cache them as bus points.

    Accepts any OSM/Nominatim query. Like ``fetch_osm_roads``, this raises
    ``OSMDownloadRequired`` when the data is not cached and ``allow_download``
    is False.
    """
    region = _require_region(region)
    slug = region_slug(region)
    path = osm_power_path(region)
    if path.exists() and not overwrite:
        return path
    members = region_members(region)
    if len(members) > 1:
        member_frames = []
        for member in members:
            try:
                member_path = fetch_osm_power_features(
                    member,
                    overwrite=overwrite,
                    allow_download=allow_download,
                )
            except OSMDownloadRequired:
                continue
            member_frames.append(gpd.read_parquet(member_path))
        if not member_frames:
            raise OSMDownloadRequired(
                f"OSM power features for every member of {region!r} are missing. Set allow_download=True to fetch them."
            )
        power = gpd.GeoDataFrame(
            pd.concat(member_frames, ignore_index=True),
            geometry="geometry",
            crs=GEOGRAPHIC_CRS,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        power.to_parquet(path)
        return path
    if not allow_download:
        raise OSMDownloadRequired(
            f"OSM power features for {region!r} are not cached at {path}. "
            "Set allow_download=True (notebook: ALLOW_DOWNLOAD = True) to fetch them."
        )

    import osmnx as ox  # imported lazily; needs network access

    ox.settings.cache_folder = str(incoming_energy_dir() / "osm" / ".cache")

    try:
        from osmnx._errors import InsufficientResponseError
    except Exception:  # pragma: no cover - version-dependent import
        InsufficientResponseError = Exception  # type: ignore[assignment]

    try:
        features = ox.features_from_place(
            region_query(region),
            tags={"power": ["substation", "plant", "generator"]},
        )
        if features.empty:
            power = _empty_power_features()
        else:
            features = features[features.geometry.notna()].copy()
            if features.empty:
                power = _empty_power_features()
            else:
                features = features.reset_index(drop=True)
                if features.crs is None:
                    features = features.set_crs(GEOGRAPHIC_CRS)
                metric = features.to_crs("EPSG:32740")
                power_values = features["power"].astype(str).to_numpy() if "power" in features else [""] * len(metric)
                power = gpd.GeoDataFrame(
                    {
                        "source": "osm_power",
                        "region": slug,
                        "bus_id": [f"{slug.upper()}_SUB_{number:03d}" for number in range(1, len(metric) + 1)],
                        "power": power_values,
                    },
                    geometry=metric.geometry.centroid.reset_index(drop=True),
                    crs="EPSG:32740",
                ).to_crs(GEOGRAPHIC_CRS)
    except InsufficientResponseError:
        power = _empty_power_features()

    path.parent.mkdir(parents=True, exist_ok=True)
    power.to_parquet(path)
    return path
