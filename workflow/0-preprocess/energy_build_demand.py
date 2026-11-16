"""Write one network's service areas, substation and node demand shares and demand levels (see energy.demand)."""

from pathlib import Path

import click

from energy.demand import DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV, build_demand_shares


@click.command()
@click.option("--nodes", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--edges", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--aoi", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--population", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--nightlights", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--demand-levels", "demand_levels", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "output_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--weight-nightlights", "weight_nightlights", type=float, default=0.6, show_default=True)
@click.option("--weight-population", "weight_population", type=float, default=0.4, show_default=True)
@click.option(
    "--max-radial-line-voltage-kv",
    "max_radial_line_voltage_kv",
    type=float,
    default=DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    show_default=True,
    help="Lines at or below this voltage are the distribution lines along which areas are measured.",
)
def main(
    nodes,
    edges,
    aoi,
    population,
    nightlights,
    demand_levels,
    output_dir,
    weight_nightlights,
    weight_population,
    max_radial_line_voltage_kv,
):
    outputs = build_demand_shares(
        nodes_path=nodes,
        edges_path=edges,
        aoi_path=aoi,
        population_path=population,
        nightlights_path=nightlights,
        demand_levels_path=demand_levels,
        output_dir=output_dir,
        weights={"nightlights": weight_nightlights, "population": weight_population},
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
    )
    click.echo(f"wrote {outputs.metadata.parent}")


if __name__ == "__main__":
    main()
