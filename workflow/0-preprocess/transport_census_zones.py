"""Join the census residential-zone table to its boundaries.

Output columns: zone_id (str), population, employed_residents,
employment_rate (= employed_residents / population), geometry.
"""

import click
import geopandas

from transport.land import read_config, read_table, require_columns


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--table", required=True)
@click.option("--boundaries", required=True)
@click.option("--output", required=True)
def main(config_path, table, boundaries, output):
    cfg = read_config(config_path)
    inputs = cfg["inputs"]
    columns = inputs["census_columns"]

    census = read_table(table, sheet_name=inputs.get("census_sheet") or 0)
    require_columns(census, columns.values(), "census table")
    census = census.rename(columns={v: k for k, v in columns.items()})[list(columns)]
    census["zone_id"] = census["zone_id"].astype(str).str.strip()
    # TODO: drop total / subtotal rows once we see the spreadsheet layout.
    if census["zone_id"].duplicated().any():
        raise ValueError(f"Duplicate census zone ids: {census.loc[census.zone_id.duplicated(), 'zone_id'].tolist()}")

    shapes = geopandas.read_file(boundaries)
    key = inputs["census_boundaries_zone_id"]
    require_columns(shapes, [key], "census boundaries")
    shapes = shapes[[key, "geometry"]].rename(columns={key: "zone_id"})
    shapes["zone_id"] = shapes["zone_id"].astype(str).str.strip()
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
