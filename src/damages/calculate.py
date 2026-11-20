"""Sample direct damages for one asset type, over hazard scenarios.

This wraps :func:`snail.damages.calculate_direct_damages`, which works on a
table of splits with one column per intensity case (here, return period) and
an optional scenario column. Here we take intensities as an array with
dimensions ``(split, case, return_period)``, where ``case`` enumerates the
combinations of any other hazard dimensions (climate scenario, epoch, GCM...).

Three calls to snail are made, each with the same seed:

1. per split, damage fraction (unit quantity and cost, no cost uncertainty)
2. per split, damage cost and expected annual damage (EAD)
3. per asset, damage cost and EAD, with totals across all assets passed in

Passing every case as a snail "scenario" means curve choice and systemic cost
draws are shared across cases, so differences between scenarios are not
confounded by those draws. Hazard, damage-noise and asset cost draws are
independent between assets (or splits, in calls 1 and 2) and cases.

Marginal per-split quantiles are reported; per-split quantiles do not sum to
per-asset quantiles.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr
from snail.damages import DamageCurve, calculate_direct_damages

from damages.config import CostParameters

CASE = "case"
RETURN_PERIOD = "return_period"
QUANTILE = "quantile"


@dataclass
class DamageArrays:
    """Quantile arrays for one asset type. The last dimension is ``quantile``.

    - ``split_fraction``, ``split_cost``: (split, case, return_period, quantile)
    - ``split_ead``: (split, case, quantile)
    - ``asset_cost``: (asset, case, return_period, quantile)
    - ``asset_ead``: (asset, case, quantile)
    - ``total_cost``: (case, return_period, quantile)
    - ``total_ead``: (case, quantile)
    """

    split_fraction: np.ndarray
    split_cost: np.ndarray
    split_ead: np.ndarray
    asset_cost: np.ndarray
    asset_ead: np.ndarray
    total_cost: np.ndarray
    total_ead: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def filled(cls, n_split, n_asset, n_case, n_rp, n_q, value=np.nan, metadata=None):
        return cls(
            split_fraction=np.full((n_split, n_case, n_rp, n_q), value),
            split_cost=np.full((n_split, n_case, n_rp, n_q), value),
            split_ead=np.full((n_split, n_case, n_q), value),
            asset_cost=np.full((n_asset, n_case, n_rp, n_q), value),
            asset_ead=np.full((n_asset, n_case, n_q), value),
            total_cost=np.full((n_case, n_rp, n_q), value),
            total_ead=np.full((n_case, n_q), value),
            metadata=metadata or {},
        )


def quantile_columns(quantiles: Sequence[float]) -> list[str]:
    """Names snail gives to quantile summary columns."""
    return [f"q_{(q * 100)}".removesuffix(".0") for q in quantiles]


def clean_intensity(intensity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Replace missing or negative intensity with zero, flagging missing cases.

    A case is missing if it has no finite values at all, for example a
    combination of scenario and epoch that is not present in the hazard data.
    Otherwise, missing values (e.g. outside the hazard grid, or no data) are
    taken to mean no hazard.

    Returns the cleaned array and a boolean ``(case,)`` array of missing cases.
    """
    intensity = np.asarray(intensity, dtype=float)
    missing_case = ~np.isfinite(intensity).any(axis=(0, 2))
    cleaned = np.where(np.isfinite(intensity) & (intensity > 0), intensity, 0.0)
    return cleaned, missing_case


def _long_frame(intensity, entity, quantity, cost_mean, keep, rp_columns):
    """Long table of (entity, case) rows with one intensity column per RP."""
    split_idx, case_idx = np.nonzero(keep)
    frame = pd.DataFrame(
        {
            "entity": entity[split_idx],
            "case": case_idx,
            "quantity": quantity[split_idx],
            "cost_mean": cost_mean,
        }
    )
    values = intensity[split_idx, case_idx, :]
    for k, column in enumerate(rp_columns):
        frame[column] = values[:, k]
    return frame


def _fill(target, frame, quantile_cols, rp_values=None):
    """Write snail quantile summaries into an array indexed by entity/case[/rp]."""
    if frame is None or frame.empty:
        return
    entity = frame["entity"].to_numpy()
    case = frame["case"].to_numpy()
    values = frame[quantile_cols].to_numpy(dtype=float)
    if rp_values is None:
        target[entity, case, :] = values
    else:
        rp_pos = {rp: k for k, rp in enumerate(rp_values)}
        rp_idx = np.asarray([rp_pos[v] for v in frame["intensity_case"]])
        target[entity, case, rp_idx, :] = values


def sample_direct_damages(
    intensity: np.ndarray,
    asset_index: np.ndarray,
    quantity: np.ndarray,
    return_periods: Sequence[float],
    curves: Mapping[str, DamageCurve],
    curve_weights: Mapping[str, float],
    cost: CostParameters,
    *,
    n_assets: int | None = None,
    n_samples: int = 1000,
    quantiles: Sequence[float] = (0.05, 0.5, 0.95),
    seed: int | None = None,
    hazard_cv: float = 0.0,
    eta_sd: float = 0.0,
) -> DamageArrays:
    """Sample damages for splits of assets of one type.

    Parameters
    ----------
    intensity : numpy.ndarray
        Hazard intensity with dimensions ``(split, case, return_period)``.
        Missing values are treated as described in :func:`clean_intensity`.
    asset_index : numpy.ndarray
        Integer position of each split's parent asset, in ``[0, n_assets)``.
    quantity : numpy.ndarray
        Quantity of each split in the units of ``cost.mean``.
    return_periods : sequence of float
        Return period, in years, of each position along the last axis of
        ``intensity``.
    curves, curve_weights
        Damage curves and their relative weights, keyed by curve identifier.
    cost : CostParameters
        Rehabilitation cost per unit quantity, with uncertainty.

    Returns
    -------
    DamageArrays
        Quantiles of damage. Missing cases are NaN. Splits and assets with zero
        intensity in every return period of a case are zero (if all curves
        have zero damage at zero intensity) or sampled.
    """
    intensity, missing_case = clean_intensity(intensity)
    n_split, n_case, n_rp = intensity.shape
    asset_index = np.asarray(asset_index, dtype=int)
    quantity = np.asarray(quantity, dtype=float)
    if asset_index.shape != (n_split,) or quantity.shape != (n_split,):
        raise ValueError("asset_index and quantity must have one value per split")
    if len(return_periods) != n_rp:
        raise ValueError("return_periods must have one value per intensity return period")
    if n_assets is None:
        n_assets = int(asset_index.max()) + 1 if n_split else 0
    quantiles = list(quantiles)
    qcols = quantile_columns(quantiles)
    n_q = len(quantiles)
    rp_values = [float(rp) for rp in return_periods]
    rp_columns = [f"rp_{k}" for k in range(n_rp)]
    intensity_columns = dict(zip(rp_values, rp_columns))
    rp_map = dict(zip(rp_values, rp_values))

    out = DamageArrays.filled(n_split, n_assets, n_case, n_rp, n_q, value=0.0)

    # Skip rows that are certain to have zero damage, if curves allow
    zero_at_zero = all(float(np.max(curve.damage_fraction(np.zeros(1)))) == 0.0 for curve in curves.values())
    active = ~missing_case[None, :] & (
        (intensity > 0).any(axis=2) if zero_at_zero else np.ones((n_split, n_case), dtype=bool)
    )
    asset_active = np.zeros((n_assets, n_case), dtype=bool)
    np.logical_or.at(asset_active, asset_index, active)

    common = dict(
        asset_id="entity",
        quantity="quantity",
        scenario="case",
        intensity_columns=intensity_columns,
        hazard_cv=hazard_cv,
        curves=dict(curves),
        curve_weights=dict(curve_weights),
        eta_sd=eta_sd,
        n_samples=n_samples,
        seed=seed,
    )
    metadata: dict[str, Any] = {}

    if active.any():
        split_entity = np.arange(n_split)
        # TODO explain properly why we do multiple calls here

        # 1. damage fraction per split
        frame = _long_frame(intensity, split_entity, np.ones(n_split), 1.0, active, rp_columns)
        result = calculate_direct_damages(frame, cost_mean=1.0, quantiles=quantiles, **common)
        _fill(out.split_fraction, result.quantiles, qcols, rp_values)

        # 2. damage cost and EAD per split
        frame = _long_frame(intensity, split_entity, quantity, cost.mean, active, rp_columns)
        result = calculate_direct_damages(
            frame,
            cost_mean=cost.mean,
            cost_asset_cv=cost.asset_cv,
            cost_systemic_cv=cost.systemic_cv,
            quantiles=quantiles,
            return_periods=rp_map,
            ead_quantiles=quantiles,
            **common,
        )
        _fill(out.split_cost, result.quantiles, qcols, rp_values)
        _fill(out.split_ead, result.ead_quantiles, qcols)

        # 3. damage cost and EAD per asset, with totals
        keep = asset_active[asset_index]
        frame = _long_frame(intensity, asset_index, quantity, cost.mean, keep, rp_columns)
        result = calculate_direct_damages(
            frame,
            cost_mean=cost.mean,
            cost_asset_cv=cost.asset_cv,
            cost_systemic_cv=cost.systemic_cv,
            quantiles=quantiles,
            return_periods=rp_map,
            ead_quantiles=quantiles,
            aggregate_assets=True,
            **common,
        )
        _fill(out.asset_cost, result.quantiles, qcols, rp_values)
        _fill(out.asset_ead, result.ead_quantiles, qcols)
        if result.total_quantiles is not None:
            for row in result.total_quantiles.itertuples(index=False):
                row = row._asdict()
                out.total_cost[row["case"], rp_values.index(row["intensity_case"]), :] = [row[c] for c in qcols]
        if result.total_ead_quantiles is not None:
            for row in result.total_ead_quantiles.itertuples(index=False):
                row = row._asdict()
                out.total_ead[row["case"], :] = [row[c] for c in qcols]
        metadata = {k: v for k, v in result.metadata.items() if k not in ("intensity_columns", "return_periods")}

    for name in ("split_fraction", "split_cost", "split_ead", "asset_cost", "asset_ead"):
        getattr(out, name)[:, missing_case, ...] = np.nan
    out.total_cost[missing_case] = np.nan
    out.total_ead[missing_case] = np.nan
    metadata["n_active_split_cases"] = int(active.sum())
    out.metadata = metadata
    return out


def stack_cases(intensity: xr.DataArray, split_dim: str = "split") -> tuple[xr.DataArray, list[str]]:
    """Stack all dimensions other than split and return period into ``case``.

    Returns the stacked array, transposed to ``(split, case, return_period)``,
    and the names of the stacked dimensions (possibly empty).
    """
    if RETURN_PERIOD not in intensity.dims:
        raise ValueError(f"Hazard intensity must have a {RETURN_PERIOD!r} dimension")
    case_dims = [d for d in intensity.dims if d not in (split_dim, RETURN_PERIOD)]
    if not case_dims:
        stacked = intensity.expand_dims({CASE: [0]})
    else:
        stacked = intensity.stack({CASE: case_dims})
    return stacked.transpose(split_dim, CASE, RETURN_PERIOD), case_dims


def unstack_cases(data: xr.DataArray | xr.Dataset, case_dims: Sequence[str]):
    """Reverse :func:`stack_cases` on a result with a ``case`` dimension."""
    if not case_dims:
        return data.isel({CASE: 0}, drop=True)
    return data.unstack(CASE)
