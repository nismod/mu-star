"""Look up hazard intensity for splits from an n-dimensional hazard array."""

from collections.abc import Sequence

import numpy as np
import pandas as pd
import xarray as xr
from snail.intersection import get_raster_values_for_splits

SPLIT = "split"


def split_intensity(
    hazard: xr.DataArray,
    splits: pd.DataFrame,
    spatial_dims: Sequence[str] = ("y", "x"),
    index_i: str = "index_i",
    index_j: str = "index_j",
) -> xr.DataArray:
    """Hazard value for each split, for every combination of non-spatial dims.

    Parameters
    ----------
    hazard : xarray.DataArray
        Hazard values with spatial dimensions ``spatial_dims`` (``y`` then
        ``x``, matching row ``index_j`` and column ``index_i``) and any number
        of other dimensions, such as ``return_period`` or ``epoch``.
    splits : pandas.DataFrame
        Splits with grid cell indices.

    Returns
    -------
    xarray.DataArray
        Values with dimensions ``("split", *other_dims)``, NaN for splits
        outside the grid.
    """
    y_dim, x_dim = spatial_dims
    other_dims = [d for d in hazard.dims if d not in spatial_dims]
    if other_dims:
        stacked = hazard.stack(_case=other_dims).transpose("_case", y_dim, x_dim)
    else:
        stacked = hazard.expand_dims("_case").transpose("_case", y_dim, x_dim)

    values = np.full((len(splits), stacked.sizes["_case"]), np.nan, dtype=float)
    table = pd.DataFrame({index_i: splits[index_i].to_numpy(), index_j: splits[index_j].to_numpy()})
    for k in range(stacked.sizes["_case"]):
        layer = stacked.isel(_case=k).load()
        values[:, k] = get_raster_values_for_splits(table, layer, index_i=index_i, index_j=index_j).to_numpy(
            dtype=float
        )

    result = xr.DataArray(
        values,
        dims=(SPLIT, "_case"),
        coords={"_case": stacked.indexes["_case"]} if other_dims else None,
        name=hazard.name,
        attrs=hazard.attrs,
    )
    if other_dims:
        result = result.unstack("_case").transpose(SPLIT, *other_dims)
    else:
        result = result.isel(_case=0, drop=True)
    return result
