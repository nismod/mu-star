"""Clean the provided energy source data and write reviewable asset tables."""

from pathlib import Path

import click

from energy.intake import prepare_provided_data


@click.command()
@click.option(
    "--input-dir",
    "input_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=str),
)
@click.option(
    "--output-dir",
    "output_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=str),
)
def main(input_dir, output_dir):
    prepare_provided_data(Path(input_dir), Path(output_dir))


if __name__ == "__main__":
    main()
