"""Cache the monthly VIIRS radiance tiles (opt-in download)."""

from pathlib import Path

import click

from energy.nightlight_source import (
    DEFAULT_RENDERING_RULE,
    DEFAULT_SERVICE,
    fetch_nightlight_months,
)


@click.command()
@click.option("--out-dir", "out_dir", required=True, type=click.Path(file_okay=False, path_type=str))
@click.option("--object-ids", "object_ids", required=True, type=str, help="Comma-separated image object ids.")
@click.option("--bbox", required=True, type=str, help="Comma-separated west,south,east,north in degrees.")
@click.option("--pixel-size-degrees", "pixel_size_degrees", required=True, type=float)
@click.option("--service", default="", type=str)
@click.option("--rendering-rule", "rendering_rule", default="", type=str)
@click.option("--allow-download", "allow_download", is_flag=True, default=False)
def main(out_dir, object_ids, bbox, pixel_size_degrees, service, rendering_rule, allow_download):
    fetch_nightlight_months(
        object_ids=[int(value) for value in object_ids.split(",")],
        bbox=[float(value) for value in bbox.split(",")],
        pixel_size_degrees=pixel_size_degrees,
        out_dir=Path(out_dir),
        service=service or DEFAULT_SERVICE,
        rendering_rule=rendering_rule or DEFAULT_RENDERING_RULE,
        allow_download=allow_download,
    )


if __name__ == "__main__":
    main()
