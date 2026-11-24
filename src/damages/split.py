"""Split asset geometries once on all hazard grids.

Splitting a layer separately on each hazard grid gives pieces that are
consistent with one grid only. An asset split in half at one place on one grid
and at another place on another grid would have different pieces for each
hazard. Instead :func:`split_on_grids` splits each asset successively on every
distinct grid, so the final pieces are the common refinement of all the grids:
each piece lies within a single cell of every grid, and so has a single value
of every hazard intensity. Cell indices on every grid are recorded in columns
``index_i_<grid id>`` and ``index_j_<grid id>`` (see :func:`index_columns`).

Grids have different extents. Snail clips polygons to the grid it splits on
(the part outside has no split) but keeps the outside part of lines, with
index -1. Here, for every geometry kind, only the part of each geometry inside
a grid's extent is split on that grid, and the part outside is kept as it is,
so no part of any geometry is ever dropped and splitting is lossless.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import shapely
from pyproj import CRS
from snail.intersection import GridDefinition, apply_indices
from snail.overlay import split_features

from damages.quantity import GEOM_KIND, asset_totals, geometry_kinds, measure_splits

GRIDS_METADATA_KEY = "mu_star:grids"

logger = logging.getLogger(__name__)


def grid_id(position: int) -> str:
    """Identifier of the ``position``-th distinct grid: ``g0``, ``g1``, ..."""
    return f"g{position}"


def index_columns(grid_id: str) -> tuple[str, str]:
    """Names of the (column, row) cell index columns for a grid: ``index_i_g0``, ``index_j_g0``."""
    return f"index_i_{grid_id}", f"index_j_{grid_id}"


def grids_match(a: GridDefinition, b: GridDefinition, rtol: float = 1e-9) -> bool:
    """Whether two grids have the same CRS, shape and transform, the transform within a small tolerance.

    The tolerance on each transform element is ``rtol`` times the cell size (the largest of the first
    four elements) for the cell-size and rotation terms, and for the offset terms, ``rtol`` times the
    larger of the cell size and the offset, so grids from different files, with floats that differ in
    the last digits, are the same grid.
    """
    if (a.height, a.width) != (b.height, b.width):
        return False
    if CRS.from_user_input(a.crs) != CRS.from_user_input(b.crs):
        return False
    ta, tb = np.asarray(a.transform, dtype=float)[:6], np.asarray(b.transform, dtype=float)[:6]
    cell = max(np.abs(ta[[0, 1, 3, 4]]).max(), np.abs(tb[[0, 1, 3, 4]]).max())
    atol = rtol * np.maximum(cell, np.maximum(np.abs(ta), np.abs(tb)))
    return bool(np.all(np.abs(ta - tb) <= atol))


def unique_grids(grids: Sequence[GridDefinition]) -> tuple[list[GridDefinition], list[int]]:
    """Distinct grids, in order of first appearance, and the position of each input grid among them."""
    unique: list[GridDefinition] = []
    position = []
    for grid in grids:
        for k, other in enumerate(unique):
            if grids_match(grid, other):
                position.append(k)
                break
        else:
            unique.append(grid)
            position.append(len(unique) - 1)
    return unique, position


def grid_definitions(grids: Sequence[GridDefinition]) -> list[dict[str, Any]]:
    """JSON-serialisable definitions: ``id``, ``crs`` (WKT2), ``shape`` ``[height, width]`` and ``transform``."""
    return [
        {
            "id": grid_id(k),
            "crs": CRS.from_user_input(grid.crs).to_wkt(),
            "shape": [int(grid.height), int(grid.width)],
            "transform": [float(v) for v in tuple(grid.transform)[:6]],
        }
        for k, grid in enumerate(grids)
    ]


def grid_from_definition(definition: dict[str, Any]) -> GridDefinition:
    height, width = definition["shape"]
    return GridDefinition(
        crs=CRS.from_user_input(definition["crs"]),
        width=int(width),
        height=int(height),
        transform=tuple(float(v) for v in definition["transform"]),
    )


def write_grids_metadata(path, definitions: list[dict[str, Any]]) -> None:
    """Record grid definitions in the schema metadata of a GeoParquet file, keeping its other metadata."""
    table = pq.read_table(path)
    metadata = {**(table.schema.metadata or {}), GRIDS_METADATA_KEY.encode(): json.dumps(definitions).encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)


def read_grids_metadata(path) -> list[dict[str, Any]]:
    """Grid definitions recorded in a split file by :func:`write_grids_metadata`."""
    metadata = pq.read_schema(path).metadata or {}
    try:
        return json.loads(metadata[GRIDS_METADATA_KEY.encode()])
    except KeyError:
        raise KeyError(f"{path} has no {GRIDS_METADATA_KEY!r} metadata; was it written by damage_split.py?") from None


def find_grid_id(definitions: list[dict[str, Any]], grid: GridDefinition) -> str | None:
    """Identifier of the recorded grid that matches ``grid``, or None."""
    for definition in definitions:
        if grids_match(grid_from_definition(definition), grid):
            return definition["id"]
    return None


def grid_extent(grid: GridDefinition) -> shapely.Polygon:
    """Outline of a grid in its own CRS."""
    a, b, c, d, e, f = (float(v) for v in tuple(grid.transform)[:6])
    corners = [(0, 0), (grid.width, 0), (grid.width, grid.height), (0, grid.height)]
    return shapely.Polygon([(a * i + b * j + c, d * i + e * j + f) for i, j in corners])


def _parts_of_dimension(geometry, dimension: int) -> list:
    """Parts of a geometry with the given dimension (1 lines, 2 polygons), dropping points and empty parts."""
    parts = shapely.get_parts(geometry)
    return [p for p in parts if not shapely.is_empty(p) and shapely.get_dimensions(p) == dimension]


def _explode_parts(frame: gpd.GeoDataFrame, parts: pd.Series) -> gpd.GeoDataFrame:
    """Rows of ``frame`` repeated for each part in the matching element of ``parts``."""
    rows = [(k, part) for k, ps in enumerate(parts) for part in ps]
    if not rows:
        return frame.iloc[:0].copy()
    positions, geometry = zip(*rows)
    out = frame.iloc[list(positions)].copy()
    out.geometry = gpd.GeoSeries(list(geometry), index=out.index, crs=frame.crs)
    return out.reset_index(drop=True)


def _split_inside(frame: gpd.GeoDataFrame, grid: GridDefinition, columns: list[str]) -> gpd.GeoDataFrame:
    """Split pieces of one geometry kind (lines or polygons), wholly inside the grid's extent, on the grid."""
    if frame.empty:
        return frame
    # snail returns pieces in the CRS of its input; pieces in different CRSs are not mixed here
    out = split_features(frame, grid)
    return out[[*columns, "geometry"]].reset_index(drop=True)


def _split_once(pieces: gpd.GeoDataFrame, grid: GridDefinition, columns: list[str]) -> gpd.GeoDataFrame:
    """Split pieces on one grid: the part inside its extent along the cells, the part outside kept whole."""
    if pieces.empty:
        return pieces
    original_crs = pieces.crs
    same_crs = (
        original_crs is None or grid.crs is None or CRS.from_user_input(original_crs) == CRS.from_user_input(grid.crs)
    )
    work = pieces if same_crs else pieces.to_crs(grid.crs)
    extent = grid_extent(grid)
    geometry = work.geometry.to_numpy()
    kinds = geometry_kinds(work.geometry).to_numpy()
    inside = shapely.intersection(geometry, extent)
    outside = shapely.difference(geometry, extent)

    is_point = kinds == "point"
    results = []
    # points are never split, and stay as they are
    if is_point.any():
        results.append(work[is_point])
    for kind, dimension in (("line", 1), ("polygon", 2)):
        selected = np.flatnonzero(kinds == kind)
        if not len(selected):
            continue
        sub = work.iloc[selected]
        inside_parts = pd.Series([_parts_of_dimension(inside[k], dimension) for k in selected])
        outside_parts = pd.Series([_parts_of_dimension(outside[k], dimension) for k in selected])
        results.append(_split_inside(_explode_parts(sub, inside_parts), grid, columns))
        results.append(_explode_parts(sub, outside_parts))
    other = ~is_point & ~np.isin(kinds, ["line", "polygon"])
    if other.any():
        results.append(work[other])
    results = [r[[*columns, "geometry"]] for r in results if len(r)]
    if not results:
        return work.iloc[:0][[*columns, "geometry"]]
    out = gpd.GeoDataFrame(pd.concat(results, ignore_index=True), geometry="geometry", crs=work.crs)
    # keep the pieces of each parent together, in the order of the parents
    out = out.sort_values("_parent", kind="stable", ignore_index=True)
    return out if same_crs else out.to_crs(original_crs)


def _cell_indices(pieces: gpd.GeoDataFrame, grid: GridDefinition, i_col: str, j_col: str) -> pd.DataFrame:
    """Cell indices of pieces on a grid (snail's convention), -1 for pieces outside the grid's extent."""
    if pieces.empty:
        return pd.DataFrame({i_col: np.array([], dtype="int64"), j_col: np.array([], dtype="int64")})
    same_crs = (
        pieces.crs is None or grid.crs is None or CRS.from_user_input(pieces.crs) == CRS.from_user_input(grid.crs)
    )
    work = pieces[["geometry"]] if same_crs else pieces[["geometry"]].to_crs(grid.crs)
    indexed = apply_indices(work, grid, index_i=i_col, index_j=j_col)
    result = indexed[[i_col, j_col]].astype("int64")
    # snail indexes a piece by its midpoint or centroid, which for a piece wrapping around the grid
    # (for example the part of a polygon outside it) can be inside; pieces are inside or outside as a whole
    representative = shapely.point_on_surface(work.geometry.to_numpy())
    inside = shapely.intersects(representative, grid_extent(grid))
    result.loc[~inside, [i_col, j_col]] = -1
    return result.reset_index(drop=True)


def split_on_grids(
    features: gpd.GeoDataFrame,
    grids: Sequence[GridDefinition],
    asset_id: str = "asset_id",
) -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
    """Split features once on all grids, so that every piece lies within one cell of every grid.

    Identical grids (same CRS, shape and transform, within a small tolerance) are used once. Features
    are split on each distinct grid in turn, using snail's ``split_features``, so that the pieces are
    the common refinement of the grids: an asset that crosses a cell edge of one grid at one place and
    of another grid at another place is cut in both places, into three pieces. Only the part of each
    geometry inside a grid's extent is split on that grid; the part outside is kept as one piece (one
    for each connected part), so no geometry is dropped, whatever its kind. Points are never split.

    Parameters
    ----------
    features : geopandas.GeoDataFrame
        Features with a CRS and an ``asset_id`` column. Multi-part geometries are exploded.
    grids : sequence of snail.intersection.GridDefinition
        Grids to split on, which may differ in extent, resolution and CRS.

    Returns
    -------
    splits : geopandas.GeoDataFrame
        One row per piece, with the parent's attributes, ``split_id``, ``geom_kind``, ``length_m``,
        ``area_m2`` and ``share`` (see :func:`damages.quantity.measure_splits`; shares are of the unsplit
        asset and sum to 1) and, for each distinct grid ``g0``, ``g1``, ..., columns ``index_i_g0``,
        ``index_j_g0`` and so on: the cell of the piece, -1 if it is outside the grid.
    definitions : list of dict
        The distinct grids, as given by :func:`grid_definitions`, in the order of the index columns.
    """
    unique, _ = unique_grids(grids)
    definitions = grid_definitions(unique)
    totals = asset_totals(features, asset_id)

    columns = [c for c in features.columns if c != features.geometry.name]
    base = features[columns].copy()
    base["geometry"] = features.geometry.to_numpy()
    base = gpd.GeoDataFrame(base, geometry="geometry", crs=features.crs)
    base = base.explode(ignore_index=True)
    base["_parent"] = np.arange(len(base))
    work_columns = [*columns, "_parent"]
    pieces = base
    for grid in unique:
        pieces = _split_once(pieces, grid, work_columns)

    pieces = pieces.reset_index(drop=True)
    index_data = {}
    for definition, grid in zip(definitions, unique):
        i_col, j_col = index_columns(definition["id"])
        indices = _cell_indices(pieces, grid, i_col, j_col)
        index_data[i_col], index_data[j_col] = indices[i_col], indices[j_col]
    pieces = pieces.drop(columns="_parent")
    if index_data:
        pieces = pd.concat([pieces, pd.DataFrame(index_data, index=pieces.index)], axis=1)
    splits = gpd.GeoDataFrame(pieces, geometry="geometry", crs=features.crs)
    splits = measure_splits(splits, asset_id=asset_id, totals=totals)
    if GEOM_KIND not in splits.columns:  # pragma: no cover
        raise AssertionError("measure_splits did not add geom_kind")
    return splits, definitions
