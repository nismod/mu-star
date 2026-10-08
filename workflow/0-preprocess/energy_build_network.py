"""Build one energy network with energy.build.build_network.

``--source`` picks the network: ``base`` (CEB substations, generators and 66 kV lines),
``inferred-osm`` (OpenStreetMap power features joined by the OSM roads near lit areas or power
assets) or ``inferred-provided`` (the CEB assets and 66 kV lines joined by the same roads). Options
not given take the build_network defaults. Validation warnings also go to stderr, so the Snakemake
log shows them.
"""

import logging
from pathlib import Path

import click

from energy.build import build_network

_PATH_OPTIONS = {"input_dir", "output_dir", "export_root", "roads_path", "power_path", "nightlight_targets"}


@click.command()
@click.option("--source", required=True, type=click.Choice(["base", "inferred-osm", "inferred-provided"]))
@click.option("--input-dir", "input_dir", type=click.Path(file_okay=False, path_type=str))
@click.option("--output-dir", "output_dir", type=click.Path(file_okay=False, path_type=str))
@click.option("--export-root", "export_root", type=click.Path(file_okay=False, path_type=str))
@click.option("--output-name", "output_name", type=str)
@click.option("--region", type=str)
@click.option("--network-type", "network_type", type=str)
@click.option("--roads-path", "roads_path", type=click.Path(path_type=str))
@click.option("--power-path", "power_path", type=click.Path(path_type=str))
@click.option("--nightlight-targets", "nightlight_targets", type=click.Path(path_type=str))
@click.option("--nightlight-support-distance-m", "nightlight_support_distance_m", type=float)
@click.option("--max-anchor-distance-m", "max_anchor_distance_m", type=float)
@click.option("--inferred-voltage-kv", "inferred_voltage_kv", type=float)
@click.option("--inferred-transmission-voltage-kv", "inferred_transmission_voltage_kv", type=float)
@click.option("--inferred-capacity-mva", "inferred_capacity_mva", type=float)
@click.option("--inferred-transmission-capacity-mva", "inferred_transmission_capacity_mva", type=float)
@click.option("--inferred-anchor-capacity-mva", "inferred_anchor_capacity_mva", type=float)
@click.option("--inferred-reference-line-length-km", "inferred_reference_line_length_km", type=float)
@click.option("--line-length-tolerance-fraction", "line_length_tolerance_fraction", type=float)
@click.option("--generation-capacity-tolerance-fraction", "generation_capacity_tolerance_fraction", type=float)
@click.option("--base-route-gap-tolerance-m", "base_route_gap_tolerance_m", type=float)
@click.option("--base-default-voltage-kv", "base_default_voltage_kv", type=float)
@click.option("--base-topology-capacity-mva", "base_topology_capacity_mva", type=float)
@click.option("--overwrite", is_flag=True, default=False)
def main(source, overwrite, **options):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    kwargs = {}
    for name, value in options.items():
        if value is None:
            continue
        kwargs[name] = Path(value) if name in _PATH_OPTIONS else value
    outputs = build_network(source, overwrite=overwrite, **kwargs)
    click.echo(f"wrote {outputs.network}")


if __name__ == "__main__":
    main()
