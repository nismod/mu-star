"""Parse census Table CA27 (2022): employed population by district of residence
and district of workplace.

CA27 (Volume VII: Economic Characteristics, p. 347 onwards; district
breakdown from p. 349) has residence districts in rows and workplace
districts in columns, with sex and age breakdowns. The workplace columns are
the nine districts of Mauritius plus Not stated, Rodrigues and Outer Islands,
and Outside Mauritius.

Input: a CSV transcribed from the PDF, one row per residence district x sex x
age group, with a column per workplace (layout in config:
transport_land.inputs.census_district_od_layout). Only the both-sexes,
all-ages rows for the nine Mauritius residence districts are used, so that
aggregate rows (Republic, island, urban/rural, district totals, male /
female) are never double counted.

Outputs
    district OD (parquet): origin_district, destination_district (2-digit
        census district codes), workers. Nine by nine.
    summary (json): totals, the workers in the three non-district workplace
        columns (kept for audit, not used as targets), and by residence district.
"""

import json

import click
import pandas as pd

from transport.land import read_config, require_columns


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--table", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, table, output, output_summary):
    cfg = read_config(config_path)
    layout = cfg["inputs"]["census_district_od_layout"]
    code_of = {name: code for code, name in cfg["districts"].items()}

    raw = pd.read_csv(table)
    raw.columns = raw.columns.str.strip()
    residence = layout["residence_column"]
    require_columns(raw, [residence], "CA27 transcription")
    for column_key, value_key in (("sex_column", "sex_value"), ("age_column", "age_value")):
        column = layout.get(column_key)
        if column:
            require_columns(raw, [column], "CA27 transcription")
            raw = raw[raw[column].astype(str).str.strip() == layout[value_key]]
    raw[residence] = raw[residence].astype(str).str.strip()
    rows = raw[raw[residence].isin(code_of)]
    if rows[residence].duplicated().any():
        raise ValueError("CA27: more than one both-sexes, all-ages row for a residence district")
    missing_rows = sorted(set(code_of) - set(rows[residence]))
    missing_columns = sorted(set(code_of) - set(raw.columns))
    if missing_rows or missing_columns:
        raise ValueError(f"CA27: missing residence districts {missing_rows} or workplace columns {missing_columns}")

    other = [c for c in layout["other_workplaces"] if c in raw.columns]
    absent_other = sorted(set(layout["other_workplaces"]) - set(other))
    matrix = rows.set_index(residence)[list(code_of)].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    extra = rows.set_index(residence)[other].apply(pd.to_numeric, errors="coerce").fillna(0.0)

    od = matrix.rename(index=code_of, columns=code_of).stack().rename("workers").reset_index()
    od.columns = ["origin_district", "destination_district", "workers"]
    od.to_parquet(output, index=False)

    total_column = next((c for c in layout.get("drop") or [] if c in rows.columns), None)
    summary = {
        "workers_in_district_od": float(matrix.to_numpy().sum()),
        "other_workplaces": {c: float(extra[c].sum()) for c in other},
        "other_workplace_columns_missing": absent_other,
        "by_residence_district": {
            code_of[d]: {"district_od": float(matrix.loc[d].sum()), **{c: float(extra.loc[d, c]) for c in other}}
            for d in matrix.index
        },
    }
    if total_column:
        # Check: districts + other workplaces = the published row total.
        published = pd.to_numeric(rows.set_index(residence)[total_column], errors="coerce")
        summary["row_total_check_max_abs_difference"] = float(
            (matrix.sum(axis=1) + extra.sum(axis=1) - published).abs().max()
        )
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
