"""Join the 2022 census residential-zone table to its zone boundaries.

Census geographical codes (Methodology Report s3.4) are hierarchical:
island (1 digit), district (+1), municipal ward / village council area (+2),
enumeration area (+2). E.g. 1112 = Mauritius (1), Port Louis (11), Ward 2.

- Rows whose code does not have census_zone_code_digits digits are
  aggregates (Republic, island, district, urban/rural) and are dropped.
- With census_sex_column set, only the census_sex_value rows (e.g. "Both
  sexes") are kept.
- The district comes from the Appendix II lookup (zone_id, zone_name,
  district_code) if it exists, else from the first two digits of the code.
  The island code is the first digit.

Codes are kept as strings, with leading zeros.

Output columns: zone_id (str), zone_name (if in the lookup), district
(2-digit code), island_code, population, employed_residents,
employment_rate (= employed_residents / population), geometry.
"""

from pathlib import Path

import click
import geopandas
import pandas as pd

from transport.land import read_config, read_table, require_columns


def clean_code(values: pd.Series) -> pd.Series:
    """Census codes as strings: strip spaces and any '.0' left by spreadsheets."""
    return values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--table", required=True)
@click.option("--boundaries", required=True)
@click.option("--districts", "district_lookup", default="", help="Appendix II lookup CSV (optional)")
@click.option("--output", required=True)
def main(config_path, table, boundaries, district_lookup, output):
    cfg = read_config(config_path)
    inputs = cfg["inputs"]
    columns = inputs["census_columns"]
    digits = int(inputs["census_zone_code_digits"])

    census = read_table(table, sheet_name=inputs.get("census_sheet") or 0, dtype={columns["zone_id"]: str})
    require_columns(census, columns.values(), "census table")
    if inputs.get("census_sex_column"):
        census = census[census[inputs["census_sex_column"]].astype(str).str.strip() == inputs["census_sex_value"]]
    census = census.rename(columns={v: k for k, v in columns.items()})[list(columns)]
    census["zone_id"] = clean_code(census["zone_id"])
    census = census[census["zone_id"].str.fullmatch(rf"\d{{{digits}}}")]
    if census["zone_id"].duplicated().any():
        raise ValueError(f"Duplicate census zone ids: {census.loc[census.zone_id.duplicated(), 'zone_id'].tolist()}")

    census["island_code"] = census["zone_id"].str[0]
    census["district"] = census["zone_id"].str[:2]
    if district_lookup and Path(district_lookup).exists():
        lookup = pd.read_csv(district_lookup, dtype=str)
        require_columns(lookup, ["zone_id", "district_code"], "Appendix II district lookup")
        lookup["zone_id"] = clean_code(lookup["zone_id"])
        lookup = lookup.set_index("zone_id")
        census["district"] = census["zone_id"].map(lookup["district_code"].str.strip()).fillna(census["district"])
        if "zone_name" in lookup:
            census["zone_name"] = census["zone_id"].map(lookup["zone_name"])
        mismatch = census[census["district"] != census["zone_id"].str[:2]]
        if len(mismatch):
            # Not an error: report it, as the code prefix should match the lookup.
            print(f"Warning: {len(mismatch)} zones whose district differs from their code prefix")

    shapes = geopandas.read_file(boundaries)
    key = inputs["census_boundaries_zone_id"]
    require_columns(shapes, [key], "census boundaries")
    shapes = shapes[[key, "geometry"]].rename(columns={key: "zone_id"})
    shapes["zone_id"] = clean_code(shapes["zone_id"])
    shapes = shapes.dissolve("zone_id", as_index=False)

    no_shape = set(census.zone_id) - set(shapes.zone_id)
    no_row = set(shapes.zone_id) - set(census.zone_id)
    if no_shape or no_row:
        raise ValueError(
            f"Census/boundary mismatch: no boundary for {sorted(no_shape)}; no census row for {sorted(no_row)}"
        )

    zones = shapes.merge(census, on="zone_id")
    zones["employment_rate"] = zones["employed_residents"] / zones["population"]
    zones = zones.to_crs(cfg["crs_metric"])
    zones.to_parquet(output)


if __name__ == "__main__":
    main()
