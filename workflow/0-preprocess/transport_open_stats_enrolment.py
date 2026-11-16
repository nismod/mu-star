"""School enrolment by district and level from the Digest of Education Statistics.

Number of schools and enrolment for primary and secondary schools, by district
(nine districts of the Island of Mauritius, and Rodrigues as a whole). The
digest publishes no school-by-school list with enrolment, only district
tables: Tables 120 and 122 (primary), 132 and 140 (secondary).
"""

import click

from transport.open_stats import (
    EDUCATION_TABLES,
    build_enrolment,
    ensure_parent,
    parse_district_total,
    read_sheet,
)


@click.command()
@click.option("--digest", required=True, type=click.Path(exists=True), help="Digest of Education Statistics workbook")
@click.option("--year", required=True, type=int, help="Reference year of the digest tables")
@click.option(
    "--source-name", required=True, help="Citation prefix, e.g. 'Digest of Education Statistics 2025 (file name)'"
)
@click.option("--output", required=True)
def main(digest, year, source_name, output):
    schools, enrolment, sources = {}, {}, {}
    for level, (s_sheet, s_title, e_sheet, e_title) in EDUCATION_TABLES.items():
        schools[level] = parse_district_total(read_sheet(digest, s_sheet), s_title)
        enrolment[level] = parse_district_total(read_sheet(digest, e_sheet), e_title)
        sources[level] = f"{source_name}, {s_title} (schools) and {e_title} (enrolment)"
    table = build_enrolment(schools, enrolment, year, sources)
    table.to_csv(ensure_parent(output), index=False)
    click.echo(f"{len(table)} rows written to {output}")


if __name__ == "__main__":
    main()
