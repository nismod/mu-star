"""Split an asset layer once on the grids of all hazard data cubes.

Reads asset features from GeoParquet (requiring ``asset_id`` and
``asset_type`` columns), reads each hazard's grid definition from GeoZarr
attributes, splits features on all distinct grids using snail (see
``damages.split.split_on_grids``) and writes the splits to GeoParquet. An asset
cut at different places on different grids is cut at all of them, so each split
lies within one cell of every grid and has one value of each hazard intensity.
Each split keeps its parent asset's attributes and gains:

- ``split_id``: split number within the asset (0, 1, ...)
- ``index_i_g0``, ``index_j_g0``, ``index_i_g1``, ...: grid column and row on
  each distinct grid (-1 if outside the grid)
- ``geom_kind``: "point", "line", "polygon" or "other"
- ``length_m``, ``area_m2``: planar in a projected metre CRS, otherwise geodesic
  (zero for slivers)
- ``share``: fraction of the unsplit parent asset's length/area (1 for points);
  the shares of an asset sum to 1, since nothing is dropped

The grids are recorded in the file's schema metadata, under the key
``mu_star:grids``, as a JSON list of ``{"id", "crs" (WKT2), "shape"
([height, width]), "transform" ([a, b, c, d, e, f])}``. ``damage_intensity.py``
uses it to find the index columns for a hazard.

Example:

    python workflow/1-damage/damage_split.py \\
        --layer example/processed/asset/layer-road.gpq \\
        --hazard example/processed/hazard/rp/peril-flood/subperil-coastal.zarr \\
        --hazard example/processed/hazard/rp/peril-cyclone/subperil-wind.zarr \\
        --output split.gpq
"""

import logging

import click
import geopandas as gpd
import zarr

from damages.geozarr import grid_from_geozarr_attrs
from damages.quantity import asset_totals, conservation_mismatches
from damages.split import split_on_grids, write_grids_metadata

REQUIRED_COLUMNS = ("asset_id", "asset_type")
CONSERVATION_RTOL = 1e-3
N_WORST = 5


@click.command()
@click.option("--layer", "layer_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--hazard",
    "hazard_paths",
    required=True,
    multiple=True,
    type=click.Path(exists=True),
    help="Hazard Zarr store, whose grid to split on. Repeat for each hazard",
)
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False))
def main(layer_path, hazard_paths, output_path):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    features = gpd.read_parquet(layer_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in features.columns]
    if missing:
        raise click.ClickException(f"{layer_path} is missing required columns {missing}")
    if features.crs is None:
        raise click.ClickException(f"{layer_path} has no CRS, so lengths and areas cannot be measured")
    if features.asset_id.duplicated().any():
        raise click.ClickException(f"{layer_path} has duplicate asset_id values")
    features = features[features.geometry.notna() & ~features.geometry.is_empty]

    grids = [grid_from_geozarr_attrs(zarr.open_group(path, mode="r").attrs.asdict()) for path in hazard_paths]
    splits, definitions = split_on_grids(features, grids)
    logging.info(
        "Split %d features into %d pieces on %d distinct grids (from %d hazards)",
        len(features),
        len(splits),
        len(definitions),
        len(hazard_paths),
    )
    totals = asset_totals(features, "asset_id")
    mismatches = conservation_mismatches(totals, splits, "asset_id", CONSERVATION_RTOL)
    if len(mismatches):
        worst = ", ".join(f"{i} ({r:.1%} off)" for i, r in mismatches.rel_diff.head(N_WORST).items())
        logging.warning(
            "%d assets have split lengths or areas differing from the unsplit asset by more than %.1f%%; worst: %s",
            len(mismatches),
            CONSERVATION_RTOL * 100,
            worst,
        )
    splits = splits.reset_index(drop=True)
    outside = {d["id"]: int((splits[f"index_i_{d['id']}"] < 0).sum()) for d in definitions}
    logging.info("Wrote %d splits; outside each grid: %s", len(splits), outside)
    splits.to_parquet(output_path)
    write_grids_metadata(output_path, definitions)


if __name__ == "__main__":
    main()
