import numpy as np
import pandas as pd
import xarray as xr
import zarr

from damages.calculate import stack_cases
from damages.intensity import split_intensity
from damages.output import DamageStore, summary_table


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


def test_damage_store_and_summary(tmp_path):
    intensity = xr.DataArray(
        np.zeros((3, 2, 2, 2)),
        dims=("split", "rcp", "epoch", "return_period"),
        coords={"rcp": ["b", "a"], "epoch": [2050, 2030], "return_period": [10.0, 100.0]},
    )
    stacked, case_dims = stack_cases(intensity)
    case_index = stacked.indexes["case"]
    quantiles = [0.05, 0.5, 0.95]
    path = tmp_path / "damage.zarr"
    store = DamageStore(
        path,
        case_index,
        case_dims,
        {d: intensity[d].values for d in case_dims},
        [10.0, 100.0],
        quantiles,
        split_coords={"asset_id": np.array(["x", "y", "x"]), "split_id": np.array([0, 0, 1])},
        asset_coords={"asset_id": np.array(["x", "y"])},
        type_coords={
            "asset_type": np.array(["t"]),
            "n_assets": np.array([2]),
            "status": np.array(["calculated"]),
            "currency": np.array(["usd"]),
        },
        attrs={"seed": 1},
        layer_attrs={"status": "calculated", "currency": "usd", "n_assets": 2},
        chunk_size=2,
    )
    rcp_a_2030 = next(k for k, key in enumerate(case_index) if key == ("a", 2030))
    # splits 0 and 2 are not contiguous
    cost = np.arange(2 * 2 * 3, dtype=float).reshape(2, 2, 3)
    store.write("split", "damage_cost", np.array([0, 2]), rcp_a_2030, cost)
    store.write("asset_type", "damage_cost", [0], rcp_a_2030, np.ones((1, 2, 3)))
    store.write("asset_type", "ead", [0], rcp_a_2030, np.full((1, 3), 2.0))
    store.write("layer", "damage_cost", None, rcp_a_2030, np.ones((2, 3)))
    store.write("layer", "ead", None, rcp_a_2030, np.full(3, 3.0))
    store.update_attrs({"done": True})

    split = xr.open_zarr(path, group="split", consolidated=False).load()
    assert split.damage_cost.dims == ("split", "rcp", "epoch", "return_period", "quantile")
    assert split.damage_cost.dtype == np.float32
    assert split.damage_cost.encoding["chunks"] == (2, 2, 2, 2, 3)
    # variable-length strings, in the order given
    assert split.rcp.values.tolist() == ["b", "a"] and split.asset_id.values.tolist() == ["x", "y", "x"]
    np.testing.assert_array_equal(split.damage_cost.sel(rcp="a", epoch=2030).values[[0, 2]], cost)
    # everything not written is NaN
    assert split.damage_cost.sel(rcp="a", epoch=2030).isel(split=1).isnull().all()
    assert split.damage_cost.sel(rcp="b").isnull().all() and split.ead.isnull().all()
    assets = xr.open_zarr(path, group="asset", consolidated=False)
    assert assets.ead.dims == ("asset", "rcp", "epoch", "quantile")
    totals = xr.open_zarr(path, group="asset_type", consolidated=False).load()
    assert totals.damage_cost.sel(rcp="a", epoch=2030, return_period=100.0, quantile=0.5).item() == 1.0
    layer = xr.open_zarr(path, group="layer", consolidated=False).load()
    assert layer.damage_cost.dims == ("rcp", "epoch", "return_period", "quantile")
    assert layer.attrs["status"] == "calculated"
    root = zarr.open_group(path, mode="r")
    assert root.attrs["seed"] == 1 and root.attrs["done"] is True

    table = summary_table(totals, layer, layer="road", peril="flood")
    # damage cost by case and return period, plus EAD by case, for the asset type and the layer
    assert len(table) == 2 * (4 * 2 + 4)
    assert table.columns[:5].tolist() == ["layer", "peril", "metric", "level", "asset_type"]
    assert {"q_5", "q_50", "q_95", "n_assets", "status", "currency"} <= set(table.columns)
    assert table.loc[table.metric == "ead", "return_period"].isna().all()
    layer_rows = table[table.level == "layer"]
    assert layer_rows.asset_type.isna().all() and (layer_rows.n_assets == 2).all()
    assert (layer_rows.status == "calculated").all() and (layer_rows.currency == "usd").all()
    ead = layer_rows[(layer_rows.metric == "ead") & (layer_rows.rcp == "a") & (layer_rows.epoch == 2030)]
    assert ead.q_50.tolist() == [3.0]
    assert (table[table.level == "asset_type"].asset_type == "t").all()


def test_summary_without_scenarios(tmp_path):
    intensity = xr.DataArray(np.zeros((1, 2)), dims=("split", "return_period"), coords={"return_period": [10, 100]})
    stacked, case_dims = stack_cases(intensity)
    path = tmp_path / "damage.zarr"
    store = DamageStore(
        path, stacked.indexes["case"], case_dims, {}, [10.0, 100.0], [0.5],
        split_coords={"asset_id": np.array(["x"])}, asset_coords={"asset_id": np.array(["x"])},
        type_coords={"asset_type": np.array(["t"]), "n_assets": np.array([1]), "status": np.array(["calculated"]),
                     "currency": np.array(["usd"])},
    )  # fmt: skip
    store.write("split", "ead", [0], 0, [[4.0]])
    store.write("layer", "ead", None, 0, [5.0])
    store.write("asset_type", "ead", [0], 0, [[6.0]])
    split = xr.open_zarr(path, group="split", consolidated=False).load()
    assert split.ead.dims == ("split", "quantile") and split.ead.item() == 4.0
    totals = xr.open_zarr(path, group="asset_type", consolidated=False).load()
    layer = xr.open_zarr(path, group="layer", consolidated=False).load()
    table = summary_table(totals, layer)
    assert table[(table.metric == "ead") & (table.level == "layer")].q_50.tolist() == [5.0]
    assert table[(table.metric == "ead") & (table.level == "asset_type")].q_50.tolist() == [6.0]


def test_empty_summary_table():
    from damages.output import empty_summary_table

    table = empty_summary_table()
    assert table.empty
    assert {"layer", "peril", "subperil", "metric", "asset_type", "return_period"} <= set(table.columns)
