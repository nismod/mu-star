"""Aggregate (scaled) population pixels to H3 cells.

"population" is the scaled raster summed over the cell: persons where the
census gives population, else employed residents (see transport_census_zones,
scale_target); it is the weight for the centroid either way.

Output columns: h3_index, population, employed_residents, census_zone_id
(the census zone holding most of the cell's population), centroid_x,
centroid_y (population-weighted, in crs_metric).
"""

import click
import geopandas
import h3
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio import features
from rasterio.transform import xy

from transport.land import h3_resolution, read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--zoning", required=True)
@click.option("--raster", required=True)
@click.option("--zones", required=True)
@click.option("--output", required=True)
def main(config_path, zoning, raster, zones, output):
    cfg = read_config(config_path)
    resolution = h3_resolution(zoning)
    zones = geopandas.read_parquet(zones).reset_index(drop=True)

    with rasterio.open(raster) as src:
        pop = src.read(1).astype("float64")
        zones_r = zones.to_crs(src.crs)
        labels = features.rasterize(
            ((geom, index + 1) for index, geom in enumerate(zones_r.geometry)),
            out_shape=pop.shape,
            transform=src.transform,
            fill=0,
            dtype="int32",
        )
        rows, cols = np.nonzero(pop > 0)
        xs, ys = xy(src.transform, rows, cols)
        to_wgs84 = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True)
        to_metric = Transformer.from_crs(src.crs, cfg["crs_metric"], always_xy=True)

    lon, lat = to_wgs84.transform(np.asarray(xs), np.asarray(ys))
    mx, my = to_metric.transform(np.asarray(xs), np.asarray(ys))
    label = labels[rows, cols]
    rate = np.concatenate([[0.0], zones["employment_rate"].to_numpy(dtype=float)])

    pixels = pd.DataFrame(
        {
            # H3 assignment by pixel centre. WorldPop pixels (~100 m) are much
            # smaller than res-8 cells (~460 m edge), so this is fine there.
            "h3_index": [h3.latlng_to_cell(y, x, resolution) for x, y in zip(lon, lat)],
            "population": pop[rows, cols],
            "employed_residents": pop[rows, cols] * rate[label],
            "census_zone_id": np.where(label > 0, zones["zone_id"].to_numpy()[np.maximum(label - 1, 0)], None),
            "wx": mx * pop[rows, cols],
            "wy": my * pop[rows, cols],
        }
    )
    cells = pixels.groupby("h3_index").agg(
        population=("population", "sum"),
        employed_residents=("employed_residents", "sum"),
        wx=("wx", "sum"),
        wy=("wy", "sum"),
    )
    cells["centroid_x"] = cells.pop("wx") / cells["population"]
    cells["centroid_y"] = cells.pop("wy") / cells["population"]
    majority = (
        pixels.groupby(["h3_index", "census_zone_id"])["population"]
        .sum()
        .reset_index()
        .sort_values("population")
        .drop_duplicates("h3_index", keep="last")
        .set_index("h3_index")["census_zone_id"]
    )
    cells["census_zone_id"] = majority
    cells.reset_index().to_parquet(output, index=False)


if __name__ == "__main__":
    main()
