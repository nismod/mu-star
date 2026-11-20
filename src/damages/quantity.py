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

Lengths and areas are measured in metres and square metres: planar if the
data are in a projected CRS with metre units, otherwise geodesic, on the
WGS84 ellipsoid (see :func:`measure_geometries`).
"""

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Geod

WGS84_GEOD = Geod(ellps="WGS84")
GEOGRAPHIC_CRS = "EPSG:4326"

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


def geometry_kinds(geometry: gpd.GeoSeries) -> pd.Series:
    """Kind of each geometry: "point", "line", "polygon" or "other"."""
    return geometry.geom_type.map(GEOMETRY_KINDS).fillna("other")


def is_metre_projected(crs) -> bool:
    """Whether a CRS is projected, with all axes in metres, so planar measures are in metres."""
    return crs.is_projected and all(axis.unit_name in ("metre", "meter") for axis in crs.axis_info)


def measure_geometries(geometry: gpd.GeoSeries) -> tuple[np.ndarray, np.ndarray]:
    """Length (lines) and area (polygons) of each geometry, in metres and square metres.

    Measured planar if the CRS is projected with metre units (see
    :func:`is_metre_projected`), otherwise geodesic (see :func:`geodesic_measures`).
    Other geometry kinds, and empty geometries, have length and area 0.
    """
    if geometry.crs is None:
        raise ValueError("Geometries need a CRS to measure lengths and areas")
    if not is_metre_projected(geometry.crs):
        return geodesic_measures(geometry)
    kind = _measured_kinds(geometry)
    length = np.where(kind == "line", geometry.length.to_numpy(), 0.0)
    area = np.where(kind == "polygon", geometry.area.to_numpy(), 0.0)
    return length, area


def _measured_kinds(geometry: gpd.GeoSeries) -> np.ndarray:
    return np.where(geometry.is_empty.to_numpy(), "other", geometry_kinds(geometry).to_numpy())


def geodesic_measures(geometry: gpd.GeoSeries) -> tuple[np.ndarray, np.ndarray]:
    """Geodesic length (lines) and area (polygons) of each geometry, in metres and square metres.

    Other geometry kinds have length and area 0. Geometries must have a CRS.
    """
    if geometry.crs is None:
        raise ValueError("Geometries need a CRS to measure geodesic lengths and areas")
    kind = _measured_kinds(geometry)
    # pyproj gives signed areas, and only subtracts holes from polygons that are
    # oriented consistently: exteriors counter-clockwise, interiors clockwise
    lonlat = shapely.orient_polygons(geometry.to_crs(GEOGRAPHIC_CRS).to_numpy(), exterior_cw=False)
    length = np.zeros(len(lonlat))
    area = np.zeros(len(lonlat))
    for k in np.flatnonzero(kind == "line"):
        length[k] = WGS84_GEOD.geometry_length(lonlat[k])
    for k in np.flatnonzero(kind == "polygon"):
        area[k] = abs(WGS84_GEOD.geometry_area_perimeter(lonlat[k])[0])
    return length, area


def measure_splits(
    splits: gpd.GeoDataFrame,
    asset_id: str = "asset_id",
    totals: pd.Series | None = None,
) -> gpd.GeoDataFrame:
    """Add split number, geometry kind, length, area and share-of-asset columns to splits.

    Lengths and areas are in metres and square metres (see
    :func:`measure_geometries`). ``share`` is each split's fraction of its
    parent asset's total length (lines) or area (polygons), or one over the
    number of splits (points, or degenerate geometries with zero measure).
    ``geom_kind`` is "point", "line", "polygon" or "other".

    ``totals`` gives the length or area of each unsplit asset, indexed by
    asset identifier (see :func:`asset_totals`). Pass it so that shares are
    of the whole asset even if part of it has no split, as for a polygon
    partly outside the grid; shares of such an asset sum to less than one.
    Without it, shares are of the total over each asset's splits.
    """
    splits = splits.copy()
    splits[SPLIT_ID] = splits.groupby(asset_id, sort=False).cumcount().astype("int64")
    if splits.empty:
        splits[GEOM_KIND] = pd.Series(dtype="object")
        splits[LENGTH_M] = pd.Series(dtype="float64")
        splits[AREA_M2] = pd.Series(dtype="float64")
        splits[SHARE] = pd.Series(dtype="float64")
        return splits

    kind = geometry_kinds(splits.geometry).to_numpy()
    is_line = kind == "line"
    is_area = kind == "polygon"
    splits[GEOM_KIND] = kind
    splits[LENGTH_M], splits[AREA_M2] = measure_geometries(splits.geometry)

    measure = pd.Series(
        np.where(is_line, splits[LENGTH_M], np.where(is_area, splits[AREA_M2], 0.0)), index=splits.index
    )
    grouped = measure.groupby(splits[asset_id], sort=False)
    total = grouped.transform("sum")
    count = grouped.transform("size")
    if totals is not None:
        whole = pd.Series(splits[asset_id].map(totals).to_numpy(dtype=float), index=splits.index)
        # fall back to the split total where the unsplit total is unknown or zero
        total = whole.where(whole > 0, total)
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


def asset_totals(features: gpd.GeoDataFrame, asset_id: str = "asset_id") -> pd.Series:
    """Length (lines) or area (polygons) of each unsplit asset, in metres or square metres.

    Measured as for :func:`measure_splits` (see :func:`measure_geometries`).

    Points and other geometries have total 0.
    """
    length, area = measure_geometries(features.geometry)
    return pd.Series(length + area, index=features[asset_id].to_numpy(), name="total")


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
