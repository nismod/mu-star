"""Acquire VIIRS night-time lights: fetch monthly tiles and composite them.

This module is deliberately generic so the same code serves any region or
project: the image service, its monthly raster object IDs, the area of interest
(bbox) and the output grid are all passed in by the caller (wired from
``config.yaml`` in the Snakemake rules). mu-star's defaults target the
Earth Observation Group "NighttimeLightsMDNB" ArcGIS image service over the
Mauritius/Rodrigues bounding box, but nothing here is Mauritius-specific.

Acquisition is opt-in and offline-first, mirroring :mod:`energy.osm`: a build
reads cached monthly tiles and never downloads on its own. Call
:func:`fetch_nightlight_months` with ``allow_download=True`` once to populate the
cache, then :func:`build_nightlight_composite` reduces the tiles to the single
radiance raster the nightlight-target step consumes.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import numpy as np
import rasterio

# Earth Observation Group monthly cloud-free VIIRS DNB radiance (global service).
DEFAULT_SERVICE = "https://di-lawimagery1.img.arcgis.com/arcgis/rest/services/NighttimeLightsMDNB/ImageServer"
# Raster function that returns raw radiance values rather than a rendered image.
DEFAULT_RENDERING_RULE = "Average Monthly Radiance (Raw Values)"


class NightlightDownloadRequired(RuntimeError):
    """Raised when monthly tiles are needed but downloading was not permitted."""


@dataclass(frozen=True)
class NightlightMonths:
    """Cached monthly tiles and the provenance record written beside them."""

    directory: Path
    paths: tuple[Path, ...]
    metadata: Path


def _tile_size(bbox: Sequence[float], pixel_size_degrees: float) -> tuple[int, int]:
    xmin, ymin, xmax, ymax = bbox
    width = round((xmax - xmin) / pixel_size_degrees)
    height = round((ymax - ymin) / pixel_size_degrees)
    if width <= 0 or height <= 0:
        raise ValueError(f"bbox {bbox} and pixel size {pixel_size_degrees} give a non-positive grid")
    return width, height


def _export_image_url(
    service: str,
    *,
    bbox: Sequence[float],
    size: tuple[int, int],
    object_id: int,
    rendering_rule: str,
) -> str:
    """Build an ArcGIS ImageServer ``exportImage`` request for one monthly raster."""
    params = {
        "bbox": ",".join(str(v) for v in bbox),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{size[0]},{size[1]}",
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_NearestNeighbor",
        "renderingRule": json.dumps({"rasterFunction": rendering_rule}),
        # Lock the mosaic to a single monthly raster so each request is one month.
        "mosaicRule": json.dumps({"mosaicMethod": "esriMosaicLockRaster", "lockRasterIds": [object_id]}),
        "f": "image",
    }
    return f"{service.rstrip('/')}/exportImage?{urllib.parse.urlencode(params)}"


def _download(url: str, dest: Path, *, timeout: float) -> None:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"Refusing to fetch a non-HTTP(S) URL: {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "mu-star-energy"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    # ArcGIS returns a JSON error body (HTTP 200) instead of an image on failure.
    if payload.lstrip()[:1] == b"{":
        raise RuntimeError(f"Image service returned an error for {url}: {payload[:300]!r}")
    dest.write_bytes(payload)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def fetch_nightlight_months(
    *,
    object_ids: list[int],
    bbox: Sequence[float],
    pixel_size_degrees: float,
    out_dir: Path,
    service: str = DEFAULT_SERVICE,
    rendering_rule: str = DEFAULT_RENDERING_RULE,
    allow_download: bool = False,
    overwrite: bool = False,
    timeout: float = 120.0,
) -> NightlightMonths:
    """Cache one raster per ``object_ids`` entry as ``NN.tif`` under ``out_dir``.

    Each object ID is one monthly raster in the service's mosaic catalogue. Tiles
    already present are reused unless ``overwrite`` is set. When a tile is missing
    and ``allow_download`` is False this raises :class:`NightlightDownloadRequired`
    rather than contacting the service, so a run never downloads without being
    asked. A ``metadata.json`` recording the service, object IDs, bbox and
    per-tile checksums is written alongside the tiles.
    """
    if not object_ids:
        raise ValueError("object_ids must list at least one monthly raster")
    out_dir = Path(out_dir)
    size = _tile_size(bbox, pixel_size_degrees)
    paths: list[Path] = []
    for index, object_id in enumerate(object_ids, start=1):
        tile = out_dir / f"{index:02d}.tif"
        if tile.exists() and not overwrite:
            paths.append(tile)
            continue
        if not allow_download:
            raise NightlightDownloadRequired(
                f"Night-lights tile {tile} is not cached. Call "
                "fetch_nightlight_months(allow_download=True) to fetch the monthly rasters."
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        url = _export_image_url(
            service, bbox=bbox, size=size, object_id=object_id, rendering_rule=rendering_rule
        )
        _download(url, tile, timeout=timeout)
        paths.append(tile)

    metadata_path = out_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "service": service,
                "rendering_rule": rendering_rule,
                "source_object_ids": list(object_ids),
                "bbox_epsg4326": list(bbox),
                "pixel_size_degrees": pixel_size_degrees,
                "tiles": [{"path": str(p), "sha256": _file_sha256(p)} for p in paths],
            },
            indent=2,
        )
    )
    return NightlightMonths(out_dir, tuple(paths), metadata_path)


def build_nightlight_composite(
    month_paths: list[Path],
    out_path: Path,
    *,
    aggregation: str = "median",
) -> Path:
    """Reduce the monthly tiles to one radiance raster (pixelwise ``median``).

    All tiles must share the grid the fetch produced (same size, CRS and
    transform); the composite inherits that grid. ``median`` is robust to the
    occasional cloud-contaminated month; ``mean`` is also supported.
    """
    paths = [Path(p) for p in month_paths]
    if not paths:
        raise ValueError("At least one monthly tile is required to build a composite")

    with rasterio.open(paths[0]) as first:
        profile = first.profile
        nodata = first.nodata

    layers = []
    for path in paths:
        with rasterio.open(path) as src:
            if (src.width, src.height) != (profile["width"], profile["height"]):
                raise ValueError(f"{path} grid {(src.width, src.height)} does not match {paths[0]}")
            layers.append(src.read(1, masked=True))

    stack = np.ma.stack(layers)
    if aggregation == "median":
        composite = np.ma.median(stack, axis=0)
    elif aggregation == "mean":
        composite = np.ma.mean(stack, axis=0)
    else:
        raise ValueError(f"Unsupported aggregation {aggregation!r}; use 'median' or 'mean'")

    filled = composite.filled(nodata if nodata is not None else 0.0).astype("float32")
    profile.update(count=1, dtype="float32", compress="deflate")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(filled, 1)
    return out_path
