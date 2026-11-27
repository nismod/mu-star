"""Pre-process official economic accounts into a model constraint table."""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import rapidfuzz

SHEET_INDEX = 17
SKIPROWS = 3
MATCH_THRESHOLD = 75
SOURCE_UNITS = 1_000_000


def preprocess_local_econ(
    raw_data_path,
    custom_bounds_path,
    output_path,
    country_code,
    year,
    rupees_per_usd,
    log_path,
):
    """Convert the official accounts workbook into a national constraint table."""
    import logging

    import scalenav.oop as snoo
    from globdata import ISIC_CODES, REGION_INDEX
    from ibis import _

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("Pre-processing %s for %s (%s)", raw_data_path, country_code, year)

    conn = snoo.connect()

    econ_df = pd.read_excel(
        raw_data_path,
        sheet_name=SHEET_INDEX,
        skiprows=SKIPROWS,
    )

    new_colnames = []
    for value in econ_df.columns:
        if re.search(string=str(value), pattern=r"\d{4}"):
            active_year = str(value).split(" ")[0]
            new_colnames.append(active_year)
        elif re.search(string=str(value), pattern=r"^Unnamed: \d+"):
            new_colnames.append(active_year)
        else:
            new_colnames.append(str(value).replace(" ", "_").lower())

    output_categories = [str(value).replace(" ", "_").replace("'", "").lower() for value in econ_df.iloc[0].fillna("")]
    econ_df.columns = [
        f"{left}{'_' + right if right else ''}" for left, right in zip(new_colnames, output_categories, strict=True)
    ]
    econ_df_clean = econ_df.drop(index=[0, len(econ_df) - 1]).reset_index(drop=True)
    econ_df_clean = econ_df_clean.mask(econ_df_clean.eq("-"), pd.NA)

    sections = [value.split(" ")[1] for value in ISIC_CODES]
    descriptions = [value["description"].split("\n")[0] for value in ISIC_CODES.values()]
    isic_section = pd.DataFrame({"section": sections, "description": descriptions})

    best_match = pd.DataFrame.from_records(
        pd.DataFrame(
            [
                [rapidfuzz.fuzz.ratio(isic, value) for isic in isic_section.description]
                for value in econ_df_clean.kind_of_economic_activity
            ]
        ).apply(lambda row: (row.argmax(), row[row.argmax()]), axis=1),
        columns=["id", "match_score"],
    )
    econ_df_clean["section"] = isic_section.section[best_match["id"]].tolist()
    econ_df_clean["match_score"] = best_match["match_score"]

    nation = (
        conn.read_parquet(custom_bounds_path)
        .cast({"geometry": "GEOMETRY"})
        .filter(_.gid_0.isin([country_code]))
        .execute()
        .set_crs("epsg:4326")
        .set_index("gid_0")
    )

    # print(nation)

    years = [str(year)]
    output_columns = [f"{value}_gross_output_at_basic_prices" for value in years]
    output_table = econ_df_clean.query(f"match_score > {MATCH_THRESHOLD}").pivot_table(
        values=output_columns,
        columns=["section"],
        fill_value=0,
        aggfunc=lambda values: np.mean(np.sum(values) * SOURCE_UNITS / len(output_columns) / rupees_per_usd),
    )

    # print(output_table.head())

    output_table.index = [country_code]

    output_gdf = (
        output_table.join(nation)
        .set_geometry("geometry")
        .reset_index(inplace=False, names="gid_0")
        .merge(
            REGION_INDEX[["country", "gid_0", "sub_continent", "continent"]],
            on="gid_0",
            how="left",
        )
        .reset_index(drop=False, names="id")
    )

    # print(output_gdf.head())

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_gdf.to_parquet(output_path)
    logging.info("Wrote economic constraint table to %s", output_path)
    return output_gdf
