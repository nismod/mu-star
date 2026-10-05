"""Download a region's OpenStreetMap roads, power features and area-of-interest outline, if missing.

Existing files are kept unless ``--overwrite`` is given. Without ``--allow-download`` (which the
fetch_energy_osm rule passes) a missing file is an error.
"""

from pathlib import Path

import click

from energy.osm import OSMDownloadRequired, fetch_osm_aoi, fetch_osm_power_features, fetch_osm_roads


@click.command()
@click.option("--region", required=True, help="Region key or OpenStreetMap place name, e.g. mauritius-rodrigues.")
@click.option("--network-type", "network_type", default="drive", show_default=True, help="osmnx road detail.")
@click.option("--data-root", "data_root", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--allow-download", "allow_download", is_flag=True, default=False, help="Allow downloading.")
@click.option("--overwrite", is_flag=True, default=False, help="Fetch again even if the cache exists.")
def main(region, network_type, data_root, allow_download, overwrite):
    steps = (
        ("roads", lambda: fetch_osm_roads(region, network_type=network_type, **options)),
        ("power features", lambda: fetch_osm_power_features(region, **options)),
        ("area of interest", lambda: fetch_osm_aoi(region, **options)),
    )
    options = {"allow_download": allow_download, "overwrite": overwrite, "data_root": data_root}
    try:
        for label, fetch in steps:
            click.echo(f"{label}: {fetch()}")
    except OSMDownloadRequired as error:
        raise click.ClickException(str(error)) from error


if __name__ == "__main__":
    main()
