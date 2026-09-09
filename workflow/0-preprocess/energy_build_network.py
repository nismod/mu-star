"""Build one energy network product via energy.network_source.build_network.

``--source`` selects which network to build: ``base`` uses the provided
transmission assets; ``inferred-osm`` uses OpenStreetMap power features; and
``inferred-provided`` combines the provided assets with a road-based inferred
distribution network. Each rule passes only the options its source needs, and
the rest fall back to the build_network defaults.
"""

import os
from pathlib import Path

import click

from energy.network_source import build_network

_PATH_OPTIONS = {"input_dir", "output_dir", "export_root", "roads_path", "nightlight_targets"}


@click.command()
@click.option("--source", required=True, type=click.Choice(["base", "inferred-osm", "inferred-provided"]))
@click.option("--input-dir", "input_dir", type=click.Path(file_okay=False, path_type=str))
@click.option("--output-dir", "output_dir", type=click.Path(file_okay=False, path_type=str))
@click.option("--export-root", "export_root", type=click.Path(file_okay=False, path_type=str))
@click.option("--output-name", "output_name", type=str)
@click.option("--region", type=str)
@click.option("--network-type", "network_type", type=str)
@click.option("--roads-path", "roads_path", type=click.Path(path_type=str))
@click.option("--nightlight-targets", "nightlight_targets", type=click.Path(path_type=str))
@click.option("--nightlight-support-distance-m", "nightlight_support_distance_m", type=float)
@click.option("--max-anchor-distance-m", "max_anchor_distance_m", type=float)
@click.option("--inferred-voltage-kv", "inferred_voltage_kv", type=float)
@click.option("--inferred-transmission-voltage-kv", "inferred_transmission_voltage_kv", type=float)
@click.option("--inferred-capacity-mva", "inferred_capacity_mva", type=float)
@click.option("--inferred-reference-line-length-km", "inferred_reference_line_length_km", type=float)
@click.option("--line-length-tolerance-fraction", "line_length_tolerance_fraction", type=float)
@click.option("--generation-capacity-tolerance-fraction", "generation_capacity_tolerance_fraction", type=float)
@click.option("--base-route-gap-tolerance-m", "base_route_gap_tolerance_m", type=float)
@click.option("--base-default-voltage-kv", "base_default_voltage_kv", type=float)
@click.option("--base-topology-capacity-mva", "base_topology_capacity_mva", type=float)
@click.option("--overwrite", is_flag=True, default=False)
@click.option("--data-root", "data_root", type=click.Path(path_type=str))
def main(source, data_root, overwrite, **options):
    # Inferred builds resolve some OSM lookups internally; point them at the
    # same data tree the rule declared its inputs under.
    if data_root:
        os.environ["MU_STAR_DATA_ROOT"] = str(Path(data_root).resolve())
    kwargs = {}
    for name, value in options.items():
        if value is None:
            continue
        kwargs[name] = Path(value) if name in _PATH_OPTIONS else value
    build_network(source, overwrite=overwrite, **kwargs)


if __name__ == "__main__":
    main()
