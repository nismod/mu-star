"""Relate split geometries to the units in which rehabilitation costs are given.

Costs in the damage configuration workbook have a ``cost_unit`` such as:

- ``mur`` - cost of the whole asset, distributed over its splits by share of
  length (lines) or area (polygons); a point has a single split, share 1
- ``mur_per_m``, ``mur_per_km`` - cost per unit length (lines only)
- ``mur_per_m2``, ``mur_per_km2`` - cost per unit area (polygons only)
- ``mur_per_<attribute>`` - cost per unit of an asset attribute, such as
  ``mur_per_capacity_mw``, which reads the ``capacity_mw`` column from the
  asset data and distributes it over splits by share

The currency is the part before ``_per_``. Units are case-insensitive, but
attribute names are matched exactly against asset data columns.
"""

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

LENGTH_UNITS = {"m": 1.0, "km": 1e-3}
AREA_UNITS = {"m2": 1.0, "km2": 1e-6}

SPLIT_ID = "split_id"
LENGTH_M = "length_m"
AREA_M2 = "area_m2"
SHARE = "share"
GEOM_KIND = "geom_kind"

GEOMETRY_KINDS = {
    "Point": "point",
    "MultiPoint": "point",
    "LineString": "line",
    "MultiLineString": "line",
    "LinearRing": "line",
    "Polygon": "polygon",
    "MultiPolygon": "polygon",
}


@dataclass(frozen=True)
class CostUnit:
    currency: str
    per: str | None

    @property
    def kind(self) -> str:
        """One of "asset", "length", "area" or "attribute"."""
        if self.per is None:
            return "asset"
        if self.per.lower() in LENGTH_UNITS:
            return "length"
        if self.per.lower() in AREA_UNITS:
            return "area"
        return "attribute"


def parse_cost_unit(unit: str) -> CostUnit:
    """Parse a cost unit string such as ``mur_per_km`` or ``usd``."""
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError(f"Cost unit must be a non-empty string, got {unit!r}")
    unit = unit.strip()
    currency, sep, per = unit.partition("_per_")
    if not currency:
        raise ValueError(f"Cost unit {unit!r} has no currency")
    if sep and not per:
        raise ValueError(f"Cost unit {unit!r} has nothing after '_per_'")
    return CostUnit(currency=currency.lower(), per=per if sep else None)


def default_measure_crs(frame: gpd.GeoDataFrame):
    """CRS in which to measure lengths and areas: an estimated UTM zone if ``frame`` is geographic."""
    if frame.crs is not None and frame.crs.is_geographic:
        return frame.estimate_utm_crs()
    return frame.crs


def geometry_kinds(geometry: gpd.GeoSeries) -> pd.Series:
    """Kind of each geometry: "point", "line", "polygon" or "other"."""
    return geometry.geom_type.map(GEOMETRY_KINDS).fillna("other")


def measure_splits(
    splits: gpd.GeoDataFrame,
    asset_id: str = "asset_id",
    measure_crs=None,
) -> gpd.GeoDataFrame:
    """Add split number, geometry kind, length, area and share-of-asset columns to splits.

    Lengths and areas are measured in ``measure_crs`` (metres), defaulting
    to an estimated UTM zone. ``share`` is each split's fraction of its
    parent asset's total length (lines) or area (polygons), or one over the
    number of splits (points, or degenerate geometries with zero measure).
    ``geom_kind`` is "point", "line", "polygon" or "other".
    """
    splits = splits.copy()
    splits[SPLIT_ID] = splits.groupby(asset_id, sort=False).cumcount().astype("int64")
    if splits.empty:
        splits[GEOM_KIND] = pd.Series(dtype="object")
        splits[LENGTH_M] = pd.Series(dtype="float64")
        splits[AREA_M2] = pd.Series(dtype="float64")
        splits[SHARE] = pd.Series(dtype="float64")
        return splits

    if measure_crs is None:
        measure_crs = default_measure_crs(splits)
    projected = splits.geometry.to_crs(measure_crs) if splits.crs is not None else splits.geometry

    kind = geometry_kinds(splits.geometry).to_numpy()
    is_line = kind == "line"
    is_area = kind == "polygon"
    splits[GEOM_KIND] = kind
    splits[LENGTH_M] = np.where(is_line, projected.length.to_numpy(), 0.0)
    splits[AREA_M2] = np.where(is_area, projected.area.to_numpy(), 0.0)

    measure = pd.Series(
        np.where(is_line, splits[LENGTH_M], np.where(is_area, splits[AREA_M2], 0.0)), index=splits.index
    )
    grouped = measure.groupby(splits[asset_id], sort=False)
    total = grouped.transform("sum")
    count = grouped.transform("size")
    splits[SHARE] = np.where(total > 0, measure / total.where(total > 0, 1.0), 1.0 / count)
    return splits


def _check_kind(splits: pd.DataFrame, measure: str, required: str, unit: CostUnit) -> None:
    """Raise if splits are not of the geometry kind a per-length or per-area cost needs.

    Zero-measure splits (slivers) of the right kind are fine: their quantity is 0. Without a
    ``geom_kind`` column, only splits that all have zero measure are rejected.
    """
    if GEOM_KIND in splits.columns:
        wrong = splits[GEOM_KIND] != required
        if wrong.any():
            found = sorted(set(splits.loc[wrong, GEOM_KIND]))
            raise ValueError(f"Cost unit per {unit.per} requires {required} geometries, found {found}")
    elif len(splits) and (splits[measure] <= 0).all():
        raise ValueError(f"Cost unit per {unit.per} requires {required} geometries with positive {measure}")


def split_quantity(splits: pd.DataFrame, unit: CostUnit) -> np.ndarray:
    """Quantity of each split, in the units of the per-unit cost.

    ``splits`` must have the columns added by :func:`measure_splits`. Splits
    with zero length or area (slivers) have quantity 0.
    """
    kind = unit.kind
    if kind == "asset":
        return splits[SHARE].to_numpy(dtype=float)
    if kind == "length":
        _check_kind(splits, LENGTH_M, "line", unit)
        return splits[LENGTH_M].to_numpy(dtype=float) * LENGTH_UNITS[unit.per.lower()]
    if kind == "area":
        _check_kind(splits, AREA_M2, "polygon", unit)
        return splits[AREA_M2].to_numpy(dtype=float) * AREA_UNITS[unit.per.lower()]
    if unit.per not in splits.columns:
        raise KeyError(f"Cost unit per {unit.per} requires an asset attribute column named {unit.per!r}")
    values = pd.to_numeric(splits[unit.per], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"Attribute {unit.per!r} must be finite and non-negative for all assets")
    return values * splits[SHARE].to_numpy(dtype=float)


def asset_totals(features: gpd.GeoDataFrame, asset_id: str = "asset_id", measure_crs=None) -> pd.Series:
    """Length (lines) or area (polygons) of each unsplit asset, in metres or square metres.

    Points and other geometries have total 0. Measured in ``measure_crs``,
    which should be the CRS used for :func:`measure_splits`.
    """
    if measure_crs is None:
        measure_crs = default_measure_crs(features)
    geometry = features.geometry.to_crs(measure_crs) if features.crs is not None else features.geometry
    kind = geometry_kinds(geometry)
    total = np.where(kind == "line", geometry.length, np.where(kind == "polygon", geometry.area, 0.0))
    return pd.Series(total, index=features[asset_id].to_numpy(), name="total")


def conservation_mismatches(
    expected: pd.Series, splits: pd.DataFrame, asset_id: str = "asset_id", rtol: float = 1e-3
) -> pd.DataFrame:
    """Assets whose split lengths/areas do not add up to the unsplit asset's.

    Parameters
    ----------
    expected : pandas.Series
        Unsplit length or area of each asset, indexed by asset id (see
        :func:`asset_totals`).
    splits : pandas.DataFrame
        Measured splits (see :func:`measure_splits`). Each asset's split
        total is its ``length_m`` for lines and ``area_m2`` for polygons.
    rtol : float
        Relative tolerance, default 0.1%.

    Returns
    -------
    pandas.DataFrame
        Columns ``expected``, ``actual`` and ``rel_diff`` (absolute difference
        relative to expected), indexed by asset id, for assets with
        ``rel_diff > rtol`` and positive expected total, worst first. Assets
        with no splits at all have actual total 0.
    """
    if GEOM_KIND in splits.columns:
        kind = splits[GEOM_KIND]
        measure = np.where(kind == "line", splits[LENGTH_M], np.where(kind == "polygon", splits[AREA_M2], 0.0))
    else:
        measure = splits[LENGTH_M].to_numpy() + splits[AREA_M2].to_numpy()
    actual = pd.Series(measure, index=splits[asset_id].to_numpy()).groupby(level=0).sum()
    actual = actual.reindex(expected.index, fill_value=0.0).astype(float)
    expected = expected.astype(float)
    positive = expected > 0
    rel_diff = (actual - expected).abs() / expected.where(positive)
    result = pd.DataFrame({"expected": expected, "actual": actual, "rel_diff": rel_diff})
    result = result[positive & (rel_diff > rtol)]
    result.index.name = asset_id
    return result.sort_values("rel_diff", ascending=False)
