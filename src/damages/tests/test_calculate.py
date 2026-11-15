import numpy as np
import pandas as pd
import pytest
import xarray as xr
from snail.damages import PiecewiseLinearDamageCurve

from damages.calculate import clean_intensity, sample_direct_damages, stack_cases, unstack_cases
from damages.config import CostParameters
from damages.quantity import parse_cost_unit

LINEAR = PiecewiseLinearDamageCurve(pd.DataFrame({"intensity": [0.0, 1.0, 2.0], "damage": [0.0, 0.5, 1.0]}))
OFFSET = PiecewiseLinearDamageCurve(pd.DataFrame({"intensity": [0.0, 2.0], "damage": [0.1, 1.0]}))


def _cost(mean=2.0, systemic_cv=0.0, asset_cv=0.0):
    return CostParameters(parse_cost_unit("usd_per_m"), mean, systemic_cv, asset_cv)


def _intensity():
    # 3 splits, 2 cases (second missing), 3 return periods
    present = np.array([[0.0, 0.5, 1.0], [0.0, 1.0, 2.0], [0.0, 0.0, 0.0]])
    return np.stack([present, np.full((3, 3), np.nan)], axis=1)


def test_clean_intensity():
    values = np.array([[[np.nan, -1.0], [np.nan, np.nan]], [[2.0, np.nan], [np.nan, np.nan]]])
    cleaned, missing = clean_intensity(values)
    assert missing.tolist() == [False, True]
    assert cleaned[:, 0].tolist() == [[0.0, 0.0], [2.0, 0.0]]


def test_deterministic_damages():
    out = sample_direct_damages(
        _intensity(),
        asset_index=np.array([0, 0, 1]),
        quantity=np.array([10.0, 5.0, 1.0]),
        return_periods=[10, 100, 1000],
        curves={"linear": LINEAR},
        curve_weights={"linear": 1.0},
        cost=_cost(),
        n_samples=20,
        seed=1,
    )
    median = 1
    np.testing.assert_allclose(out.split_fraction[:, 0, :, median], [[0, 0.25, 0.5], [0, 0.5, 1], [0, 0, 0]])
    np.testing.assert_allclose(out.split_cost[:, 0, :, median], [[0, 5, 10], [0, 5, 10], [0, 0, 0]])
    np.testing.assert_allclose(out.asset_cost[:, 0, :, median], [[0, 10, 20], [0, 0, 0]])
    np.testing.assert_allclose(out.total_cost[0, :, median], [0, 10, 20])
    assert out.asset_ead[0, 0, median] > 0
    assert out.asset_ead[1, 0, median] == 0
    # split EADs add up to asset EAD when there is no uncertainty
    assert out.split_ead[:2, 0, median].sum() == pytest.approx(out.asset_ead[0, 0, median])
    # missing case is NaN
    assert np.isnan(out.split_cost[:, 1]).all()
    assert np.isnan(out.total_ead[1]).all()
    # zero-intensity split was skipped
    assert out.metadata["n_active_split_cases"] == 2


def test_nonzero_damage_at_zero_intensity_is_sampled():
    out = sample_direct_damages(
        _intensity(),
        asset_index=np.array([0, 0, 1]),
        quantity=np.array([10.0, 5.0, 1.0]),
        return_periods=[10, 100, 1000],
        curves={"offset": OFFSET},
        curve_weights={"offset": 1.0},
        cost=_cost(),
        n_samples=20,
        seed=1,
    )
    assert out.split_fraction[2, 0, 0, 1] == pytest.approx(0.1)
    assert out.metadata["n_active_split_cases"] == 3


def test_cost_uncertainty_spreads_quantiles():
    out = sample_direct_damages(
        _intensity(),
        asset_index=np.array([0, 0, 1]),
        quantity=np.array([10.0, 5.0, 1.0]),
        return_periods=[10, 100, 1000],
        curves={"linear": LINEAR},
        curve_weights={"linear": 1.0},
        cost=_cost(systemic_cv=0.2, asset_cv=0.2),
        n_samples=500,
        seed=1,
    )
    low, mid, high = out.asset_cost[0, 0, 2]
    assert low < mid < high
    assert mid == pytest.approx(20, rel=0.1)
    # damage fractions are unaffected by cost uncertainty
    np.testing.assert_allclose(out.split_fraction[0, 0, 2], [0.5, 0.5, 0.5])


def test_stack_unstack_cases():
    da = xr.DataArray(
        np.arange(2 * 3 * 2 * 4).reshape(2, 3, 2, 4),
        dims=("rcp", "split", "epoch", "return_period"),
        coords={"rcp": ["a", "b"], "epoch": [2030, 2050], "return_period": [1, 10, 100, 1000]},
    )
    stacked, case_dims = stack_cases(da)
    assert stacked.dims == ("split", "case", "return_period")
    assert case_dims == ["rcp", "epoch"]
    xr.testing.assert_equal(unstack_cases(stacked, case_dims).transpose(*da.dims), da)

    no_cases, case_dims = stack_cases(da.isel(rcp=0, epoch=0, drop=True))
    assert no_cases.sizes["case"] == 1 and case_dims == []
    with pytest.raises(ValueError, match="return_period"):
        stack_cases(da.isel(return_period=0, drop=True))


def test_missing_case_given_explicitly():
    # all splits dry or outside the grid, but the hazard has data: zero damage, not missing
    intensity = np.full((2, 2, 3), np.nan)
    kwargs = dict(
        asset_index=np.array([0, 1]),
        quantity=np.array([1.0, 1.0]),
        return_periods=[10, 100, 1000],
        curves={"linear": LINEAR},
        curve_weights={"linear": 1.0},
        cost=_cost(),
        n_samples=20,
        seed=1,
    )
    inferred = sample_direct_damages(intensity, **kwargs)
    assert np.isnan(inferred.asset_cost).all()
    given = sample_direct_damages(intensity, missing_case=np.array([False, True]), **kwargs)
    assert (given.asset_cost[:, 0] == 0).all()
    assert np.isnan(given.asset_cost[:, 1]).all()
    assert (given.total_ead[0] == 0).all()
