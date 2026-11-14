import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

from damages.quantity import measure_splits, parse_cost_unit, split_quantity


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
