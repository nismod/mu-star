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


def measure_splits(
    splits: gpd.GeoDataFrame,
    asset_id: str = "asset_id",
    measure_crs=None,
) -> gpd.GeoDataFrame:
    """Add split number, length, area and share-of-asset columns to splits.

    Lengths and areas are measured in ``measure_crs`` (metres), defaulting
    to an estimated UTM zone. ``share`` is each split's fraction of its
    parent asset's total length (lines) or area (polygons), or one over the
    number of splits (points, or degenerate geometries with zero measure).
    """
    splits = splits.copy()
    splits[SPLIT_ID] = splits.groupby(asset_id, sort=False).cumcount().astype("int64")
    if splits.empty:
        splits[LENGTH_M] = pd.Series(dtype="float64")
        splits[AREA_M2] = pd.Series(dtype="float64")
        splits[SHARE] = pd.Series(dtype="float64")
        return splits

    if measure_crs is None:
        measure_crs = splits.estimate_utm_crs() if splits.crs is not None and splits.crs.is_geographic else splits.crs
    projected = splits.geometry.to_crs(measure_crs) if splits.crs is not None else splits.geometry

    geom_type = splits.geometry.geom_type.to_numpy()
    is_line = np.isin(geom_type, ["LineString", "MultiLineString"])
    is_area = np.isin(geom_type, ["Polygon", "MultiPolygon"])
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


def split_quantity(splits: pd.DataFrame, unit: CostUnit) -> np.ndarray:
    """Quantity of each split, in the units of the per-unit cost.

    ``splits`` must have the columns added by :func:`measure_splits`.
    """
    kind = unit.kind
    if kind == "asset":
        return splits[SHARE].to_numpy(dtype=float)
    if kind == "length":
        if (splits[LENGTH_M] <= 0).any():
            raise ValueError(f"Cost unit per {unit.per} requires line geometries with positive length")
        return splits[LENGTH_M].to_numpy(dtype=float) * LENGTH_UNITS[unit.per.lower()]
    if kind == "area":
        if (splits[AREA_M2] <= 0).any():
            raise ValueError(f"Cost unit per {unit.per} requires polygon geometries with positive area")
        return splits[AREA_M2].to_numpy(dtype=float) * AREA_UNITS[unit.per.lower()]
    if unit.per not in splits.columns:
        raise KeyError(f"Cost unit per {unit.per} requires an asset attribute column named {unit.per!r}")
    values = pd.to_numeric(splits[unit.per], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"Attribute {unit.per!r} must be finite and non-negative for all assets")
    return values * splits[SHARE].to_numpy(dtype=float)
