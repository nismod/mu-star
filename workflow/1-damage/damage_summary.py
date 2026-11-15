"""Summarise direct damage totals by asset type, across layers and hazards.

Reads the ``asset_type`` group of each damage Zarr store and writes one long
table, with layer, peril and subperil labels parsed from each input path
(``.../{layer}/rp/{peril}/{subperil}/damage.zarr``).

With no inputs, writes an empty table with a header only.

Quantiles are of totals over all assets of each type. Quantiles of different
asset types cannot be added together to find quantiles of a layer total.

Example:

    python workflow/1-damage/damage_summary.py \\
        --damage example/results/damage/layer-road/rp/peril-flood/subperil-coastal/damage.zarr \\
        --output summary.csv
"""

import logging
import warnings
from pathlib import Path

import click
import pandas as pd
import xarray as xr

from damages.output import empty_summary_table, summary_table

# Snakemake writes a .snakemake_timestamp file in directory outputs, which zarr reports when opening the store
warnings.filterwarnings("ignore", message=r"Object at \.snakemake_timestamp is not recognized")


def labels_from_path(path: str) -> dict[str, str]:
    parts = Path(path).parts
    subperil, peril, layer = parts[-2], parts[-3], parts[-5]
    return {
        "layer": layer.removeprefix("layer-"),
        "peril": peril.removeprefix("peril-"),
        "subperil": subperil.removeprefix("subperil-"),
    }


@click.command()
@click.option("--damage", "damage_paths", multiple=True, type=click.Path(exists=True))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False))
def main(damage_paths, output_path):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tables = []
    for path in damage_paths:
        totals = xr.open_zarr(path, group="asset_type", consolidated=False).load()
        tables.append(summary_table(totals, **labels_from_path(path)))
    if tables:
        table = pd.concat(tables, ignore_index=True)
    else:
        logging.warning("No damage outputs to summarise (no layers or hazards configured?), writing an empty table")
        table = empty_summary_table()
    if output_path.endswith(".parquet"):
        table.to_parquet(output_path, index=False)
    else:
        table.to_csv(output_path, index=False)
    logging.info("Wrote %d rows to %s", len(table), output_path)


if __name__ == "__main__":
    main()
