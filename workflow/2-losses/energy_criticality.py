"""Write the N-1 criticality tables of inferred-provided by power flow (see energy.criticality)."""

import logging
from pathlib import Path

import click

from energy.criticality import DEFAULT_ISLAND_CARRIERS, DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV, build_criticality


@click.command()
@click.option("--network", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--substation-weights", "substation_weights", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--demand-levels", "demand_levels", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "output_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--line-circuits", "line_circuits", type=click.Path(exists=True, path_type=Path))
@click.option("--substation-names", "substation_names", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--line-type",
    "line_types",
    type=(int, str),
    multiple=True,
    help="A design voltage (kV) and the PyPSA line type of one circuit; repeat for each design voltage.",
)
@click.option(
    "--max-radial-line-voltage-kv",
    "max_radial_line_voltage_kv",
    type=float,
    default=DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    show_default=True,
    help="Lines above this voltage enter the power flow; lines at or below it are distribution lines.",
)
@click.option("--s-max-pu", "s_max_pu", type=float, default=1.0, show_default=True)
@click.option("--island-carrier", "island_carriers", multiple=True, default=DEFAULT_ISLAND_CARRIERS, show_default=True)
def main(
    network,
    substation_weights,
    demand_levels,
    output_dir,
    line_circuits,
    substation_names,
    line_types,
    max_radial_line_voltage_kv,
    s_max_pu,
    island_carriers,
):
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.WARNING)
    for name in ("pypsa", "linopy"):
        logging.getLogger(name).setLevel(logging.ERROR)
    outputs = build_criticality(
        network_path=network,
        substation_weights_path=substation_weights,
        demand_levels_path=demand_levels,
        output_dir=output_dir,
        line_circuits_path=line_circuits,
        substation_names_path=substation_names,
        line_types=dict(line_types) or None,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        s_max_pu=s_max_pu,
        island_carriers=tuple(island_carriers),
    )
    click.echo(f"wrote {outputs.metadata.parent}")


if __name__ == "__main__":
    main()
