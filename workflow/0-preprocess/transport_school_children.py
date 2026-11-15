"""School-age children per H3 cell and school level (draft).

Input: children by census area and age band (2022 census; source TBC),
transcribed to a CSV: area_name, district, row_type (aggregate | area),
age_band, children. Areas are matched to census codes by name, as for CA1
(transport.census.match_codes).

Each area's children are spread over its pixels by the scaled WorldPop
pattern, aggregated to H3 cells, and grouped into school levels
(school.levels: which age bands, and the enrolment rate).

Output: h3_index, level, children (enrolled school-age children by residence).
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

from transport.census import clean_code, match_codes
from transport.land import h3_resolution, read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--zoning", required=True)
@click.option("--children", "children_path", required=True)
@click.option("--codes", required=True)
@click.option("--zones", required=True, help="census zones (with codes and geometry)")
@click.option("--raster", required=True, help="scaled WorldPop")
@click.option("--output", required=True)
def main(config_path, zoning, children_path, codes, zones, raster, output):
    cfg = read_config(config_path)
    levels = cfg["school"]["levels"]

    table = pd.read_csv(children_path)
    table = table[table["row_type"].astype(str).str.strip().str.lower() == "area"].rename(
        columns={"district": "district_name"}
    )
    lookup = pd.read_csv(codes, dtype=str)
    lookup["zone_id"] = clean_code(lookup["zone_id"])
    table["zone_id"] = match_codes(table, lookup, cfg["districts"], cfg["inputs"].get("census_name_overrides") or {})
    if table["zone_id"].isna().any():
        raise ValueError(f"Unmatched areas: {table.loc[table.zone_id.isna(), 'area_name'].unique().tolist()}")

    band_level = {band: level for level, spec in levels.items() for band in spec["age_bands"]}
    table["level"] = table["age_band"].astype(str).str.strip().map(band_level)
    table = table.dropna(subset=["level"])
    rate = {level: spec["enrolment_rate"] for level, spec in levels.items()}
    table["children"] = pd.to_numeric(table["children"], errors="coerce") * table["level"].map(rate)
    per_area = table.groupby(["zone_id", "level"])["children"].sum().unstack(fill_value=0.0)

    census = geopandas.read_parquet(zones).reset_index(drop=True)
    with rasterio.open(raster) as src:
        weight = src.read(1).astype("float64")
        labels = features.rasterize(
            ((g, i + 1) for i, g in enumerate(census.to_crs(src.crs).geometry)),
            out_shape=weight.shape,
            transform=src.transform,
            fill=0,
            dtype="int32",
        )
        rows, cols = np.nonzero((weight > 0) & (labels > 0))
        xs, ys = xy(src.transform, rows, cols)
        lon, lat = Transformer.from_crs(src.crs, "EPSG:4326", always_xy=True).transform(np.asarray(xs), np.asarray(ys))

    label = labels[rows, cols]
    w = weight[rows, cols]
    area_total = np.bincount(label, weights=w, minlength=len(census) + 1)
    share = w / area_total[label]  # pixel's share of its area
    zone_ids = census["zone_id"].to_numpy()[label - 1]
    cells = np.array([h3.latlng_to_cell(y, x, h3_resolution(zoning)) for x, y in zip(lon, lat)])

    out = []
    for level in per_area.columns:
        children = per_area[level].reindex(zone_ids).fillna(0.0).to_numpy() * share
        out.append(pd.DataFrame({"h3_index": cells, "level": level, "children": children}))
    result = pd.concat(out).groupby(["h3_index", "level"], as_index=False)["children"].sum()
    result[result["children"] > 0].to_parquet(output, index=False)


if __name__ == "__main__":
    main()
