"""Published inter-island traffic (Mauritius, Rodrigues) from Statistics Mauritius digests.

Sources:
  - Digest of International Travel and Tourism Statistics, Tables 2a and 2b
    (passengers between Mauritius and Rodrigues, and Rodrigues and Reunion).
  - Digest of Rodrigues Statistics, Tables 20 and 22 (passengers by air and
    sea, all origins and destinations) and Table 30 (exports of selected
    commodities to Mauritius).

Statistics Mauritius publishes nothing found for Agalega or St Brandon, and no
freight from Mauritius to Rodrigues. This is a source table for a later step;
it is not keyed by link_id.
"""

import click

from transport.open_stats import (
    build_traffic,
    ensure_parent,
    parse_rodrigues_exports,
    parse_rodrigues_mode_year,
    read_sheet,
)


@click.command()
@click.option(
    "--tourism", required=True, type=click.Path(exists=True), help="Digest of International Travel and Tourism"
)
@click.option("--tourism-sheet", default="Tables 2a & 2b", show_default=True)
@click.option("--rodrigues", required=True, type=click.Path(exists=True), help="Digest of Rodrigues Statistics")
@click.option("--tourism-source", required=True)
@click.option("--rodrigues-source", required=True)
@click.option("--output", required=True)
def main(tourism, tourism_sheet, rodrigues, tourism_source, rodrigues_source, output):
    table = build_traffic(
        read_sheet(tourism, tourism_sheet),
        parse_rodrigues_mode_year(read_sheet(rodrigues, "Table 20")),
        parse_rodrigues_mode_year(read_sheet(rodrigues, "Table 22")),
        parse_rodrigues_exports(read_sheet(rodrigues, "Table 30")),
        {"tourism": tourism_source, "rodrigues": rodrigues_source},
    )
    table.to_csv(ensure_parent(output), index=False)
    click.echo(f"{len(table)} rows written to {output}")


if __name__ == "__main__":
    main()
