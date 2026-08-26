"""Select likely electrification targets from VIIRS night-time lights.

The high-pass filter and threshold are adapted from GridFinder 3.1.2 by Chris
Arderne (MIT licence): https://github.com/carderne/gridfinder

Only the nightlight target step is kept. The inferred distribution network no
longer routes a least-cost tree over roads; instead these targets are used
downstream to retain the road subnetwork they support (see
``network_source._nightlight_supported_roads``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.transform import xy
from scipy import signal
from shapely.geometry import Point, box, mapping


@dataclass(frozen=True)
class NightlightTargetOutputs:
    """Reviewable files written by :func:`build_nightlight_targets`."""

    targets_raster: Path
    targets: Path
    metadata: Path


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_vector(path: Path, label: str) -> gpd.GeoDataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    frame = gpd.read_parquet(path) if path.suffix.lower() in {".parquet", ".geoparquet"} else gpd.read_file(path)
    if frame.empty:
        raise ValueError(f"{label} is empty: {path}")
    if frame.crs is None:
        raise ValueError(f"{label} must have a coordinate reference system")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise ValueError(f"{label} contains missing or empty geometries")
    if not frame.geometry.is_valid.all():
        raise ValueError(f"{label} contains invalid geometries")
    return frame


def _polygon_features(path: Path, label: str) -> gpd.GeoDataFrame:
    frame = _read_vector(path, label)
    unsupported = set(frame.geometry.geom_type) - {"Polygon", "MultiPolygon"}
    if unsupported:
        raise ValueError(f"{label} contains unsupported geometry types: {sorted(unsupported)}")
    return frame


def create_nightlight_filter() -> np.ndarray:
    """Return the normalised 41-by-41 high-pass smoothing kernel."""
    rows, columns = np.indices((41, 41))
    distance = np.hypot(rows - 20, columns - 20)
    kernel = np.zeros((41, 41), dtype=np.float64)
    noncentral = distance > 0
    kernel[noncentral] = 1 / (1 + distance[noncentral] / 2) ** 3
    return kernel / kernel.sum()


def nightlight_targets(
    nightlights: np.ndarray,
    *,
    threshold: float = 0.1,
    valid_mask: np.ndarray | None = None,
    target_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply the high-pass filter and threshold to VIIRS radiance."""
    values = np.asarray(nightlights, dtype=np.float64)
    if values.ndim != 2 or values.size == 0:
        raise ValueError("nightlights must be a non-empty two-dimensional array")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("nightlight_threshold must be greater than zero")
    valid = np.isfinite(values)
    if valid_mask is not None:
        supplied = np.asarray(valid_mask, dtype=bool)
        if supplied.shape != values.shape:
            raise ValueError("valid_mask must have the same shape as nightlights")
        valid &= supplied
    target_area = valid.copy()
    if target_mask is not None:
        supplied = np.asarray(target_mask, dtype=bool)
        if supplied.shape != values.shape:
            raise ValueError("target_mask must have the same shape as nightlights")
        target_area &= supplied
    if not valid.any() or not target_area.any():
        raise ValueError("Nightlight area has no valid cells")

    prepared = np.where(valid, values, 0)
    smoothed = signal.convolve2d(
        prepared,
        create_nightlight_filter(),
        mode="same",
        boundary="fill",
        fillvalue=0,
    )
    filtered = prepared - smoothed
    return target_area & (filtered >= threshold), filtered


def _target_points(
    mask_array: np.ndarray,
    radiance: np.ndarray,
    filtered: np.ndarray,
    transform: rasterio.Affine,
    crs: CRS,
) -> gpd.GeoDataFrame:
    rows, columns = np.where(mask_array)
    x_coordinates, y_coordinates = xy(transform, rows, columns, offset="center")
    return gpd.GeoDataFrame(
        {
            "target_id": [f"nightlight_{number:06d}" for number in range(1, len(rows) + 1)],
            "target_type": "nightlight",
            "radiance": radiance[rows, columns].astype(float),
            "filtered_radiance": filtered[rows, columns].astype(float),
            "geometry": [Point(x, y) for x, y in zip(x_coordinates, y_coordinates, strict=True)],
        },
        geometry="geometry",
        crs=crs,
    ).to_crs("EPSG:4326")


def _read_nightlights(
    path: Path,
    aoi: gpd.GeoDataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, rasterio.Affine, CRS]:
    with rasterio.open(path) as source:
        if source.count != 1 or source.crs is None:
            raise ValueError("Nightlights raster must have one band and a coordinate system")
        crs = CRS.from_user_input(source.crs)
        aoi_geometry = aoi.to_crs(crs).geometry.union_all()
        if not aoi_geometry.intersects(box(*source.bounds)):
            raise ValueError("Nightlight AOI does not overlap the nightlights raster")
        pixel_size = max(abs(source.transform.a), abs(source.transform.e))
        clipped, transform = mask(
            source,
            [mapping(aoi_geometry.buffer(20 * pixel_size))],
            crop=True,
            filled=False,
            indexes=1,
        )
        radiance = np.asarray(clipped.filled(0), dtype=np.float64)
        filter_valid = ~np.ma.getmaskarray(clipped) & np.isfinite(radiance)
        analysis_valid = filter_valid & geometry_mask(
            [mapping(aoi_geometry)],
            out_shape=radiance.shape,
            transform=transform,
            invert=True,
        )
    if not analysis_valid.any():
        raise ValueError("Nightlight AOI has no valid nightlights cells")
    return radiance, filter_valid, analysis_valid, transform, crs


def _write_raster(
    path: Path,
    array: np.ndarray,
    *,
    transform: rasterio.Affine,
    crs: CRS,
    nodata: float | int,
) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=array.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
        compress="deflate",
    ) as destination:
        destination.write(array, 1)


def build_nightlight_targets(
    nightlights_path: Path,
    output_dir: Path,
    *,
    aoi_path: Path,
    region: str,
    nightlight_threshold: float = 0.1,
) -> NightlightTargetOutputs:
    """Vectorise VIIRS nightlight targets inside an area of interest.

    This replaces the former GridFinder least-cost search. It writes only the
    nightlight target points (and their raster mask); road selection happens
    later in :func:`network_source._nightlight_supported_roads`.
    """
    nightlights_path = Path(nightlights_path)
    aoi_path = Path(aoi_path)
    for path, label in (
        (nightlights_path, "Nightlights raster"),
        (aoi_path, "Nightlight AOI"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")

    aoi = _polygon_features(aoi_path, "Nightlight AOI")
    radiance, filter_valid, analysis_valid, transform, raster_crs = _read_nightlights(nightlights_path, aoi)
    nightlight_mask, filtered = nightlight_targets(
        radiance,
        threshold=nightlight_threshold,
        valid_mask=filter_valid,
        target_mask=analysis_valid,
    )
    if not nightlight_mask.any():
        raise ValueError("Nightlights filtering produced zero targets")
    target_points = _target_points(nightlight_mask, radiance, filtered, transform, raster_crs)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = NightlightTargetOutputs(
        targets_raster=output_dir / "targets.tif",
        targets=output_dir / "targets.geoparquet",
        metadata=output_dir / "metadata.json",
    )
    _write_raster(
        outputs.targets_raster,
        nightlight_mask.astype(np.uint8),
        transform=transform,
        crs=raster_crs,
        nodata=0,
    )
    target_points.to_parquet(outputs.targets)
    metadata = {
        "method": "viirs_nightlight_high_pass_threshold",
        "method_source": "https://github.com/carderne/gridfinder",
        "method_license": "MIT",
        "region": region,
        "nightlights": str(nightlights_path),
        "nightlights_sha256": _file_sha256(nightlights_path),
        "aoi": str(aoi_path),
        "aoi_sha256": _file_sha256(aoi_path),
        "nightlight_threshold": float(nightlight_threshold),
        "nightlight_target_count": int(nightlight_mask.sum()),
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2))
    return outputs
