"""Assemble damage quantile arrays into labelled xarray datasets.

The damage Zarr store for a layer and hazard has three groups:

- ``split``: per split, ``damage_fraction``, ``damage_cost`` and ``ead``
- ``asset``: per asset, ``damage_cost`` and ``ead``
- ``asset_type``: totals over all assets of each type, ``damage_cost`` and
  ``ead``

Each variable has a ``quantile`` dimension, the hazard's scenario dimensions
(e.g. ``rcp``, ``epoch``) and, except for ``ead``, ``return_period``. Split
geometries are not repeated here: the ``split`` dimension is aligned with the
rows of the split GeoParquet file, and labelled by ``asset_id`` and
``split_id`` coordinates.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd
import xarray as xr

from damages.calculate import CASE, QUANTILE, RETURN_PERIOD, DamageArrays, unstack_cases

SPLIT = "split"
ASSET = "asset"
ASSET_TYPE = "asset_type"


def empty_arrays(n_split, n_asset, n_type, n_case, n_rp, n_q) -> dict[str, np.ndarray]:
    """NaN-filled output arrays, keyed by ``<group>/<variable>``."""
    return {
        "split/damage_fraction": np.full((n_split, n_case, n_rp, n_q), np.nan),
        "split/damage_cost": np.full((n_split, n_case, n_rp, n_q), np.nan),
        "split/ead": np.full((n_split, n_case, n_q), np.nan),
        "asset/damage_cost": np.full((n_asset, n_case, n_rp, n_q), np.nan),
        "asset/ead": np.full((n_asset, n_case, n_q), np.nan),
        "asset_type/damage_cost": np.full((n_type, n_case, n_rp, n_q), np.nan),
        "asset_type/ead": np.full((n_type, n_case, n_q), np.nan),
    }


def insert_arrays(
    arrays: dict[str, np.ndarray],
    result: DamageArrays,
    split_positions: np.ndarray,
    asset_positions: np.ndarray,
    type_position: int,
) -> None:
    """Copy the results for one asset type into the full output arrays."""
    arrays["split/damage_fraction"][split_positions] = result.split_fraction
    arrays["split/damage_cost"][split_positions] = result.split_cost
    arrays["split/ead"][split_positions] = result.split_ead
    arrays["asset/damage_cost"][asset_positions] = result.asset_cost
    arrays["asset/ead"][asset_positions] = result.asset_ead
    arrays["asset_type/damage_cost"][type_position] = result.total_cost
    arrays["asset_type/ead"][type_position] = result.total_ead


def build_datasets(
    arrays: dict[str, np.ndarray],
    case_index: pd.Index,
    case_dims: Sequence[str],
    return_periods: Sequence[float],
    quantiles: Sequence[float],
    split_coords: dict[str, np.ndarray],
    asset_coords: dict[str, np.ndarray],
    type_coords: dict[str, np.ndarray],
) -> dict[str, xr.Dataset]:
    """Build one dataset per output group, unstacking scenario cases."""
    entity_dims = {"split": SPLIT, "asset": ASSET, "asset_type": ASSET_TYPE}
    entity_coords = {"split": split_coords, "asset": asset_coords, "asset_type": type_coords}
    attrs = {
        "damage_fraction": {"long_name": "Damage fraction", "units": "1"},
        "damage_cost": {"long_name": "Direct damage (rehabilitation) cost"},
        "ead": {"long_name": "Expected annual damage (rehabilitation) cost, per year"},
    }
    if case_dims:
        # stack_cases always produces a MultiIndex, even for one dimension
        case_coords = xr.Coordinates.from_pandas_multiindex(case_index, CASE)
    else:
        case_coords = xr.Coordinates({CASE: np.asarray(case_index)})

    datasets = {}
    for group, dim in entity_dims.items():
        data_vars = {}
        for key, values in arrays.items():
            g, name = key.split("/")
            if g != group:
                continue
            dims = (dim, CASE, RETURN_PERIOD, QUANTILE) if values.ndim == 4 else (dim, CASE, QUANTILE)
            data_vars[name] = xr.Variable(dims, values, attrs=attrs[name])
        coords = {k: (dim, np.asarray(v)) for k, v in entity_coords[group].items()}
        coords[RETURN_PERIOD] = np.asarray(return_periods, dtype=float)
        coords[QUANTILE] = np.asarray(quantiles, dtype=float)
        ds = xr.Dataset(data_vars, coords=coords).assign_coords(case_coords)
        ds = unstack_cases(ds, case_dims)
        ds[RETURN_PERIOD].attrs["units"] = "years"
        dims_order = [dim, *case_dims, RETURN_PERIOD, QUANTILE]
        datasets[group] = ds.transpose(*[d for d in dims_order if d in ds.dims])
    return datasets


def summary_table(totals: xr.Dataset, **labels) -> pd.DataFrame:
    """Long-form table of asset-type totals from an ``asset_type`` group.

    Columns are the given labels (e.g. layer, peril, subperil), then
    ``asset_type``, any scenario dimensions, ``metric`` ("damage_cost" or
    "ead"), ``return_period`` (NaN for EAD), and one column per quantile.
    """
    frames = []
    extra_coords = [c for c in ("n_assets", "status", "currency") if c in totals.coords]
    for metric in ("damage_cost", "ead"):
        da = totals[metric]
        frame = da.to_series().unstack(QUANTILE)
        frame.columns = [f"q_{(q * 100)}".removesuffix(".0") for q in frame.columns]
        frame = frame.reset_index()
        if RETURN_PERIOD not in frame:
            frame[RETURN_PERIOD] = np.nan
        frame.insert(0, "metric", metric)
        frames.append(frame)
    table = pd.concat(frames, ignore_index=True)
    coord_frame = totals[extra_coords].to_dataframe().reset_index()[[ASSET_TYPE, *extra_coords]]
    table = table.merge(coord_frame, on=ASSET_TYPE, how="left")
    for i, (key, value) in enumerate(labels.items()):
        table.insert(i, key, value)
    return table
