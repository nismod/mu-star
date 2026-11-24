"""The damage Zarr store, written incrementally, and the summary table.

The damage Zarr store for a layer and hazard has four groups:

- ``split``: per split, ``damage_fraction``, ``damage_cost`` and ``ead``
- ``asset``: per asset, ``damage_cost`` and ``ead``
- ``asset_type``: totals over all assets of each type, ``damage_cost`` and
  ``ead``
- ``layer``: totals over all assets of the layer, ``damage_cost`` and ``ead``

Each variable has a ``quantile`` dimension, the hazard's scenario dimensions
(e.g. ``rcp``, ``epoch``) and, except for ``ead``, ``return_period``. Split
geometries are not repeated here: the ``split`` dimension is aligned with the
rows of the split GeoParquet file, and labelled by ``asset_id`` and
``split_id`` coordinates.

:class:`DamageStore` creates every group and variable up front, as float32
filled with NaN, chunked along ``split`` and ``asset`` and whole along every
other dimension, then writes results one asset type and scenario combination
(case) at a time, so the outputs are never held in memory for the whole layer.
String coordinates are stored as variable-length strings.
"""

from collections.abc import Mapping, Sequence

import dask.array
import numpy as np
import pandas as pd
import xarray as xr
import zarr

from damages.calculate import QUANTILE, RETURN_PERIOD

SPLIT = "split"
ASSET = "asset"
ASSET_TYPE = "asset_type"
LAYER = "layer"
GROUPS = (SPLIT, ASSET, ASSET_TYPE, LAYER)
# variables of each group, and whether they have a return period dimension
VARIABLES = {
    SPLIT: {"damage_fraction": True, "damage_cost": True, "ead": False},
    ASSET: {"damage_cost": True, "ead": False},
    ASSET_TYPE: {"damage_cost": True, "ead": False},
    LAYER: {"damage_cost": True, "ead": False},
}
ATTRS = {
    "damage_fraction": {"long_name": "Damage fraction", "units": "1"},
    "damage_cost": {"long_name": "Direct damage (rehabilitation) cost"},
    "ead": {"long_name": "Expected annual damage (rehabilitation) cost, per year"},
}
# chunk length along split and asset: about 65 MB (uncompressed float32) per chunk with 12 scenario
# combinations, 9 return periods and 3 quantiles
CHUNK_SIZE = 50_000
# summary table level column values
LEVEL_ASSET_TYPE = "asset_type"
LEVEL_LAYER = "layer"


def _strings(values) -> np.ndarray:
    """Object array of str, which xarray and zarr v3 store as variable-length strings."""
    return np.asarray(values, dtype=str).astype(object)


def _coord(values) -> np.ndarray:
    values = np.asarray(values)
    return _strings(values) if values.dtype.kind in "OUS" else values


class DamageStore:
    """A damage Zarr store, created up front and filled in by :meth:`write`.

    Parameters
    ----------
    path : str
        Store path; any existing store is replaced.
    case_index : pandas.Index
        The ``case`` index from :func:`damages.calculate.stack_cases`: a MultiIndex over
        ``case_dims``, or a single case if there are none.
    case_dims : sequence of str
        Scenario dimensions, in output order.
    case_coords : mapping
        Coordinate values of each scenario dimension, in output order.
    return_periods, quantiles : sequence of float
    split_coords, asset_coords, type_coords : mapping of name to array
        Coordinates along ``split``, ``asset`` and ``asset_type``, including ``asset_type`` itself
        for the ``asset_type`` group.
    attrs : mapping
        Root group attributes.
    layer_attrs : mapping
        ``layer`` group attributes.
    chunk_size : int
        Chunk length along ``split`` and ``asset``.
    """

    def __init__(
        self,
        path: str,
        case_index: pd.Index,
        case_dims: Sequence[str],
        case_coords: Mapping[str, np.ndarray],
        return_periods: Sequence[float],
        quantiles: Sequence[float],
        split_coords: Mapping[str, np.ndarray],
        asset_coords: Mapping[str, np.ndarray],
        type_coords: Mapping[str, np.ndarray],
        attrs: Mapping | None = None,
        layer_attrs: Mapping | None = None,
        chunk_size: int = CHUNK_SIZE,
    ):
        self.path = str(path)
        self.case_dims = list(case_dims)
        case_coords = {d: np.asarray(case_coords[d]) for d in self.case_dims}
        # position of each case along each scenario dimension
        if self.case_dims:
            self._case_positions = list(
                zip(*[pd.Index(case_coords[d]).get_indexer(case_index.get_level_values(d)) for d in self.case_dims])
            )
            if any(p < 0 for position in self._case_positions for p in position):
                raise ValueError("case index values are missing from the scenario coordinates")
        else:
            self._case_positions = [()] * len(case_index)
        case_shape = [len(case_coords[d]) for d in self.case_dims]
        n_rp, n_q = len(return_periods), len(quantiles)
        common_coords = {
            **{d: _coord(v) for d, v in case_coords.items()},
            RETURN_PERIOD: np.asarray(return_periods, dtype=float),
            QUANTILE: np.asarray(quantiles, dtype=float),
        }
        entities = {
            SPLIT: (SPLIT, split_coords),
            ASSET: (ASSET, asset_coords),
            ASSET_TYPE: (ASSET_TYPE, type_coords),
            LAYER: (None, {}),
        }

        root = zarr.open_group(self.path, mode="w")
        root.attrs.update(dict(attrs or {}))
        for group, (dim, coords) in entities.items():
            n = len(next(iter(coords.values()))) if dim else 0
            data_vars = {}
            for name, with_rp in VARIABLES[group].items():
                dims = [*([dim] if dim else []), *self.case_dims, *([RETURN_PERIOD] if with_rp else []), QUANTILE]
                shape = [*([n] if dim else []), *case_shape, *([n_rp] if with_rp else []), n_q]
                chunks = tuple([max(1, min(chunk_size, n))] if dim else []) + tuple(shape[1:] if dim else shape)
                values = dask.array.full(shape, np.nan, dtype="float32", chunks=chunks)
                data_vars[name] = xr.Variable(dims, values, attrs=ATTRS[name], encoding={"chunks": chunks})
            ds_coords = {**common_coords, **{k: (dim, _coord(v)) for k, v in coords.items() if k != dim}}
            if dim and dim in coords:
                ds_coords[dim] = _coord(coords[dim])
            ds = xr.Dataset(data_vars, coords=ds_coords)
            ds[RETURN_PERIOD].attrs["units"] = "years"
            if group == LAYER:
                ds.attrs.update(dict(layer_attrs or {}))
            # metadata and coordinates only: unwritten chunks read as NaN
            ds.to_zarr(self.path, group=group, mode="w", compute=False, consolidated=False)
        self._root = zarr.open_group(self.path, mode="r+")
        self._arrays = {}

    def write(self, group: str, name: str, positions: np.ndarray | None, case: int, values: np.ndarray) -> None:
        """Write ``values`` for one case: ``(len(positions), [return_period,] quantile)``.

        ``positions`` are along the group's entity dimension (``split``, ``asset`` or
        ``asset_type``), in any order; None for the ``layer`` group.
        """
        key = f"{group}/{name}"
        if key not in self._arrays:
            self._arrays[key] = self._root[key]
        array = self._arrays[key]
        values = np.asarray(values, dtype="float32")
        case_position = self._case_positions[case]
        if positions is None:
            array[case_position] = values
            return
        positions = np.asarray(positions, dtype=np.int64)
        if not len(positions):
            return
        # contiguous runs are written as slices, others by orthogonal selection
        if (np.diff(positions) == 1).all():
            array[(slice(int(positions[0]), int(positions[-1]) + 1), *case_position)] = values
        else:
            array.oindex[(positions, *case_position)] = values

    def update_attrs(self, attrs: Mapping) -> None:
        self._root.attrs.update(dict(attrs))


def _quantile_columns(quantiles) -> list[str]:
    return [f"q_{(q * 100)}".removesuffix(".0") for q in quantiles]


def _metric_frames(totals: xr.Dataset) -> pd.DataFrame:
    frames = []
    for metric in ("damage_cost", "ead"):
        series = totals[metric].to_series()
        frame = series.unstack(QUANTILE) if series.index.nlevels > 1 else series.to_frame().T
        frame.columns = _quantile_columns(frame.columns)
        frame = frame.reset_index(drop=series.index.nlevels == 1)
        if RETURN_PERIOD not in frame:
            frame[RETURN_PERIOD] = np.nan
        frame.insert(0, "metric", metric)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def summary_table(totals: xr.Dataset, layer_totals: xr.Dataset | None = None, **labels) -> pd.DataFrame:
    """Long-form table of asset-type totals from an ``asset_type`` group, and of layer totals.

    Columns are the given labels (e.g. layer, peril, subperil), then ``metric`` ("damage_cost" or
    "ead"), ``level`` ("asset_type" for totals over the assets of one type, "layer" for totals over
    every asset of the layer), ``asset_type`` (empty for layer totals), any scenario dimensions,
    ``return_period`` (NaN for EAD), one column per quantile, and ``n_assets``, ``status`` and
    ``currency``.
    """
    extra_coords = [c for c in ("n_assets", "status", "currency") if c in totals.coords]
    table = _metric_frames(totals)
    coord_frame = totals[extra_coords].to_dataframe().reset_index()[[ASSET_TYPE, *extra_coords]]
    table = table.merge(coord_frame, on=ASSET_TYPE, how="left")
    table.insert(1, "level", LEVEL_ASSET_TYPE)
    if layer_totals is not None:
        layer = _metric_frames(layer_totals)
        layer.insert(1, "level", LEVEL_LAYER)
        layer.insert(2, ASSET_TYPE, np.nan)
        for key in ("n_assets", "status", "currency"):
            layer[key] = layer_totals.attrs.get(key, np.nan)
        table = pd.concat([table, layer[table.columns]], ignore_index=True)
    for i, (key, value) in enumerate(labels.items()):
        table.insert(i, key, value)
    return table


EMPTY_SUMMARY_COLUMNS = [
    "layer",
    "peril",
    "subperil",
    "metric",
    "level",
    "asset_type",
    "return_period",
    "n_assets",
    "status",
    "currency",
]


def empty_summary_table() -> pd.DataFrame:
    """Header-only summary table, for when there are no damage outputs to summarise.

    Has the label, metric, level and asset type columns of :func:`summary_table`, but
    no scenario dimension or quantile columns, as these are not known.
    """
    return pd.DataFrame(columns=EMPTY_SUMMARY_COLUMNS)
