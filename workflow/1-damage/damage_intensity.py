"""Attribute hazard intensity values to each split.

Reads splits (as written by ``damage_split.py``, on the grids of all hazards)
and a hazard data cube. The splits' schema metadata lists the grids they were
split on; the one matching the hazard's grid gives the index columns used to
look up values. The script fails if none matches (the hazard was not among
those the layer was split on). It writes a Zarr store with ``intensity``, with dimensions
``(split, ...hazard dims)`` - for example ``(split, rcp, epoch,
return_period)``. The ``split`` dimension is aligned with the rows of the split
GeoParquet file and has ``asset_id``, ``split_id`` and ``asset_type``
coordinates. ``has_data``, with the hazard dimensions only, records whether
the hazard has any data on its grid for each combination.

Example:

    python workflow/1-damage/damage_intensity.py \\
        --splits split.gpq \\
        --hazard example/processed/hazard/rp/peril-flood/subperil-coastal.zarr \\
        --variable inundation_depth \\
        --output intensity.zarr
"""

import logging

import click
import numpy as np
import pandas as pd
import xarray as xr

from damages.geozarr import check_coords_match_grid, grid_from_geozarr_attrs
from damages.intensity import SPLIT, split_intensity
from damages.split import find_grid_id, index_columns, read_grids_metadata


@click.command()
@click.option("--splits", "splits_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--hazard", "hazard_path", required=True, type=click.Path(exists=True))
@click.option("--variable", required=True, help="Name of the hazard intensity variable in the Zarr store")
@click.option("--output", "output_path", required=True, type=click.Path())
def main(splits_path, hazard_path, variable, output_path):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    hazard = xr.open_zarr(hazard_path, consolidated=False)
    if variable not in hazard:
        raise click.ClickException(f"Variable {variable!r} not in {hazard_path}: {list(hazard.data_vars)}")
    grid = grid_from_geozarr_attrs(hazard.attrs)
    y_dim, x_dim = hazard.attrs.get("spatial:dimensions", ["y", "x"])
    check_coords_match_grid(hazard[x_dim].values, hazard[y_dim].values, grid)

    definitions = read_grids_metadata(splits_path)
    grid_id = find_grid_id(definitions, grid)
    if grid_id is None:
        raise click.ClickException(
            f"The grid of {hazard_path} (shape {(grid.height, grid.width)}, transform {tuple(grid.transform)}) "
            f"matches none of the {len(definitions)} grids {splits_path} was split on: "
            f"{[(d['id'], d['shape'], d['transform']) for d in definitions]}. "
            "Re-run damage_split.py with this hazard included."
        )
    i_col, j_col = index_columns(grid_id)
    logging.info("Hazard grid is %s in %s", grid_id, splits_path)
    splits = pd.read_parquet(splits_path, columns=["asset_id", "split_id", "asset_type", i_col, j_col])

    da = hazard[variable]
    logging.info("Looking up %s %s for %d splits", variable, dict(da.sizes), len(splits))
    intensity = split_intensity(da, splits, spatial_dims=(y_dim, x_dim), index_i=i_col, index_j=j_col)
    intensity = intensity.rename("intensity").astype("float32")
    intensity.attrs = {**da.attrs, "hazard_path": str(hazard_path), "hazard_variable": variable}
    intensity = intensity.assign_coords(
        {
            "asset_id": (SPLIT, splits.asset_id.astype(str).to_numpy()),
            "split_id": (SPLIT, splits.split_id.to_numpy(dtype=np.int64)),
            "asset_type": (SPLIT, splits.asset_type.astype(str).to_numpy()),
        }
    )
    ds = intensity.to_dataset()
    # whether the hazard has any data, anywhere on the grid, for each combination of other dimensions
    ds["has_data"] = da.notnull().any(dim=[y_dim, x_dim]).compute()
    ds.attrs = {k: v for k, v in hazard.attrs.items() if not k.startswith(("spatial:", "proj:", "zarr_conventions"))}
    ds.to_zarr(output_path, mode="w", consolidated=False)
    logging.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
