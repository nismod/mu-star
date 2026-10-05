"""Combine the monthly VIIRS tiles into one radiance raster (pixelwise median)."""

from pathlib import Path

import click

from energy.nightlights import build_nightlight_composite


@click.command()
@click.argument("months", nargs=-1, type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=str))
def main(months, output):
    build_nightlight_composite([Path(month) for month in months], Path(output))


if __name__ == "__main__":
    main()
