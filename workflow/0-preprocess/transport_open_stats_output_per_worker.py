"""Output per worker by ISIC section from Statistics Mauritius tables.

GVA at current basic prices by industry group (National Accounts, one year)
divided by employment by industry section (2022 census, Table CA3, Republic of
Mauritius, currently employed population 16+). The industry-group to ISIC
section mapping and its caveats are documented in transport.open_stats.

Writes the main table (item 9 of docs/src/transport-data.md) and, next to it,
`<output stem>-reconciliation.csv`: every published group with its employment,
the employment that is not matched (section U, 'not stated'), the labour
survey comparison, and the totals.
"""

from pathlib import Path

import click

from transport.open_stats import (
    build_output_per_worker,
    ensure_parent,
    parse_census_employment,
    parse_gva_table,
    parse_labour_employment,
    read_sheet,
)


@click.command()
@click.option("--gva-xlsx", required=True, type=click.Path(exists=True), help="National Accounts workbook")
@click.option("--gva-sheet", default="Table 3", show_default=True)
@click.option("--census-xls", required=True, type=click.Path(exists=True), help="Census Volume VII workbook")
@click.option("--census-sheet", default="CA3", show_default=True)
@click.option("--labour-xlsx", default="", help="Digest of Labour Statistics workbook (comparison only)")
@click.option("--labour-sheet", default="Table 1.4", show_default=True)
@click.option("--labour-year", default=2023, show_default=True, type=int)
@click.option("--year", default=2022, show_default=True, type=int, help="GVA year (census year)")
@click.option("--expected-total", default=538516, show_default=True, type=int, help="Census total employed (CA1)")
@click.option("--source-gva", required=True, help="Citation for the GVA table")
@click.option("--source-employment", required=True, help="Citation for the employment table")
@click.option("--output", required=True)
def main(
    gva_xlsx,
    gva_sheet,
    census_xls,
    census_sheet,
    labour_xlsx,
    labour_sheet,
    labour_year,
    year,
    expected_total,
    source_gva,
    source_employment,
    output,
):
    gva = parse_gva_table(read_sheet(gva_xlsx, gva_sheet), year)
    census = parse_census_employment(read_sheet(census_xls, census_sheet))
    labour = None
    if labour_xlsx:
        labour = parse_labour_employment(read_sheet(labour_xlsx, labour_sheet), labour_year)
    table, reconciliation = build_output_per_worker(
        gva, census, year, source_gva, source_employment, expected_total, labour, labour_year
    )
    out = ensure_parent(output)
    table.to_csv(out, index=False)
    reconciliation.to_csv(out.with_name(f"{Path(output).stem}-reconciliation.csv"), index=False)
    click.echo(f"{len(table)} sectors written to {out}")


if __name__ == "__main__":
    main()
