"""Scale WorldPop to the census zone totals, and report the comparison.

Each pixel is multiplied by census_population / worldpop_sum for the census
zone its centre falls in. Pixels outside every census zone are set to zero
(and their total reported in the check table).

Check table columns: zone_id, census_population, worldpop_population, ratio, flagged.
"""

import click
import geopandas
import numpy as np
import pandas as pd
import rasterio
from rasterio import features

from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--worldpop", required=True)
@click.option("--zones", required=True)
@click.option("--output-raster", required=True)
@click.option("--output-check", required=True)
def main(config_path, worldpop, zones, output_raster, output_check):
    cfg = read_config(config_path)["population"]
    zones = geopandas.read_parquet(zones).reset_index(drop=True)

    with rasterio.open(worldpop) as src:
        pop = src.read(1, masked=True).filled(0).astype("float64")
        pop[pop < 0] = 0
        profile = src.profile
        zones_r = zones.to_crs(src.crs)
        # Integer label per pixel: 1..n for zones, 0 outside.
        labels = features.rasterize(
            ((geom, index + 1) for index, geom in enumerate(zones_r.geometry)),
            out_shape=pop.shape,
            transform=src.transform,
            fill=0,
            dtype="int32",
        )

    sums = np.bincount(labels.ravel(), weights=pop.ravel(), minlength=len(zones) + 1)
    check = pd.DataFrame(
        {
            "zone_id": zones["zone_id"],
            "census_population": zones["population"].to_numpy(dtype=float),
            "worldpop_population": sums[1:],
        }
    )
    check["ratio"] = check["census_population"] / check["worldpop_population"]
    tol = float(cfg["check_ratio_tolerance"])
    check["flagged"] = (check["ratio"] > tol) | (check["ratio"] < 1 / tol) | ~np.isfinite(check["ratio"])
    outside = pd.DataFrame(
        [{"zone_id": "(outside census zones)", "census_population": 0.0, "worldpop_population": sums[0]}]
    )
    pd.concat([check, outside]).to_csv(output_check, index=False)

    if cfg["worldpop_mode"] == "scale":
        factor = np.concatenate([[0.0], np.nan_to_num(check["ratio"].to_numpy(), nan=0.0, posinf=0.0)])
        scaled = pop * factor[labels]
        # TODO: zones with census population but no WorldPop pixels (ratio inf)
        # lose their population here; spread it uniformly over the zone instead.
    else:
        scaled = pop

    profile.update(dtype="float32", nodata=0, compress="deflate")
    with rasterio.open(output_raster, "w", **profile) as dst:
        dst.write(scaled.astype("float32"), 1)


if __name__ == "__main__":
    main()
