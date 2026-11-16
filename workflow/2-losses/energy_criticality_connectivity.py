"""Write the N-1 criticality tables of inferred-provided by connectivity (see energy.connectivity)."""

import logging
from pathlib import Path

import click

from energy.connectivity import build_connectivity_criticality
from energy.criticality import DEFAULT_ISLAND_CARRIERS, DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV


@click.command()
@click.option("--network", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--substation-weights", "substation_weights", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--node-weights", "node_weights", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--demand-levels", "demand_levels", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "output_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--line-circuits", "line_circuits", type=click.Path(exists=True, path_type=Path))
@click.option("--substation-names", "substation_names", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--max-radial-line-voltage-kv",
    "max_radial_line_voltage_kv",
    type=float,
    default=DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    show_default=True,
    help="Lines above this voltage are run meshed; lines at or below it are distribution lines, run radially.",
)
@click.option("--island-carrier", "island_carriers", multiple=True, default=DEFAULT_ISLAND_CARRIERS, show_default=True)
def main(
    network,
    substation_weights,
    node_weights,
    demand_levels,
    output_dir,
    line_circuits,
    substation_names,
    max_radial_line_voltage_kv,
    island_carriers,
):
    logging.getLogger("pypsa").setLevel(logging.ERROR)
    outputs = build_connectivity_criticality(
        network_path=network,
        substation_weights_path=substation_weights,
        node_weights_path=node_weights,
        demand_levels_path=demand_levels,
        output_dir=output_dir,
        line_circuits_path=line_circuits,
        substation_names_path=substation_names,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        island_carriers=tuple(island_carriers),
    )
    click.echo(f"wrote {outputs.metadata.parent}")


if __name__ == "__main__":
    main()
