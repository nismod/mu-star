"""Combine residents and jobs into one zone table (one row per H3 cell).

Output: GeoParquet of points (population-weighted centroid, or the cell centre
where there are no residents) with columns zone_id (= h3_index), population,
employed_residents, jobs, census_zone_id, district, plus the jobs_<sector>
columns.

census_zone_id, district and island come from the census zone that contains
the H3 cell centre (or the nearest one, for cells on the coast), so cells with
jobs but no residents also get them. Only the cells of one island are kept.

With jobs.scale_to_employed_residents, the island's jobs are scaled to its
employed residents, so that jobs and employed residents are in the same units
(the radiation model adds them) and the island is closed (no commuting
between islands).
"""

import click
import geopandas
import h3
import numpy as np
import pandas as pd
from pyproj import Transformer

from transport.land import read_config


@click.command()
@click.option("--residents", required=True)
@click.option("--jobs", required=True)
@click.option("--census-zones", required=True)
@click.option("--island", required=True)
@click.option("--config", "config_path", default="config/transport/transport.yaml")
@click.option("--output", required=True)
def main(residents, jobs, census_zones, island, config_path, output):
    cfg = read_config(config_path)
    zones = pd.read_parquet(residents).merge(pd.read_parquet(jobs), on="h3_index", how="outer")
    value_columns = [
        c for c in zones.columns if c in ("population", "employed_residents", "jobs", "output") or c.startswith("jobs_")
    ]
    zones[value_columns] = zones[value_columns].fillna(0.0)
    zones = zones[(zones["employed_residents"] > 0) | (zones["jobs"] > 0)].copy()

    # Cell centre for zones without residents.
    to_metric = Transformer.from_crs("EPSG:4326", cfg["crs_metric"], always_xy=True)
    centres = np.array([h3.cell_to_latlng(c) for c in zones["h3_index"]])
    cx, cy = to_metric.transform(centres[:, 1], centres[:, 0])
    zones["centroid_x"] = zones["centroid_x"].fillna(pd.Series(cx, index=zones.index))
    zones["centroid_y"] = zones["centroid_y"].fillna(pd.Series(cy, index=zones.index))

    # Census zone and district of each cell, by its centre.
    centre_points = geopandas.GeoDataFrame(
        {"h3_index": zones["h3_index"].to_numpy()}, geometry=geopandas.points_from_xy(cx, cy), crs=cfg["crs_metric"]
    )
    census = geopandas.read_parquet(census_zones)[["zone_id", "district", "island_code", "geometry"]].to_crs(
        cfg["crs_metric"]
    )
    located = geopandas.sjoin_nearest(centre_points, census, how="left").drop_duplicates("h3_index")
    located = located.set_index("h3_index")
    zones["census_zone_id"] = zones["h3_index"].map(located["zone_id"])
    zones["district"] = zones["h3_index"].map(located["district"])
    zones["island_code"] = zones["h3_index"].map(located["island_code"])

    # One island only.
    zones = zones[zones["island_code"] == str(cfg["islands"][island]["census_island_code"])].copy()
    if zones.empty:
        raise ValueError(f"No zones for island {island!r}")
    if cfg["jobs"]["scale_to_employed_residents"]:
        job_columns = ["jobs", *(c for c in zones.columns if c.startswith("jobs_"))]
        zones[job_columns] = zones[job_columns] * zones["employed_residents"].sum() / zones["jobs"].sum()

    # TODO: clip to cfg["study_area"] (main island) once we have an island outline.
    zones = zones.rename(columns={"h3_index": "zone_id"})
    out = geopandas.GeoDataFrame(
        zones, geometry=geopandas.points_from_xy(zones.centroid_x, zones.centroid_y), crs=cfg["crs_metric"]
    ).drop(columns=["centroid_x", "centroid_y"])
    out.to_parquet(output)


if __name__ == "__main__":
    main()
