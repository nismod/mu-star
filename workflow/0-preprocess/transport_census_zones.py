"""Census areas (2022): employed residents by Municipal Ward / Village Council
Area (and Rodrigues region), with codes, districts and boundaries.

Sources (2022 census, Volume VII: Economic Characteristics):

- Table CA1 (printed pp. 1-30): employment and activity status by area.
  Area names are printed in the rows, under district headings, with the
  national and district aggregates on the first page. Transcribed to a CSV
  (columns mapped in config: area_name, district, row_type, employed, and
  optionally population).
- Appendix II (pp. 474-476): the official 4-digit codes and names, by
  district (e.g. 1201 Arsenal, Pamplemousses). Appendices III-IV (pp.
  477-478): the six Rodrigues regions. Transcribed to a CSV: zone_id,
  zone_name, district_code.

Census codes (Methodology Report s3.4): island (1 digit; 1 Mauritius, 2
Rodrigues, 3 Agalega), district (+1; e.g. 11 Port Louis), area (+2).

Method: keep the CA1 rows with row_type "area" (so national, district and
urban/rural aggregates are never counted), and match each to its code by name
within its district. Names are compared without accents, case or
punctuation. A name that is unique across the lookup is matched even without
a district (e.g. the Rodrigues regions). census_name_overrides
("printed name|district": "code") fixes anything left. Unmatched rows are an
error, listed in full.

Output columns: zone_id (4-digit code, str), zone_name, district (2-digit
code), island_code, employed_residents, population (NaN if not given),
scale_target (what WorldPop is scaled to: population, or employed residents
if there is no population), employment_rate (employed / scale_target),
geometry.
"""

import click
import geopandas
import numpy as np
import pandas as pd

from transport.census import clean_code, match_codes
from transport.land import read_config, require_columns


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--table", required=True, help="CA1 transcription (CSV)")
@click.option("--codes", required=True, help="Appendix II-IV area codes (CSV)")
@click.option("--boundaries", required=True)
@click.option("--output", required=True)
def main(config_path, table, codes, boundaries, output):
    cfg = read_config(config_path)
    inputs = cfg["inputs"]
    columns = {k: v for k, v in inputs["census_columns"].items() if v}

    census = pd.read_csv(table)
    census.columns = census.columns.str.strip()
    require_columns(census, columns.values(), "CA1 transcription")
    census = census.rename(columns={v: k for k, v in columns.items()})
    census = census[census["row_type"].astype(str).str.strip().str.lower() == "area"].copy()

    lookup = pd.read_csv(codes, dtype=str)
    require_columns(lookup, ["zone_id", "zone_name", "district_code"], "area code lookup")
    lookup["zone_id"] = clean_code(lookup["zone_id"])
    lookup["district_code"] = lookup["district_code"].str.strip()

    census["zone_id"] = match_codes(census, lookup, cfg["districts"], inputs.get("census_name_overrides") or {})
    unmatched = census[census["zone_id"].isna()]
    if len(unmatched):
        listed = unmatched[["area_name", *(["district_name"] if "district_name" in census else [])]]
        raise ValueError(f"CA1 areas with no code (add census_name_overrides):\n{listed.to_string()}")
    if census["zone_id"].duplicated().any():
        raise ValueError(
            f"Areas matched to the same code: {census.loc[census.zone_id.duplicated(), 'zone_id'].tolist()}"
        )

    lookup = lookup.set_index("zone_id")
    census["zone_name"] = census["zone_id"].map(lookup["zone_name"])
    census["district"] = census["zone_id"].map(lookup["district_code"])
    census["island_code"] = census["zone_id"].str[0]
    mismatch = census[census["district"] != census["zone_id"].str[:2]]
    if len(mismatch):
        print(f"Warning: {len(mismatch)} areas whose district differs from their code prefix")
    census["employed_residents"] = pd.to_numeric(census["employed_residents"], errors="coerce")
    census["population"] = pd.to_numeric(census["population"], errors="coerce") if "population" in census else np.nan
    census["scale_target"] = census["population"].fillna(census["employed_residents"])
    census["employment_rate"] = census["employed_residents"] / census["scale_target"]

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
            f"Census/boundary mismatch: no boundary for {sorted(no_shape)}; no CA1 row for {sorted(no_row)}"
        )

    keep = [
        "zone_id",
        "zone_name",
        "district",
        "island_code",
        "employed_residents",
        "population",
        "scale_target",
        "employment_rate",
    ]
    zones = shapes.merge(census[keep], on="zone_id").to_crs(cfg["crs_metric"])
    zones.to_parquet(output)


if __name__ == "__main__":
    main()
