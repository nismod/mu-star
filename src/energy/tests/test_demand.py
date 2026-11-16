import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from energy.demand import (
    area_scores,
    build_demand_shares,
    distribution_key,
    node_scores,
    node_shares,
    road_areas,
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


def _edges(rows):
    """Edges from ``(bus0, bus1, model_v_nom_kv, length_km)`` tuples."""
    return pd.DataFrame(rows, columns=["bus0", "bus1", "model_v_nom_kv", "length_km"])


def test_road_areas_follow_the_distribution_lines_not_the_straight_line():
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["W", "E", "n", "w1", "x"],
            "region": ["mauritius"] * 5,
            "geometry": [
                Point(57.02, -20.05),
                Point(57.08, -20.05),
                Point(57.04, -20.05),  # nearer W in a straight line, but its road leads to E
                Point(57.02, -20.06),
                Point(57.03, -20.09),  # on no line
            ],
        },
        crs="EPSG:4326",
    )
    supply = nodes[nodes["bus_id"].isin(["W", "E"])].assign(island="mauritius")
    # The 66 kV line from W to n is above the 35 kV threshold, so it is not a distribution line.
    edges = _edges([("n", "E", 11.0, 5.0), ("W", "n", 66.0, 2.0), ("W", "w1", 11.0, 1.0)])

    areas = road_areas(nodes, edges, supply)

    assert areas["area_bus_id"].to_dict() == {"W": "W", "E": "E", "n": "E", "w1": "W", "x": "W"}
    assert areas["reached"].to_dict() == {"W": True, "E": True, "n": True, "w1": True, "x": False}
    meshed = road_areas(nodes, edges, supply, max_radial_line_voltage_kv=66.0)
    assert meshed.at["n", "area_bus_id"] == "W"


def test_service_areas_join_the_cells_of_each_supply_points_nodes():
    outlines = gpd.GeoDataFrame(
        {"geometry": [box(57.0, -20.1, 57.1, -20.0), box(63.4, -19.8, 63.5, -19.7)]}, crs="EPSG:4326"
    )
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["W", "E", "n", "R"],
            "region": ["mauritius"] * 3 + ["rodrigues"],
            "geometry": [Point(57.02, -20.05), Point(57.08, -20.05), Point(57.04, -20.05), Point(63.45, -19.75)],
        },
        crs="EPSG:4326",
    )
    node_areas = pd.DataFrame(
        {"area_bus_id": ["W", "E", "E", "R"], "reached": [True] * 4}, index=pd.Index(["W", "E", "n", "R"])
    )

    areas = service_areas(nodes, node_areas, outlines)

    by_bus = areas.set_index("bus_id").geometry
    assert sorted(by_bus.index) == ["E", "R", "W"]
    assert by_bus["W"].area + by_bus["E"].area == pytest.approx(0.01, rel=1e-3)
    assert by_bus["E"].contains(Point(57.04, -20.05)) and not by_bus["W"].contains(Point(57.04, -20.05))
    assert by_bus["R"].area == pytest.approx(0.01, rel=1e-3)


def test_area_and_node_scores_follow_the_rasters(tmp_path):
    outlines = gpd.GeoDataFrame({"geometry": [box(57.0, -20.1, 57.1, -20.0)]}, crs="EPSG:4326")
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["W", "n1", "E", "n2", "u"],
            "region": ["mauritius"] * 5,
            "geometry": [
                Point(57.025, -20.05),
                Point(57.025, -20.08),
                Point(57.075, -20.05),
                Point(57.075, -20.08),
                Point(57.005, -20.005),  # on no line, so it gets no cells
            ],
        },
        crs="EPSG:4326",
    )
    supply = nodes[nodes["bus_id"].isin(["W", "E"])].assign(island="mauritius")
    node_areas = road_areas(nodes, _edges([("W", "n1", 11.0, 3.0), ("E", "n2", 11.0, 3.0)]), supply)
    # 10 x 10 cells of 0.01 degrees; people only in the western half, lights only in the eastern half.
    population = np.zeros((10, 10))
    population[:, :5] = 10.0
    lights = np.zeros((10, 10))
    lights[:, 5:] = 2.0
    _write_raster(tmp_path / "pop.tif", population)
    _write_raster(tmp_path / "lights.tif", lights)
    rasters = {"population": tmp_path / "pop.tif", "nightlights": tmp_path / "lights.tif"}

    node_frame = node_scores(nodes, node_areas, rasters, outlines)
    scores = area_scores(node_frame, ["W", "E"])

    assert scores.loc["W", "population"] == pytest.approx(500.0)
    assert scores.loc["E", "population"] == pytest.approx(0.0)
    assert scores.loc["E", "nightlights"] == pytest.approx(100.0)
    assert node_frame.loc["u", ["population", "nightlights"]].tolist() == [0.0, 0.0]
    assert node_frame.at["u", "area_bus_id"] == "W"
    reached = node_frame.loc[node_areas.index[node_areas["reached"]]]
    shares = node_shares(reached, distribution_key(scores))
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


def test_build_demand_shares_writes_shares_that_add_to_one_on_each_island(tmp_path):
    outlines = gpd.GeoDataFrame(
        {"geometry": [box(57.0, -20.1, 57.1, -20.0), box(63.4, -19.8, 63.5, -19.7)]}, crs="EPSG:4326"
    )
    outlines.to_parquet(tmp_path / "aoi.parquet")
    nodes = gpd.GeoDataFrame(
        {
            "bus_id": ["bus::A", "bus::B", "dist::1", "dist::3", "asset::plant-r", "dist::2"],
            "kind": ["substation", "substation"] + ["distribution_node"] * 2 + ["generator", "distribution_node"],
            "region": ["mauritius"] * 4 + ["rodrigues"] * 2,
            "geometry": [
                Point(57.02, -20.05),
                Point(57.08, -20.05),
                Point(57.05, -20.03),
                Point(57.09, -20.09),  # on no line: no demand
                Point(63.42, -19.75),
                Point(63.45, -19.72),
            ],
        },
        crs="EPSG:4326",
    )
    nodes.to_parquet(tmp_path / "nodes.geoparquet")
    edges = _edges([("bus::A", "dist::1", 11.0, 4.0), ("asset::plant-r", "dist::2", 11.0, 4.0)])
    gpd.GeoDataFrame(edges, geometry=[LineString([(0, 0), (1, 1)])] * len(edges), crs="EPSG:4326").to_parquet(
        tmp_path / "edges.geoparquet"
    )
    _write_raster(tmp_path / "pop.tif", np.full((10, 10), 3.0))  # covers the first island only
    _write_raster(tmp_path / "lights.tif", np.full((10, 10), 1.0))
    levels = pd.DataFrame(
        {
            "level": ["peak", "average", "peak", "average"],
            "island": ["mauritius", "mauritius", "rodrigues", "rodrigues"],
            "demand_mw": [500.0, 350.0, 8.0, 5.0],
        }
    )
    levels.to_csv(tmp_path / "levels.csv", index=False)
    arguments = dict(
        nodes_path=tmp_path / "nodes.geoparquet",
        edges_path=tmp_path / "edges.geoparquet",
        aoi_path=tmp_path / "aoi.parquet",
        population_path=tmp_path / "pop.tif",
        nightlights_path=tmp_path / "lights.tif",
        demand_levels_path=tmp_path / "levels.csv",
        output_dir=tmp_path / "demand",
    )

    outputs = build_demand_shares(**arguments)

    substations = pd.read_csv(outputs.substation_weights)
    node_weights = pd.read_csv(outputs.node_weights)
    metadata = json.loads(outputs.metadata.read_text())
    assert substations.groupby("island")["service_weight"].sum().to_dict() == pytest.approx(
        {"mauritius": 1.0, "rodrigues": 1.0}
    )
    assert node_weights.groupby("island")["service_weight"].sum().to_dict() == pytest.approx(
        {"mauritius": 1.0, "rodrigues": 1.0}
    )
    assert set(node_weights["bus_id"]) == set(nodes["bus_id"])
    assert node_weights.set_index("bus_id").at["dist::3", "service_weight"] == 0.0
    assert len(gpd.read_parquet(outputs.service_areas)) == 3
    assert metadata["weights"] == {"nightlights": 0.6, "population": 0.4}
    assert (metadata["max_radial_line_voltage_kv"], metadata["nodes_not_joined"]) == (35.0, 1)
    assert pd.read_csv(outputs.demand_levels)["demand_mw"].tolist() == [500.0, 350.0, 8.0, 5.0]

    levels[levels["island"].eq("mauritius")].to_csv(tmp_path / "levels.csv", index=False)
    with pytest.raises(ValueError, match="no demand levels for \\['rodrigues'\\]"):
        build_demand_shares(**arguments)
