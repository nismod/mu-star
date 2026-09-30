import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from energy import nightlights
from energy.nightlights import (
    NightlightDownloadRequired,
    build_nightlight_composite,
    fetch_nightlight_months,
    tile_name,
)


def _write_tile(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(57, -19, 0.5, 0.5),
    ) as dst:
        dst.write(values.astype("float32"), 1)


def test_build_nightlight_composite_is_pixelwise_median(tmp_path):
    tiles = []
    for index, radiance in enumerate((1.0, 5.0, 9.0), start=1):
        tile = tmp_path / tile_name(index, 119 + index)
        _write_tile(tile, np.full((2, 2), radiance))
        tiles.append(tile)

    out = build_nightlight_composite(tiles, tmp_path / "composite.tif")

    with rasterio.open(out) as src:
        assert np.allclose(src.read(1), 5.0)


def test_composite_ignores_nan_months(tmp_path):
    tiles = []
    for index, radiance in enumerate((1.0, np.nan, 2.0), start=1):
        tile = tmp_path / tile_name(index, 119 + index)
        _write_tile(tile, np.full((2, 2), radiance))
        tiles.append(tile)

    out = build_nightlight_composite(tiles, tmp_path / "composite.tif")

    with rasterio.open(out) as src:
        assert np.allclose(src.read(1), 1.5)


def test_fetch_requires_allow_download_when_uncached(tmp_path):
    with pytest.raises(NightlightDownloadRequired):
        fetch_nightlight_months(
            object_ids=[120, 121],
            bbox=[57, -21, 64, -19],
            pixel_size_degrees=0.5,
            out_dir=tmp_path / "months",
            allow_download=False,
        )


def test_fetch_uses_cache_without_downloading(tmp_path, monkeypatch):
    out_dir = tmp_path / "months"
    for index, object_id in ((1, 120), (2, 121)):
        _write_tile(out_dir / tile_name(index, object_id), np.ones((2, 2)))

    def _forbidden(*args, **kwargs):
        raise AssertionError("cached tiles must not trigger a download")

    monkeypatch.setattr(nightlights, "_download", _forbidden)
    result = fetch_nightlight_months(
        object_ids=[120, 121],
        bbox=[57, -21, 64, -19],
        pixel_size_degrees=0.5,
        out_dir=out_dir,
        allow_download=False,
    )

    assert len(result.paths) == 2
    assert json.loads(result.metadata.read_text())["source_object_ids"] == [120, 121]
    # Another year's object ids do not reuse these tiles.
    with pytest.raises(NightlightDownloadRequired):
        fetch_nightlight_months(
            object_ids=[108, 109],
            bbox=[57, -21, 64, -19],
            pixel_size_degrees=0.5,
            out_dir=out_dir,
            allow_download=False,
        )


def test_fetch_downloads_missing_tiles_when_allowed(tmp_path, monkeypatch):
    def _fake_download(url, dest, *, timeout):
        _write_tile(Path(dest), np.zeros((2, 2)))

    monkeypatch.setattr(nightlights, "_download", _fake_download)
    result = fetch_nightlight_months(
        object_ids=[120, 121, 122],
        bbox=[57, -21, 64, -19],
        pixel_size_degrees=0.5,
        out_dir=tmp_path / "months",
        allow_download=True,
    )

    assert [p.name for p in result.paths] == ["01-120.tif", "02-121.tif", "03-122.tif"]
    assert all(p.exists() for p in result.paths)
