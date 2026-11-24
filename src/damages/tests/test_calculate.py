import numpy as np
import pandas as pd
import pytest
import xarray as xr
from snail.damages import PiecewiseLinearDamageCurve, simulate_direct_damages

from damages.calculate import (
    asset_keys,
    clean_intensity,
    simulate_asset_type,
    stack_cases,
    systemic_key,
)
from damages.config import CostParameters
from damages.quantity import parse_cost_unit

LINEAR = PiecewiseLinearDamageCurve(pd.DataFrame({"intensity": [0.0, 1.0, 2.0], "damage": [0.0, 0.5, 1.0]}))
OFFSET = PiecewiseLinearDamageCurve(pd.DataFrame({"intensity": [0.0, 2.0], "damage": [0.1, 1.0]}))
RETURN_PERIODS = [10, 100, 1000]
IDS = np.array(["a", "b"])


def _cost(mean=2.0, systemic_cv=0.0, asset_cv=0.0):
    return CostParameters(parse_cost_unit("usd_per_m"), mean, systemic_cv, asset_cv)


def _intensity():
    # 3 splits (of assets a, a, b), 2 cases (second without data), 3 return periods. NaN and negative in a case with
    # data mean no hazard
    present = np.array([[0.0, 0.5, 1.0], [np.nan, 1.0, 2.0], [-1.0, 0.0, np.nan]])
    return np.stack([present, np.full((3, 3), np.nan)], axis=1)


def _run(intensity=None, curve=LINEAR, cost=None, has_data=(True, False), **kwargs):
    kwargs = dict(n_samples=20, seed=1) | kwargs
    return dict(
        simulate_asset_type(
            _intensity() if intensity is None else intensity,
            asset_index=np.array([0, 0, 1]),
            quantity=np.array([10.0, 5.0, 1.0]),
            return_periods=RETURN_PERIODS,
            curves={"c": curve},
            curve_weights={"c": 1.0},
            cost=cost or _cost(),
            keys=asset_keys(IDS),
            has_data=None if has_data is None else np.array(has_data),
            **kwargs,
        )
    )


def test_clean_intensity():
    values = np.array([[np.nan, -1.0], [2.0, np.inf]], dtype="float32")
    cleaned = clean_intensity(values)
    assert cleaned.dtype == np.float64
    assert cleaned.tolist() == [[0.0, 0.0], [2.0, 0.0]]


def test_keys_are_stable_hashes_of_ids():
    keys = asset_keys(["a", "b", "c"])
    assert keys.dtype == np.uint64
    # independent of order and of the other ids
    assert asset_keys(["c", "a"]).tolist() == [keys[2], keys[0]]
    np.testing.assert_array_equal(asset_keys(np.array(["a", "b", "c"], dtype=object)), keys)
    # pinned, so that a change of hashing (which changes every sampled result) is noticed
    assert int(keys[0]) == 13950350942979735504
    assert systemic_key("road") == systemic_key("road") != systemic_key("rail")
    assert 0 <= systemic_key("road") < 2**64


def test_deterministic_damages():
    out = _run()
    assert set(out) == {0, 1}
    assert out[1] is None  # no data: not computed
    r = out[0]
    median = 1
    np.testing.assert_allclose(r.split_fraction[:, :, median], [[0, 0.25, 0.5], [0, 0.5, 1], [0, 0, 0]])
    np.testing.assert_allclose(r.split_cost[:, :, median], [[0, 5, 10], [0, 5, 10], [0, 0, 0]])
    np.testing.assert_allclose(r.asset_cost[:, :, median], [[0, 10, 20], [0, 0, 0]])
    np.testing.assert_allclose(r.total_cost[:, median], [0, 10, 20])
    assert r.asset_ead[0, median] > 0
    assert r.asset_ead[1, median] == 0
    # split EADs add up to asset EAD, and every quantile is the same with no uncertainty
    assert r.split_ead[:2, median].sum() == pytest.approx(r.asset_ead[0, median])
    np.testing.assert_allclose(r.asset_cost[..., 0], r.asset_cost[..., 2])
    assert r.total_samples.shape == (20, 3)
    np.testing.assert_allclose(r.total_samples, np.broadcast_to([0, 10, 20], (20, 3)))
    # the dry split (zero, negative or NaN intensity) is skipped by snail
    assert r.metadata["n_computed_splits"] == 2


def test_all_cases_by_default():
    out = _run(intensity=_intensity()[:, :1], has_data=None)
    assert list(out) == [0] and out[0] is not None


def test_case_without_data_is_not_computed_even_with_values():
    # has_data is what decides, not whether this asset type's splits have values
    intensity = np.repeat(_intensity()[:, :1], 2, axis=1)
    out = _run(intensity=intensity, has_data=(False, True))
    assert out[0] is None
    np.testing.assert_allclose(out[1].total_cost[:, 1], [0, 10, 20])


def test_nonzero_damage_at_zero_intensity_is_sampled():
    r = _run(curve=OFFSET)[0]
    assert r.split_fraction[2, 0, 1] == pytest.approx(0.1)
    assert r.metadata["n_computed_splits"] == 3


def test_cost_uncertainty_spreads_quantiles():
    r = _run(cost=_cost(systemic_cv=0.2, asset_cv=0.2), n_samples=500)[0]
    low, mid, high = r.asset_cost[0, 2]
    assert low < mid < high
    assert mid == pytest.approx(20, rel=0.1)
    # damage fractions are unaffected by cost uncertainty
    np.testing.assert_allclose(r.split_fraction[0, 2], [0.5, 0.5, 0.5])


def test_common_random_numbers_across_cases():
    # the same intensity in both cases gives the same samples, so scenario differences are not sampling noise
    intensity = np.repeat(_intensity()[:, :1], 2, axis=1)
    out = _run(intensity=intensity, cost=_cost(systemic_cv=0.2, asset_cv=0.3), has_data=(True, True), n_samples=100)
    np.testing.assert_array_equal(out[0].total_samples, out[1].total_samples)
    np.testing.assert_array_equal(out[0].split_cost, out[1].split_cost)


def test_draws_do_not_depend_on_asset_order():
    cost = _cost(systemic_cv=0.2, asset_cv=0.3)
    forward = _run(cost=cost, n_samples=100)[0]
    # the same splits, with assets numbered the other way round
    backward = dict(
        simulate_asset_type(
            _intensity()[::-1],
            asset_index=np.array([0, 1, 1]),
            quantity=np.array([1.0, 5.0, 10.0]),
            return_periods=RETURN_PERIODS,
            curves={"c": LINEAR},
            curve_weights={"c": 1.0},
            cost=cost,
            keys=asset_keys(IDS[::-1]),
            has_data=np.array([True, False]),
            n_samples=100,
            seed=1,
        )
    )[0]
    np.testing.assert_allclose(backward.split_cost[::-1], forward.split_cost)
    np.testing.assert_allclose(backward.asset_cost[::-1], forward.asset_cost)
    np.testing.assert_allclose(backward.total_samples, forward.total_samples)


def test_total_samples_add_up_across_asset_types():
    # Two asset types with the same curve and cost, run separately, sum to the totals of one call over all their
    # assets: draws are keyed by asset id, and curve choice and systemic draws are shared
    cost = _cost(systemic_cv=0.3, asset_cv=0.4)
    curves = {"linear": LINEAR, "offset": OFFSET}
    weights = {"linear": 0.5, "offset": 0.5}
    intensity = np.array([[[0.2, 0.8, 1.5]], [[0.0, 0.4, 1.0]], [[1.0, 1.5, 2.0]], [[0.0, 0.0, 0.3]]])
    ids = np.array(["r1", "r2", "c1"])
    asset_of_split = np.array([0, 0, 1, 2])
    quantity = np.array([3.0, 4.0, 2.0, 1.0])
    kwargs = dict(n_samples=200, seed=7, quantiles=[0.05, 0.5, 0.95])

    def run(splits, assets):
        local = np.searchsorted(assets, asset_of_split[splits])
        return dict(
            simulate_asset_type(
                intensity[splits],
                local,
                quantity[splits],
                RETURN_PERIODS,
                curves,
                weights,
                cost,
                keys=asset_keys(ids[assets]),
                **kwargs,
            )
        )[0]

    first, second = run(np.array([0, 1, 3]), np.array([0, 2])), run(np.array([2]), np.array([1]))
    direct = simulate_direct_damages(
        intensity[:, 0], asset_of_split, quantity, curves=list(curves.values()), curve_weights=[0.5, 0.5],
        cost_mean=cost.mean, cost_systemic_cv=cost.systemic_cv, cost_asset_cv=cost.asset_cv,
        return_periods=RETURN_PERIODS, asset_keys=asset_keys(ids), keep_total_samples=True, **kwargs,
    )  # fmt: skip
    np.testing.assert_allclose(first.total_samples + second.total_samples, direct.total_samples, rtol=1e-12)
    # but quantiles of the sum are not the sums of quantiles
    assert not np.allclose(first.total_cost + second.total_cost, direct.total_cost)


def test_independent_systemic_draws():
    cost = _cost(systemic_cv=0.3)
    shared = _run(cost=cost, n_samples=200)[0]
    keyed = _run(cost=cost, n_samples=200, systemic_key=systemic_key("road"))[0]
    assert not np.allclose(shared.total_samples, keyed.total_samples)
    # with no uncertainty the key changes nothing
    np.testing.assert_array_equal(_run()[0].split_cost, _run(systemic_key=systemic_key("road"))[0].split_cost)


def test_validation():
    with pytest.raises(ValueError, match="one value per split"):
        _run(intensity=_intensity()[:2])
    with pytest.raises(ValueError, match="has_data"):
        _run(has_data=(True,))
    with pytest.raises(ValueError, match="return period"):
        _run(intensity=_intensity()[:, :, :2])


def test_stack_unstack_cases():
    da = xr.DataArray(
        np.arange(2 * 3 * 2 * 4).reshape(2, 3, 2, 4),
        dims=("rcp", "split", "epoch", "return_period"),
        coords={"rcp": ["a", "b"], "epoch": [2030, 2050], "return_period": [1, 10, 100, 1000]},
    )
    stacked, case_dims = stack_cases(da)
    assert stacked.dims == ("split", "case", "return_period")
    assert case_dims == ["rcp", "epoch"]
    xr.testing.assert_equal(stacked.unstack("case").transpose(*da.dims), da)

    no_cases, case_dims = stack_cases(da.isel(rcp=0, epoch=0, drop=True))
    assert no_cases.sizes["case"] == 1 and case_dims == []
    with pytest.raises(ValueError, match="return_period"):
        stack_cases(da.isel(return_period=0, drop=True))
