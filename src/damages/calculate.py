"""Sample direct damages for one asset type, one scenario combination at a time.

Hazard intensity comes as an array with dimensions ``(split, case,
return_period)``, where ``case`` enumerates the combinations of any other
hazard dimensions (climate scenario, epoch, GCM...), see :func:`stack_cases`.
:func:`simulate_asset_type` makes one call to
:func:`snail.damages.simulate_direct_damages` per case, which samples damages
once and summarises them per split, per asset and in total, from the same
samples, and can return the total samples so that totals can be added up
across asset types.

Random draws in snail are keyed rather than drawn in sequence:

- each asset's local draws (hazard, damage noise and asset cost factors) depend
  only on the seed and the asset's key. Keys are a 64-bit hash of the asset id
  (:func:`asset_keys`), so an asset gets the same draws in every case (common
  random numbers across scenarios), in every run, and whatever the order of
  assets or layers, or which other assets are computed
- curve choice and systemic cost draws depend on the seed and, optionally, a
  systemic key (:func:`systemic_key`). With no key, they are shared by every
  call with the same seed: all cases, asset types, layers and hazards
"""

from collections.abc import Iterator, Mapping, Sequence

import numpy as np
import pandas as pd
import xarray as xr
from snail.damages import DamageCurve, DirectDamageArrays, simulate_direct_damages

from damages.config import CostParameters

CASE = "case"
RETURN_PERIOD = "return_period"
QUANTILE = "quantile"

SHARED = "shared"
INDEPENDENT = "independent"
SYSTEMIC_DRAWS = (SHARED, INDEPENDENT)


def _hash_strings(values) -> np.ndarray:
    """Stable 64-bit hashes of strings: pandas' SipHash of the UTF-8 text, with its default key."""
    return pd.util.hash_array(np.asarray(values, dtype=str).astype(object))


def asset_keys(asset_ids: Sequence) -> np.ndarray:
    """Keys of snail's per-asset random draws: a 64-bit hash of each asset id, as a string.

    Uses :func:`pandas.util.hash_array`, so keys depend only on the ids, not on their order, the
    other assets, the case, the layer or the run. Ids must be unique; a collision of two distinct
    ids (probability about ``n**2 / 2**65``) makes snail raise an error.
    """
    return _hash_strings(asset_ids)


def systemic_key(asset_type: str) -> int:
    """Key of snail's curve choice and systemic cost draws for an asset type: a 64-bit hash of its name.

    Used with ``systemic_draws = "independent"``, so each asset type gets its own, independent,
    curve choice and systemic cost draws, the same in every case, layer, hazard and run.
    """
    return int(_hash_strings([asset_type])[0])


def clean_intensity(intensity: np.ndarray) -> np.ndarray:
    """Replace missing (NaN) or negative intensity with zero, as float64.

    Used within a case that has hazard data, where a missing value (for example outside the hazard
    grid, or a cell with no data) means no hazard. Cases with no data at all are not computed, see
    :func:`simulate_asset_type`.
    """
    intensity = np.asarray(intensity, dtype=float)
    return np.where(np.isfinite(intensity) & (intensity > 0), intensity, 0.0)


def simulate_asset_type(
    intensity: np.ndarray,
    asset_index: np.ndarray,
    quantity: np.ndarray,
    return_periods: Sequence[float],
    curves: Mapping[str, DamageCurve],
    curve_weights: Mapping[str, float],
    cost: CostParameters,
    *,
    keys: np.ndarray,
    has_data: np.ndarray | None = None,
    n_samples: int = 1000,
    quantiles: Sequence[float] = (0.05, 0.5, 0.95),
    seed: int = 0,
    hazard_cv: float = 0.0,
    eta_sd: float = 0.0,
    systemic_key: int | None = None,
) -> Iterator[tuple[int, DirectDamageArrays | None]]:
    """Sample damages to the splits of assets of one type, one case at a time.

    Makes one call to :func:`snail.damages.simulate_direct_damages` per case, with split, asset and
    total levels and ``keep_total_samples=True``, and yields ``(case, result)`` as each is done, so
    that the caller can write results and add up total samples without holding every case.

    Parameters
    ----------
    intensity : numpy.ndarray
        Hazard intensity with dimensions ``(split, case, return_period)``. Within a case with data,
        NaN or negative intensity is taken to be zero (no hazard), see :func:`clean_intensity`.
    asset_index : numpy.ndarray
        Integer position of each split's parent asset, in ``[0, len(keys))``.
    quantity : numpy.ndarray
        Quantity of each split in the units of ``cost.mean``.
    return_periods : sequence of float
        Return period, in years, of each position along the last axis of ``intensity``.
    curves, curve_weights
        Damage curves and their relative weights, keyed by curve identifier.
    cost : CostParameters
        Rehabilitation cost per unit quantity, with uncertainty.
    keys : numpy.ndarray
        Key of each asset's random draws, one per asset, see :func:`asset_keys`.
    has_data : numpy.ndarray, optional
        Boolean ``(case,)`` array, False for cases with no hazard data at all (for example a
        scenario with no baseline epoch). These are not computed, and yield None. By default, every
        case has data.
    n_samples, quantiles, seed, hazard_cv, eta_sd
        Passed to snail. ``seed`` is an integer in ``[0, 2**64)``.
    systemic_key : int, optional
        Key of the curve choice and systemic cost draws. None shares them with every other call with
        the same seed.

    Yields
    ------
    tuple of (int, DirectDamageArrays or None)
        The case, and snail's result: ``split_fraction``, ``split_cost`` ``(split, return_period,
        quantile)``, ``split_ead`` ``(split, quantile)``, ``asset_cost``, ``asset_ead``,
        ``total_cost``, ``total_ead`` and ``total_samples`` ``(sample, return_period)``. None for
        cases without data.
    """
    intensity = np.asarray(intensity)
    if intensity.ndim != 3:
        raise ValueError("intensity must have dimensions (split, case, return_period)")
    n_split, n_case, n_rp = intensity.shape
    asset_index = np.asarray(asset_index)
    quantity = np.asarray(quantity, dtype=float)
    if asset_index.shape != (n_split,) or quantity.shape != (n_split,):
        raise ValueError("asset_index and quantity must have one value per split")
    if len(return_periods) != n_rp:
        raise ValueError("return_periods must have one value per intensity return period")
    has_data = np.ones(n_case, dtype=bool) if has_data is None else np.asarray(has_data, dtype=bool)
    if has_data.shape != (n_case,):
        raise ValueError("has_data must have one value per case")
    names = list(curves)
    common = dict(
        curves=[curves[name] for name in names],
        curve_weights=[curve_weights[name] for name in names],
        cost_mean=cost.mean,
        cost_systemic_cv=cost.systemic_cv,
        cost_asset_cv=cost.asset_cv,
        hazard_cv=hazard_cv,
        eta_sd=eta_sd,
        n_samples=n_samples,
        quantiles=list(quantiles),
        return_periods=np.asarray(return_periods, dtype=float),
        seed=seed,
        asset_keys=keys,
        systemic_key=systemic_key,
        n_assets=len(keys),
        levels=("split", "asset", "total"),
        keep_total_samples=True,
    )
    for case in range(n_case):
        if not has_data[case]:
            yield case, None
            continue
        yield case, simulate_direct_damages(clean_intensity(intensity[:, case, :]), asset_index, quantity, **common)


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
