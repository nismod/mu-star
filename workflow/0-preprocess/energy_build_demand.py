"""Write one network's service areas, substation and node demand shares and demand levels (see energy.demand)."""

from pathlib import Path

import click

from energy.demand import build_demand_shares


@click.command()
@click.option("--nodes", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--aoi", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--population", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--nightlights", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--demand-levels", "demand_levels", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "output_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--weight-nightlights", "weight_nightlights", type=float, default=0.6, show_default=True)
@click.option("--weight-population", "weight_population", type=float, default=0.4, show_default=True)
def main(nodes, aoi, population, nightlights, demand_levels, output_dir, weight_nightlights, weight_population):
    outputs = build_demand_shares(
        nodes_path=nodes,
        aoi_path=aoi,
        population_path=population,
        nightlights_path=nightlights,
        demand_levels_path=demand_levels,
        output_dir=output_dir,
        weights={"nightlights": weight_nightlights, "population": weight_population},
    )
    click.echo(f"wrote {outputs.metadata.parent}")


if __name__ == "__main__":
    main()
