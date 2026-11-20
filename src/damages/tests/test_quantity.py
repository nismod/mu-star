import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient

from damages.quantity import (
    asset_totals,
    conservation_mismatches,
    geodesic_measures,
    measure_geometries,
    measure_splits,
    parse_cost_unit,
    split_quantity,
)


@pytest.mark.parametrize(
    "unit, currency, per, kind",
    [
        ("mur", "mur", None, "asset"),
        ("MUR_per_km", "mur", "km", "length"),
        ("usd_per_m2", "usd", "m2", "area"),
        ("mur_per_capacity_mw", "mur", "capacity_mw", "attribute"),
    ],
)
def test_parse_cost_unit(unit, currency, per, kind):
    parsed = parse_cost_unit(unit)
    assert (parsed.currency, parsed.per, parsed.kind) == (currency, per, kind)


@pytest.mark.parametrize("unit", ["", "_per_m", "mur_per_", None])
def test_parse_cost_unit_invalid(unit):
    with pytest.raises(ValueError):
        parse_cost_unit(unit)


def _splits():
    # Metre-based CRS so measures are exact
    return gpd.GeoDataFrame(
        {
            "asset_id": ["a", "a", "b", "c"],
            "capacity_mw": [np.nan, np.nan, np.nan, 20.0],
            "geometry": [
                LineString([(0, 0), (300, 0)]),
                LineString([(300, 0), (1000, 0)]),
                box(0, 0, 10, 20),
                Point(0, 0),
            ],
        },
        crs="EPSG:32740",
    )


def test_measure_splits():
    splits = measure_splits(_splits())
    assert splits.split_id.tolist() == [0, 1, 0, 0]
    assert splits.length_m.tolist() == pytest.approx([300, 700, 0, 0])
    assert splits.area_m2.tolist() == pytest.approx([0, 0, 200, 0])
    assert splits.share.tolist() == pytest.approx([0.3, 0.7, 1, 1])
    assert splits.geom_kind.tolist() == ["line", "line", "polygon", "point"]


def test_split_quantity():
    splits = measure_splits(_splits())
    lines, area, point = splits.iloc[:2], splits.iloc[[2]], splits.iloc[[3]]
    assert split_quantity(lines, parse_cost_unit("mur_per_km")) == pytest.approx([0.3, 0.7])
    assert split_quantity(lines, parse_cost_unit("mur")) == pytest.approx([0.3, 0.7])
    assert split_quantity(area, parse_cost_unit("mur_per_m2")) == pytest.approx([200])
    assert split_quantity(point, parse_cost_unit("mur_per_capacity_mw")) == pytest.approx([20])
    with pytest.raises(ValueError, match="line"):
        split_quantity(point, parse_cost_unit("mur_per_km"))
    with pytest.raises(KeyError):
        split_quantity(point, parse_cost_unit("mur_per_mw"))
    with pytest.raises(ValueError, match="finite"):
        split_quantity(lines, parse_cost_unit("mur_per_capacity_mw"))


def test_split_quantity_zero_measure_slivers():
    splits = gpd.GeoDataFrame(
        {
            "asset_id": ["a", "a", "b", "b"],
            "geometry": [
                LineString([(0, 0), (1000, 0)]),
                LineString([(1000, 0), (1000, 0)]),
                box(0, 0, 10, 20),
                box(10, 0, 10, 20),
            ],
        },
        crs="EPSG:32740",
    )
    splits = measure_splits(splits)
    lines, areas = splits.iloc[:2], splits.iloc[2:]
    assert lines.share.tolist() == pytest.approx([1, 0])
    assert split_quantity(lines, parse_cost_unit("mur_per_km")) == pytest.approx([1, 0])
    assert split_quantity(areas, parse_cost_unit("mur_per_m2")) == pytest.approx([200, 0])
    # all-zero measure of the right kind is fine with geom_kind, wrong kind is not
    degenerate = measure_splits(splits.iloc[[1]])
    assert split_quantity(degenerate, parse_cost_unit("mur_per_m")) == pytest.approx([0])
    with pytest.raises(ValueError, match="polygon"):
        split_quantity(lines, parse_cost_unit("mur_per_m2"))
    # without geom_kind, only an all-zero measure is rejected
    legacy = lines.drop(columns="geom_kind")
    assert split_quantity(legacy, parse_cost_unit("mur_per_m")) == pytest.approx([1000, 0])
    with pytest.raises(ValueError, match="positive"):
        split_quantity(legacy.iloc[[1]], parse_cost_unit("mur_per_m"))


def test_conservation_mismatches():
    features = gpd.GeoDataFrame(
        {
            "asset_id": ["a", "b", "c", "d", "e"],
            "geometry": [
                LineString([(0, 0), (1000, 0)]),
                LineString([(0, 10), (1000, 10)]),
                box(0, 0, 10, 20),
                Point(0, 0),
                LineString([(0, 20), (100, 20)]),
            ],
        },
        crs="EPSG:32740",
    )
    expected = asset_totals(features)
    assert expected.tolist() == pytest.approx([1000, 1000, 200, 0, 100])
    splits = gpd.GeoDataFrame(
        {
            "asset_id": ["a", "a", "b", "c", "c", "d"],
            "geometry": [
                LineString([(0, 0), (400, 0)]),
                LineString([(400, 0), (1000, 0)]),
                LineString([(0, 10), (900, 10)]),  # lost 10%
                box(0, 0, 10, 10),
                box(0, 10, 10, 20),
                Point(0, 0),
            ],
        },
        crs="EPSG:32740",
    )
    result = conservation_mismatches(expected, measure_splits(splits))
    # b lost 10%, e has no splits at all
    assert result.index.tolist() == ["e", "b"]
    assert result.rel_diff.tolist() == pytest.approx([1.0, 0.1])
    assert result.actual.tolist() == pytest.approx([0, 900])
    assert conservation_mismatches(expected, measure_splits(splits), rtol=1.0).empty


def test_measure_splits_share_of_unsplit_asset():
    # a polygon of 400 m2 of which only 200 m2 has a split: its share is a half, not one
    totals = pd.Series({"a": 1000.0, "b": 400.0, "c": 0.0})
    splits = measure_splits(_splits(), totals=totals)
    assert splits.share.tolist() == pytest.approx([0.3, 0.7, 0.5, 1])


HOLE = box(57.52, -20.28, 57.54, -20.26)
OUTER = box(57.5, -20.3, 57.6, -20.2)


def test_geodesic_measures():
    geometry = gpd.GeoSeries(
        [
            LineString([(0, 0), (1, 0)]),  # one degree along the equator
            OUTER,
            Polygon(OUTER.exterior.coords, [HOLE.exterior.coords]),  # hole wound the same way as the exterior
            orient(OUTER, -1.0),  # clockwise
            Point(57.5, -20.2),
            LineString(),
        ],
        crs="EPSG:4326",
    )
    length, area = geodesic_measures(geometry)
    assert length.tolist() == pytest.approx([111_319.491, 0, 0, 0, 0, 0])
    _, (outer_area, hole_area) = geodesic_measures(gpd.GeoSeries([OUTER, HOLE], crs="EPSG:4326"))
    assert area.tolist() == pytest.approx([0, outer_area, outer_area - hole_area, outer_area, 0, 0])
    assert outer_area == pytest.approx(115_668_102, rel=1e-6)


def test_measure_geometries_planar_in_projected_metres():
    geometry = gpd.GeoSeries([LineString([(0, 0), (300, 400)]), box(0, 0, 10, 20), Point(0, 0)], crs="EPSG:32740")
    length, area = measure_geometries(geometry)
    assert length.tolist() == [500, 0, 0]
    assert area.tolist() == [0, 200, 0]


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:2263"])  # degrees, and a projected CRS in US survey feet
def test_measure_geometries_geodesic_otherwise(crs):
    geometry = gpd.GeoSeries([LineString([(57.5, -20.3), (57.6, -20.2)]), OUTER], crs="EPSG:4326").to_crs(crs)
    length, area = measure_geometries(geometry)
    expected_length, expected_area = geodesic_measures(geometry)
    assert length == pytest.approx(expected_length)
    assert area == pytest.approx(expected_area)
    assert length[0] == pytest.approx(15_340, rel=1e-2)  # about 0.1 degrees each way, near 20 S


def test_measures_need_crs():
    geometry = gpd.GeoSeries([LineString([(0, 0), (1, 0)])])
    with pytest.raises(ValueError, match="CRS"):
        measure_geometries(geometry)
    with pytest.raises(ValueError, match="CRS"):
        geodesic_measures(geometry)
