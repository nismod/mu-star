"""Extract VIIRS nightlight connection targets from the radiance composite."""

from pathlib import Path

import click

from energy.nightlight_targets import build_nightlight_targets


@click.command()
@click.option("--nightlights", required=True, type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--aoi", required=True, type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option("--output-dir", "output_dir", required=True, type=click.Path(file_okay=False, path_type=str))
@click.option("--region", required=True, type=str)
@click.option("--nightlight-threshold", "nightlight_threshold", required=True, type=float)
def main(nightlights, aoi, output_dir, region, nightlight_threshold):
    build_nightlight_targets(
        Path(nightlights),
        Path(output_dir),
        aoi_path=Path(aoi),
        region=region,
        nightlight_threshold=nightlight_threshold,
    )


if __name__ == "__main__":
    main()
