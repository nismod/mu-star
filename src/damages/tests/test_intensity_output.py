import numpy as np
import pandas as pd
import xarray as xr

from damages.calculate import stack_cases
from damages.intensity import split_intensity
from damages.output import build_datasets, empty_arrays, summary_table


def test_split_intensity_nd():
    hazard = xr.DataArray(
        np.arange(2 * 3 * 4, dtype=float).reshape(2, 3, 4),
        dims=("return_period", "y", "x"),
        coords={"return_period": [10, 100]},
    )
    splits = pd.DataFrame({"index_i": [0, 3, -1], "index_j": [0, 2, -1]})
    values = split_intensity(hazard, splits)
    assert values.dims == ("split", "return_period")
    np.testing.assert_array_equal(values.sel(return_period=10).values[:2], [0, 11])
    np.testing.assert_array_equal(values.sel(return_period=100).values[:2], [12, 23])
    assert np.isnan(values.values[2]).all()


def test_build_datasets_and_summary():
    intensity = xr.DataArray(
        np.zeros((3, 2, 2)),
        dims=("split", "rcp", "return_period"),
        coords={"rcp": ["a", "b"], "return_period": [10.0, 100.0]},
    )
    stacked, case_dims = stack_cases(intensity)
    quantiles = [0.05, 0.5, 0.95]
    arrays = empty_arrays(3, 2, 1, 2, 2, 3)
    for key in arrays:
        arrays[key][:] = 1.0
    datasets = build_datasets(
        arrays,
        stacked.indexes["case"],
        case_dims,
        [10.0, 100.0],
        quantiles,
        split_coords={"asset_id": np.array(["x", "x", "y"]), "split_id": np.array([0, 1, 0])},
        asset_coords={"asset_id": np.array(["x", "y"])},
        type_coords={
            "asset_type": np.array(["t"]),
            "n_assets": np.array([2]),
            "status": np.array(["calculated"]),
            "currency": np.array(["usd"]),
        },
    )
    assert datasets["split"].damage_cost.dims == ("split", "rcp", "return_period", "quantile")
    assert datasets["asset"].ead.dims == ("asset", "rcp", "quantile")
    assert datasets["asset_type"].damage_cost.sel(rcp="b", return_period=100.0, quantile=0.5).item() == 1.0

    table = summary_table(datasets["asset_type"], layer="road", peril="flood")
    assert len(table) == 2 * 2 + 2  # damage cost by rcp and return period, plus EAD by rcp
    assert table.columns[:2].tolist() == ["layer", "peril"]
    assert {"q_05", "q_50", "q_95", "n_assets", "currency"} <= set(table.columns)
    assert table.loc[table.metric == "ead", "return_period"].isna().all()
