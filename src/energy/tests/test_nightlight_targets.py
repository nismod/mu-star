import json

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from energy.nightlight_targets import (
    build_nightlight_targets,
    nightlight_targets,
)


def _write_nightlights(path, values):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=from_origin(0, values.shape[0] * 100, 100, 100),
        nodata=-9999,
    ) as destination:
        destination.write(values.astype("float32"), 1)


def test_nightlight_targets_flags_bright_isolated_cells():
    values = np.zeros((15, 15), dtype=np.float64)
    values[7, 7] = 10.0

    mask, filtered = nightlight_targets(values, threshold=1.0)

    assert mask[7, 7]
    assert mask.sum() == 1
    assert filtered[7, 7] > 0


def test_build_nightlight_targets_writes_target_points(tmp_path):
    nightlights = np.zeros((12, 12), dtype=np.float32)
    nightlights[3, 9] = 5
    nightlights[8, 9] = 5
    nightlights_path = tmp_path / "viirs.tif"
    _write_nightlights(nightlights_path, nightlights)

    aoi_path = tmp_path / "aoi.parquet"
    gpd.GeoDataFrame(
        {"region": ["test"], "geometry": [box(0, 0, 1_200, 1_200)]},
        crs="EPSG:3857",
    ).to_parquet(aoi_path)

    outputs = build_nightlight_targets(
        nightlights_path,
        tmp_path / "nightlight_targets",
        aoi_path=aoi_path,
        region="test",
        nightlight_threshold=1,
    )

    metadata = json.loads(outputs.metadata.read_text())
    targets = gpd.read_parquet(outputs.targets)

    assert metadata["nightlight_target_count"] == 2
    assert metadata["region"] == "test"
    assert len(targets) == 2
    assert set(targets["target_type"]) == {"nightlight"}
    assert targets.crs.to_epsg() == 4326
    assert outputs.targets_raster.exists()
