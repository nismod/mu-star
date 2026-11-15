"""Split an asset layer on the grid of a hazard data cube.

Reads asset features from GeoParquet (requiring ``asset_id`` and
``asset_type`` columns), reads the hazard grid definition from GeoZarr
attributes, splits features on the grid using snail and writes the splits to
GeoParquet. Each split keeps its parent asset's attributes and gains:

- ``split_id``: split number within the asset (0, 1, ...)
- ``index_i``, ``index_j``: grid column and row (-1 if outside the grid)
- ``geom_kind``: "point", "line", "polygon" or "other"
- ``length_m``, ``area_m2``: measured in a projected CRS (zero for slivers)
- ``share``: fraction of the unsplit parent asset's length/area (1 for points)

Example:

    python workflow/1-damage/damage_split.py \\
        --layer example/processed/asset/layer-road.gpq \\
        --hazard example/processed/hazard/rp/peril-flood/subperil-coastal.zarr \\
        --output split.gpq
"""

import logging

import click
import geopandas as gpd
import zarr
from snail.overlay import split_features

from damages.geozarr import grid_from_geozarr_attrs
from damages.quantity import asset_totals, conservation_mismatches, default_measure_crs, measure_splits

REQUIRED_COLUMNS = ("asset_id", "asset_type")
CONSERVATION_RTOL = 1e-3
N_WORST = 5


@click.command()
@click.option("--layer", "layer_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--hazard", "hazard_path", required=True, type=click.Path(exists=True))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False))
@click.option("--measure-crs", default=None, help="Projected CRS for lengths and areas (default: UTM estimate)")
def main(layer_path, hazard_path, output_path, measure_crs):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    features = gpd.read_parquet(layer_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in features.columns]
    if missing:
        raise click.ClickException(f"{layer_path} is missing required columns {missing}")
    if features.asset_id.duplicated().any():
        raise click.ClickException(f"{layer_path} has duplicate asset_id values")
    features = features[features.geometry.notna() & ~features.geometry.is_empty]

    grid = grid_from_geozarr_attrs(zarr.open_group(hazard_path, mode="r").attrs.asdict())
    logging.info("Splitting %d features on %dx%d grid", len(features), grid.width, grid.height)

    splits = split_features(features, grid)
    if measure_crs is None:
        measure_crs = default_measure_crs(features)
    totals = asset_totals(features, "asset_id", measure_crs)
    splits = measure_splits(splits, asset_id="asset_id", measure_crs=measure_crs, totals=totals)
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
    n_outside = int((splits.index_i < 0).sum())
    logging.info("Wrote %d splits (%d outside grid)", len(splits), n_outside)
    splits.to_parquet(output_path)


if __name__ == "__main__":
    main()
