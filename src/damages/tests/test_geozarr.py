import numpy as np
import pytest
import rioxarray  # noqa: F401
import xarray as xr

from damages.geozarr import check_coords_match_grid, geozarr_attrs, grid_from_geozarr_attrs, with_geozarr_attrs


def test_attrs_round_trip_to_grid():
    attrs = geozarr_attrs((0.5, 0, 10.0, 0, -0.5, 20.0), (4, 6), "EPSG:4326")
    assert attrs["spatial:bbox"] == [10.0, 18.0, 13.0, 20.0]
    assert attrs["proj:code"] == "EPSG:4326"
    assert {c["name"] for c in attrs["zarr_conventions"]} == {"spatial", "proj"}
    grid = grid_from_geozarr_attrs(attrs)
    assert (grid.width, grid.height) == (6, 4)
    assert grid.transform == (0.5, 0.0, 10.0, 0.0, -0.5, 20.0)
    assert grid.crs.to_epsg() == 4326


def test_grid_requires_crs_and_transform():
    attrs = geozarr_attrs((1, 0, 0, 0, -1, 0), (1, 1), "EPSG:4326")
    with pytest.raises(KeyError):
        grid_from_geozarr_attrs({k: v for k, v in attrs.items() if not k.startswith("proj:")})
    with pytest.raises(KeyError):
        grid_from_geozarr_attrs({k: v for k, v in attrs.items() if k != "spatial:transform"})
    with pytest.raises(ValueError):
        grid_from_geozarr_attrs({**attrs, "spatial:registration": "node"})


def _dataset():
    x = 10.25 + 0.5 * np.arange(6)
    y = 19.75 - 0.5 * np.arange(4)
    da = xr.DataArray(
        np.zeros((2, 4, 6)), dims=("return_period", "y", "x"), coords={"x": x, "y": y, "return_period": [10, 100]}
    )
    return da.to_dataset(name="depth").rio.write_crs("EPSG:4326")


def test_spatial_ref_is_not_a_data_variable_after_zarr_round_trip(tmp_path):
    ds = with_geozarr_attrs(_dataset())
    ds.to_zarr(tmp_path / "store.zarr", mode="w", consolidated=False)
    back = xr.open_zarr(tmp_path / "store.zarr", consolidated=False)
    assert "spatial_ref" not in back.data_vars
    assert back.rio.crs.to_epsg() == 4326


def test_with_geozarr_attrs_matches_coords():
    ds = with_geozarr_attrs(_dataset())
    grid = grid_from_geozarr_attrs(ds.attrs)
    assert grid.transform == pytest.approx((0.5, 0.0, 10.0, 0.0, -0.5, 20.0))
    check_coords_match_grid(ds.x.values, ds.y.values, grid)
    with pytest.raises(ValueError, match="x coordinates"):
        check_coords_match_grid(ds.x.values + 0.1, ds.y.values, grid)
