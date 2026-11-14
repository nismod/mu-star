"""Parse census Table CA27 (travel to work, district to district).

DRAFT: the layout options in config (transport_land.inputs.
census_district_od_layout) are a guess until we see the file. The parser
expects a matrix with residence districts in rows and workplace districts in
columns.

Outputs
    district OD (parquet): origin_district, destination_district, workers
    summary (json): totals by residence district, and the workers in the
        "other workplace" columns (abroad, at home, ...) left out of the OD
"""

import json

import click
import pandas as pd

from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--table", required=True)
@click.option("--zones", required=True, help="census zones, to check district names")
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, table, zones, output, output_summary):
    cfg = read_config(config_path)
    layout = cfg["inputs"]["census_district_od_layout"]

    raw = pd.read_excel(
        table, sheet_name=layout.get("sheet") or 0, header=layout["header_row"], index_col=layout["index_column"]
    )
    raw.index = raw.index.astype(str).str.strip()
    raw.columns = raw.columns.astype(str).str.strip()
    drop = set(layout.get("drop") or [])
    raw = raw.loc[~raw.index.isin(drop) & raw.index.notna() & (raw.index != "nan"), ~raw.columns.isin(drop)]
    raw = raw.loc[:, ~raw.columns.str.startswith("Unnamed")]
    raw = raw.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    rename = layout.get("rename") or {}
    raw = raw.rename(index=rename, columns=rename)

    other = [c for c in layout.get("other_workplaces") or [] if c in raw.columns]
    matrix = raw.drop(columns=other)

    districts = set(pd.read_parquet(zones, columns=["district"])["district"])
    unknown = (set(matrix.index) | set(matrix.columns)) - districts
    # Districts outside the study area (e.g. Rodrigues) are expected here; the
    # radiation rule only uses districts that have zones.
    od = matrix.stack().rename("workers").reset_index()
    od.columns = ["origin_district", "destination_district", "workers"]
    od.to_parquet(output, index=False)

    summary = {
        "workers": float(raw.to_numpy().sum()),
        "workers_in_od": float(matrix.to_numpy().sum()),
        "other_workplaces": {c: float(raw[c].sum()) for c in other},
        "by_residence_district": raw.sum(axis=1).to_dict(),
        "districts_not_in_census_zones": sorted(unknown),
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
