"""Download the WorldPop population raster: "constrained" 2020 grid, people per 100 m cell, UN-adjusted.

One file covers the whole country, Rodrigues included. The fetch_energy_population rule runs this
only when the file is missing.
"""

import urllib.request
from pathlib import Path

import click


@click.command()
@click.option("--url", required=True, help="Download URL of the GeoTIFF.")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
def main(url, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, partial.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
    partial.replace(output)
    click.echo(f"downloaded: {output}")


if __name__ == "__main__":
    main()
