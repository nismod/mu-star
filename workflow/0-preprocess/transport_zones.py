"""Combine residents and jobs into one zone table (one row per H3 cell).

Output: GeoParquet of points (population-weighted centroid, or the cell centre
where there are no residents) with columns zone_id (= h3_index), population,
employed_residents, jobs, census_zone_id, plus the jobs_<sector> columns.
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
@click.option("--config", "config_path", default="config/transport/transport.yaml")
@click.option("--output", required=True)
def main(residents, jobs, config_path, output):
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

    # TODO: clip to cfg["study_area"] (main island) once we have an island outline.
    zones = zones.rename(columns={"h3_index": "zone_id"})
    out = geopandas.GeoDataFrame(
        zones, geometry=geopandas.points_from_xy(zones.centroid_x, zones.centroid_y), crs=cfg["crs_metric"]
    ).drop(columns=["centroid_x", "centroid_y"])
    out.to_parquet(output)


if __name__ == "__main__":
    main()
