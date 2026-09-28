import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from energy.demand import (
    area_scores,
    build_demand_shares,
    distribution_key,
    node_scores,
    node_shares,
    service_areas,
    supply_points,
)


def _write_raster(path, values, *, west=57.0, north=-20.0, size=0.01):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(west, north, size, size),
        nodata=-1.0,
    ) as dst:
        dst.write(values.astype("float32"), 1)


def test_distribution_key_normalises_and_weights_indicators():
    scores = pd.DataFrame({"nightlights": [10.0, 0.0], "population": [0.0, 100.0]}, index=["A", "B"])
    key = distribution_key(scores, weights={"nightlights": 0.6, "population": 0.4})
    assert key["A"] == pytest.approx(0.6)
    assert key["B"] == pytest.approx(0.4)
    assert distribution_key(scores * 0).tolist() == [0.5, 0.5]
    with pytest.raises(ValueError, match="not present"):
        distribution_key(scores, weights={"gdp": 1.0})


def test_service_areas_split_an_island_and_keep_a_single_point_island_whole():
    outlines = gpd.GeoDataFrame(
        {"geometry": [box(57.0, -20.1, 57.1, -20.0), box(63.4, -19.8, 63.5, -19.7)]}, crs="EPSG:4326"
    )
    supply = gpd.GeoDataFrame(
        {"bus_id": ["W", "E", "R"], "geometry": [Point(57.02, -20.05), Point(57.08, -20.05), Point(63.45, -19.75)]},
        crs="EPSG:4326",
    )

    areas = service_areas(supply, outlines)

    assert sorted(areas["bus_id"]) == ["E", "R", "W"]
    by_bus = areas.set_index("bus_id").geometry
    assert by_bus["W"].area + by_bus["E"].area == pytest.approx(0.01, rel=1e-6)
    assert by_bus["W"].contains(Point(57.02, -20.05)) and by_bus["E"].contains(Point(57.08, -20.05))
    assert by_bus["R"].equals(outlines.geometry.iloc[1])


def test_area_and_node_scores_follow_the_rasters(tmp_path):
    outlines = gpd.GeoDataFrame({"geometry": [box(57.0, -20.1, 57.1, -20.0)]}, crs="EPSG:4326")
    supply = gpd.GeoDataFrame(
        {"bus_id": ["W", "E"], "geometry": [Point(57.02, -20.05), Point(57.08, -20.05)]}, crs="EPSG:4326"
    )
    areas = service_areas(supply, outlines)
    # 10 x 10 cells of 0.01 degrees; people only in the western half, lights only in the eastern half.
    population = np.zeros((10, 10))
    population[:, :5] = 10.0
    lights = np.zeros((10, 10))
    lights[:, 5:] = 2.0
    _write_raster(tmp_path / "pop.tif", population)
    _write_raster(tmp_path / "lights.tif", lights)
    rasters = {"population": tmp_path / "pop.tif", "nightlights": tmp_path / "lights.tif"}

    scores = area_scores(areas, rasters)
    assert scores.loc["W", "population"] == pytest.approx(500.0)
    assert scores.loc["E", "population"] == pytest.approx(0.0)
    assert scores.loc["E", "nightlights"] == pytest.approx(100.0)

    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["W", "n1", "E", "n2"],
            "geometry": [Point(57.02, -20.05), Point(57.03, -20.02), Point(57.08, -20.05), Point(57.09, -20.09)],
        },
        crs="EPSG:4326",
    )
    node_frame = node_scores(nodes, areas, rasters)
    assert set(node_frame["area_bus_id"]) == {"W", "E"}
    assert node_frame.loc[["W", "n1"], "population"].sum() == pytest.approx(500.0)
    shares = node_shares(node_frame, distribution_key(scores))
    assert shares.sum() == pytest.approx(1.0)
    assert shares.loc[["W", "n1"]].sum() == pytest.approx(0.4)  # population weight 0.4 lands in the west


def test_supply_points_fall_back_to_power_stations_on_an_island_without_a_substation():
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["bus::A", "asset::gen-a", "dist::1", "asset::island-plant", "asset::island-pv", "dist::2"],
            "kind": ["substation", "generator", "distribution_node", "generator", "generator", "distribution_node"],
            "region": ["mauritius"] * 3 + ["rodrigues"] * 3,
            "geometry": [
                Point(57.5, -20.2),
                Point(57.51, -20.2),
                Point(57.55, -20.2),
                Point(63.42, -19.68),
                Point(63.42, -19.68),  # rooftop PV at the same point as the station: counted once
                Point(63.45, -19.7),
            ],
        },
        crs="EPSG:4326",
    )

    points = supply_points(nodes)

    assert list(points["bus_id"]) == ["bus::A", "asset::island-plant"]
    with pytest.raises(ValueError, match="no substation or generator node"):
        supply_points(nodes[nodes["kind"].eq("distribution_node")])


def test_build_demand_shares_writes_the_review_files(tmp_path):
    outlines = gpd.GeoDataFrame({"geometry": [box(57.0, -20.1, 57.1, -20.0)]}, crs="EPSG:4326")
    outlines.to_parquet(tmp_path / "aoi.parquet")
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["bus::A", "bus::B", "dist::1"],
            "kind": ["substation", "substation", "distribution_node"],
            "geometry": [Point(57.02, -20.05), Point(57.08, -20.05), Point(57.05, -20.03)],
        },
        crs="EPSG:4326",
    )
    nodes.to_parquet(tmp_path / "nodes.geoparquet")
    _write_raster(tmp_path / "pop.tif", np.full((10, 10), 3.0))
    _write_raster(tmp_path / "lights.tif", np.full((10, 10), 1.0))
    levels = pd.DataFrame({"level": ["peak", "average"], "demand_mw": [500.0, 350.0]})
    levels.to_csv(tmp_path / "levels.csv", index=False)

    outputs = build_demand_shares(
        nodes_path=tmp_path / "nodes.geoparquet",
        aoi_path=tmp_path / "aoi.parquet",
        population_path=tmp_path / "pop.tif",
        nightlights_path=tmp_path / "lights.tif",
        demand_levels_path=tmp_path / "levels.csv",
        output_dir=tmp_path / "demand",
    )

    substations = pd.read_csv(outputs.substation_weights)
    node_weights = pd.read_csv(outputs.node_weights)
    metadata = json.loads(outputs.metadata.read_text())
    assert substations["service_weight"].sum() == pytest.approx(1.0)
    assert node_weights["service_weight"].sum() == pytest.approx(1.0)
    assert set(node_weights["bus_id"]) == {"bus::A", "bus::B", "dist::1"}
    assert len(gpd.read_parquet(outputs.service_areas)) == 2
    assert metadata["weights"] == {"nightlights": 0.6, "population": 0.4}
    assert pd.read_csv(outputs.demand_levels)["demand_mw"].tolist() == [500.0, 350.0]
