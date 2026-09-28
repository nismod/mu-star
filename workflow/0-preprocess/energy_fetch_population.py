"""Cache the WorldPop population raster for the region (opt-in download).

WorldPop's "constrained" 2020 grids give people per 100 m cell, adjusted to UN
totals. One file covers the whole country including Rodrigues. Nothing is
downloaded unless ``--allow-download`` is given (the fetch_energy_population
rule passes it when energy.population.allow_download is true).
"""

import urllib.request
from pathlib import Path

import click


@click.command()
@click.option("--url", required=True, help="Download URL of the GeoTIFF.")
@click.option("--output", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--allow-download", "allow_download", is_flag=True, default=False, help="Allow downloading.")
def main(url, output, allow_download):
    if output.is_file():
        click.echo(f"cached: {output}")
        return
    if not allow_download:
        raise click.ClickException(
            f"Population raster is not cached at {output}.\n"
            "Fetch it once (needs internet): set energy.population.allow_download: true in "
            "config/energy/energy.yaml and run `snakemake -c1 fetch_energy_population`."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, partial.open("wb") as handle:
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
    partial.replace(output)
    click.echo(f"downloaded: {output}")


if __name__ == "__main__":
    main()
