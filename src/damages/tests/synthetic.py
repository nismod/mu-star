"""A tiny synthetic dataset with checkable direct damages, for end-to-end
tests.

The hazard is a 4x4 grid of 100 m cells in EPSG:32740 (metres), with epochs,
return periods and depths chosen for simplicity. Assets are drawn in "local"
coordinates ``(u, v)``, metres east and south of the grid's top left corner, so
that cell ``(i, j)`` (column, row) covers ``u`` in ``[100 i, 100 (i + 1)]`` and
``v`` in ``[100 j, 100 (j + 1)]``. No asset line lies on a cell edge.

A second hazard, "offset", has the same cell size on a grid shifted 45 m east and 25 m south of the first, so
every asset crossing cell edges of both grids is split at both. Layers are split once on both grids, and each
split has the depth of the cell it lies in on each grid.

Expected damages are calculated here, independently of the ``damages`` package:
geometries are intersected with cell boxes using shapely and curves are
evaluated with ``numpy.interp``.
"""

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import LineString, Point, Polygon, box

CRS = "EPSG:32740"
X0, Y0, CELL, N = 500_000.0, 7_000_000.0, 100.0, 4
EPOCHS = [2020, 2050, 2080]
RETURN_PERIODS = [10, 100, 1000]
HAZARD_KEY = "flood_synthetic"
HAZARD_KEY_OFFSET = "flood_offset"
# the offset grid's top left corner is this far east and south of the first grid's
OFFSET_U, OFFSET_V = 45.0, 25.0
METRIC = "depth_m"

# Depth (m) at return period 10 in epoch 2020, by row (north to south) and column. One cell is NaN.
BASE_DEPTH = np.array(
    [
        [0.0, 0.4, 1.0, 2.0],
        [0.0, 0.6, 1.2, np.nan],
        [0.2, 0.8, 0.0, 3.0],
        [0.0, 0.0, 0.5, 4.0],
    ]
)
# the same for the offset grid, with a NaN cell elsewhere
BASE_DEPTH_OFFSET = np.array(
    [
        [0.0, 0.0, 0.8, 1.6],
        [0.4, 1.0, 0.0, 2.4],
        [np.nan, 0.6, 1.4, 0.0],
        [0.2, 0.0, 3.0, 5.0],
    ]
)
RP_FACTOR = [1.0, 1.5, 2.0]
EPOCH_FACTOR = [1.0, 1.25, np.nan]  # nothing at all is known for 2080

# Piecewise linear damage curves: (depth, damage fraction)
CURVES = {
    "half": [(0, 0), (2, 1), (10, 1), (20, 1)],  # depth / 2, up to 1
    "quarter": [(0, 0), (4, 1), (10, 1), (20, 1)],  # depth / 4, up to 1
}

# asset type -> cost unit, cost, curve. Not listed here: "pylon_nv" (not vulnerable) and "mystery_pipe" (unknown)
COSTS = {
    "road_line": ("usd_per_km", 100_000.0, "half"),
    "canal": ("usd", 50_000.0, "quarter"),
    "building": ("usd_per_m2", 20.0, "half"),
    "reservoir": ("usd", 200_000.0, "quarter"),
    "substation": ("usd_per_capacity_mw", 1_000.0, "half"),
}
NOT_VULNERABLE = "pylon_nv"
UNKNOWN = "mystery_pipe"


@dataclass(frozen=True)
class Grid:
    """A square grid of ``N`` x ``N`` cells with a top left corner, and the depth at return period 10 in 2020."""

    x0: float
    y0: float
    base_depth: np.ndarray
    key: str

    @property
    def box(self):
        return box(self.x0, self.y0 - CELL * N, self.x0 + CELL * N, self.y0)

    def cell_box(self, i: int, j: int):
        return box(self.x0 + CELL * i, self.y0 - CELL * (j + 1), self.x0 + CELL * (i + 1), self.y0 - CELL * j)

    def cell_of(self, geometry_point) -> tuple[int, int]:
        """Grid cell (i, j) of a point, or (-1, -1) outside the grid."""
        i = int(np.floor((geometry_point.x - self.x0) / CELL))
        j = int(np.floor((self.y0 - geometry_point.y) / CELL))
        return (i, j) if 0 <= i < N and 0 <= j < N else (-1, -1)

    def depth_cube(self) -> np.ndarray:
        """Depth with dimensions (epoch, return_period, y, x)."""
        factor = np.asarray(EPOCH_FACTOR)[:, None, None, None] * np.asarray(RP_FACTOR)[None, :, None, None]
        return factor * self.base_depth

    def dataset(self, units: str = "m") -> xr.Dataset:
        from damages.geozarr import with_geozarr_attrs

        ds = xr.Dataset(
            {"depth": (("epoch", "return_period", "y", "x"), self.depth_cube(), {"units": units})},
            coords={
                "epoch": EPOCHS,
                "return_period": RETURN_PERIODS,
                "y": self.y0 - CELL * (np.arange(N) + 0.5),
                "x": self.x0 + CELL * (np.arange(N) + 0.5),
            },
        )
        return with_geozarr_attrs(ds.rio.write_crs(CRS))


GRID = Grid(X0, Y0, BASE_DEPTH, HAZARD_KEY)
GRID_OFFSET = Grid(X0 + OFFSET_U, Y0 - OFFSET_V, BASE_DEPTH_OFFSET, HAZARD_KEY_OFFSET)
GRIDS = [GRID, GRID_OFFSET]


def depth_cube() -> np.ndarray:
    """Depth of the first grid with dimensions (epoch, return_period, y, x)."""
    return GRID.depth_cube()


def hazard_dataset(units: str = "m", grid: Grid = GRID) -> xr.Dataset:
    return grid.dataset(units)


def _xy(u, v):
    return X0 + u, Y0 - v


def point(u, v):
    return Point(*_xy(u, v))


def line(*uv):
    return LineString([_xy(*p) for p in uv])


def polygon(*uv):
    return Polygon([_xy(*p) for p in uv])


def rectangle(u0, v0, u1, v1):
    return polygon((u0, v0), (u1, v0), (u1, v1), (u0, v1))


def _layer(rows) -> gpd.GeoDataFrame:
    frame = pd.DataFrame(rows, columns=["asset_id", "asset_type", "capacity_mw", "geometry"])
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=CRS)


def layers() -> dict[str, gpd.GeoDataFrame]:
    """Asset layers by name. Only point assets have a ``capacity_mw``."""
    nan = np.nan
    return {
        "line": _layer(
            [
                # along row 0, four cells: 80, 100, 100, 80 m
                ("L1", "road_line", nan, line((20, 50), (380, 50))),
                # sloping across (0,1), (1,1), (2,1), (2,2), (3,2), none of them at a cell corner
                ("L2", "road_line", nan, line((50, 120), (350, 270))),
                # wholly inside the NaN cell (3,1)
                ("L3", "road_line", nan, line((350, 110), (350, 190))),
                # 100 m west of the grid, then (0,3), (1,3) and (2,3)
                ("L4", "road_line", nan, line((-100, 350), (250, 350))),
                # down column 1: 70, 100, 100, 70 m
                ("C1", "canal", nan, line((150, 30), (150, 370))),
                # inside cell (2,0), a single split with share 1
                ("C2", "canal", nan, line((210, 20), (290, 80))),
            ]
        ),
        "polygon": _layer(
            [
                # four cells: (1,1), (2,1), (1,2), (2,2), areas 2100, 1800, 2800, 2400
                ("B1", "building", nan, rectangle(130, 170, 260, 240)),
                # inside cell (3,3), 3600 m2
                ("B2", "building", nan, rectangle(320, 320, 380, 380)),
                # 60 x 50 m in cell (3,0), 40 x 50 m east of the grid
                ("B3", "building", nan, rectangle(340, 20, 440, 70)),
                # a triangle in cells (0,0), (1,0), (0,1)
                ("R1", "reservoir", nan, polygon((20, 30), (180, 30), (20, 150))),
                # inside cell (2,2), which has no flooding
                ("R2", "reservoir", nan, rectangle(220, 220, 280, 280)),
                # 40 x 50 m in cell (3,1), 60 x 50 m east of the grid: only 40% of its whole-asset cost is exposed
                ("R3", "reservoir", nan, rectangle(360, 120, 460, 170)),
            ]
        ),
        "point": _layer(
            [
                ("S1", "substation", 10.0, point(150, 150)),
                ("S2", "substation", 20.0, point(350, 350)),
                ("S3", "substation", 30.0, point(350, 150)),  # NaN cell
                ("S4", "substation", 40.0, point(-50, 50)),  # outside the grid
                ("S5", "substation", 5.0, point(250, 50)),
            ]
        ),
        "mixed": _layer(
            [
                ("M1", "substation", 8.0, point(250, 350)),
                ("M2", NOT_VULNERABLE, nan, point(150, 150)),
                ("M3", "road_line", nan, line((170, 260), (330, 320))),
                ("M4", "building", nan, rectangle(30, 250, 90, 350)),
                ("M5", UNKNOWN, nan, line((20, 380), (80, 380))),
            ]
        ),
    }


def workbook_tables() -> dict[str, pd.DataFrame]:
    """Damage configuration, with one curve per asset type and no uncertainty."""
    types = [*COSTS, NOT_VULNERABLE]
    curve_rows = [(name, d, f) for name, points in CURVES.items() for d, f in points]
    return {
        "asset-type": pd.DataFrame({"asset_type": types}),
        "hazard-metric": pd.DataFrame({"intensity_metric": [METRIC], "unit": ["m"]}),
        "hazard": pd.DataFrame({"hazard": [HAZARD_KEY, HAZARD_KEY_OFFSET], "intensity_metric": [METRIC, METRIC]}),
        "asset-type-hazard-vulnerability": pd.DataFrame(
            {
                "asset_type": types,
                "hazard_intensity_metric": METRIC,
                "vulnerable": [1] * len(COSTS) + [0],
            }
        ),
        "asset-type-cost": pd.DataFrame(
            {
                "asset_type": list(COSTS),
                "cost_unit": [c[0] for c in COSTS.values()],
                "cost_mean": [c[1] for c in COSTS.values()],
                "cost_systemic_cv": 0.0,
                "cost_asset_cv": 0.0,
            }
        ),
        "damage-curves": pd.DataFrame({"curve_id": list(CURVES), "hazard_intensity_metric": METRIC}),
        "damage-curve-data": pd.DataFrame(curve_rows, columns=["curve_id", "hazard_intensity", "damage_fraction"]),
        "asset-type-damage-curve": pd.DataFrame(
            {
                "asset_type": list(COSTS),
                "hazard_intensity_metric": METRIC,
                "curve_id": [c[2] for c in COSTS.values()],
                "curve_weight": 1.0,
            }
        ),
    }


def write_workbook(path, tables=None) -> None:
    with pd.ExcelWriter(path) as writer:
        for sheet, table in (tables or workbook_tables()).items():
            table.to_excel(writer, sheet_name=sheet, index=False)


# Independent reference calculations


def _measure(geometry) -> float:
    return geometry.length if geometry.geom_type in ("LineString", "MultiLineString") else geometry.area


def pieces(geometry, grids: list[Grid] | None = None) -> list[tuple[tuple[tuple[int, int], ...], float]]:
    """Parts of a geometry in each combination of cells: (cells, length or area).

    ``cells`` has the (i, j) of the part on each grid, (-1, -1) where it is outside the grid. Points have measure
    1. The parts of a line or polygon outside a grid are kept whole, as (-1, -1): they are not dropped, whatever
    the geometry. Each grid is applied in turn, so pieces are the common refinement of the grids.
    """
    grids = GRIDS if grids is None else grids
    if geometry.geom_type == "Point":
        return [(tuple(g.cell_of(geometry) for g in grids), 1.0)]
    parts = [((), geometry)]
    for grid in grids:
        refined = []
        for cells, part in parts:
            for j in range(N):
                for i in range(N):
                    inside = part.intersection(grid.cell_box(i, j))
                    if _measure(inside) > 1e-9:
                        refined.append(((*cells, (i, j)), inside))
            outside = part.difference(grid.box)
            if _measure(outside) > 1e-9:
                refined.append(((*cells, (-1, -1)), outside))
        parts = refined
    return [(cells, _measure(part)) for cells, part in parts]


def whole(geometry) -> float:
    """Length or area of the whole asset, including any part outside the grids (1 for a point)."""
    if geometry.geom_type == "Point":
        return 1.0
    return _measure(geometry)


def curve_fraction(curve: str, depth: np.ndarray) -> np.ndarray:
    d, f = zip(*CURVES[curve])
    return np.interp(depth, d, f)


def ead(damage: np.ndarray, return_periods=RETURN_PERIODS) -> np.ndarray:
    """Expected annual damage from damage at each return period (last axis).

    Damage is linear in ln(probability) between return periods, and equal to the damage at the rarest return
    period for rarer events. There is no damage for events more frequent than the most frequent return period.
    """
    order = np.argsort(return_periods)[::-1]  # rarest first, so probability increases
    p = 1.0 / np.asarray(return_periods, dtype=float)[order]
    d = np.moveaxis(np.moveaxis(damage, -1, 0)[order], 0, -1)
    total = d[..., 0] * p[0]  # from probability 0 to the rarest event
    for k in range(len(p) - 1):
        pa, pb = p[k], p[k + 1]
        ln = np.log(pb / pa)
        # integral of da + (db - da) * ln(p / pa) / ln(pb / pa) dp, from pa to pb
        total = total + d[..., k] * (pb - pa) + (d[..., k + 1] - d[..., k]) * (pb * ln - (pb - pa)) / ln
    return total


@dataclass
class Expected:
    """Hand-calculated damages of the vulnerable assets in a layer.

    ``splits`` has one row per (asset, cell) piece, with ``fraction`` and ``cost`` arrays of shape
    (epoch, return_period) in columns of the same names, and ``ead`` of shape (epoch,).
    """

    splits: pd.DataFrame

    @property
    def assets(self) -> pd.DataFrame:
        return _sum_by(self.splits, ["asset_id", "asset_type"])

    @property
    def asset_types(self) -> pd.DataFrame:
        return _sum_by(self.splits, ["asset_type"])


def _sum_by(splits, keys):
    rows = []
    for key, group in splits.groupby(keys, sort=True):
        cost = np.sum(np.stack(group.cost.to_list()), axis=0)
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(keys, key)), "cost": cost, "ead": ead(cost)})
    return pd.DataFrame(rows)


def expected_damages(layer: gpd.GeoDataFrame, grid: Grid = GRID) -> Expected:
    """Damage from hazard on ``grid`` for every asset of a layer whose type is in ``COSTS``.

    Assets are split on all grids, as the workflow does, so there is a row for each combination of cells; the
    depth of each is that of its cell on ``grid``. ``cells`` is the (i, j) on each of ``GRIDS``.
    """
    depth = np.nan_to_num(grid.depth_cube(), nan=0.0)  # (epoch, rp, y, x), NaN is no flood
    which = GRIDS.index(grid)
    rows = []
    for asset in layer.itertuples():
        if asset.asset_type not in COSTS:
            continue
        unit, unit_cost, curve = COSTS[asset.asset_type]
        total = whole(asset.geometry)  # shares are of the unsplit asset
        for cells, measure in pieces(asset.geometry):
            i, j = cells[which]
            per = unit.split("_per_")[-1] if "_per_" in unit else None
            quantity = {
                None: measure / total,  # share of the asset
                "km": measure / 1000,
                "m2": measure,
                "capacity_mw": asset.capacity_mw * measure / total,
            }[per]
            d = depth[:, :, j, i] if i >= 0 else np.zeros(depth.shape[:2])
            fraction = curve_fraction(curve, d)
            cost = fraction * quantity * unit_cost
            # no information at all in an epoch means no answer
            missing = np.isnan(EPOCH_FACTOR)[:, None]
            fraction, cost = np.where(missing, np.nan, fraction), np.where(missing, np.nan, cost)
            rows.append(
                {
                    "asset_id": asset.asset_id,
                    "asset_type": asset.asset_type,
                    "i": i,
                    "j": j,
                    "cells": cells,
                    "measure": measure,
                    "share": measure / total,
                    "quantity": quantity,
                    "fraction": fraction,
                    "cost": cost,
                    "ead": ead(cost),
                }
            )
    return Expected(pd.DataFrame(rows))
