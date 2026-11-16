"""Schools of one island from open data (transport-data item 10).

Sources, combined:
  * the official primary school list of the Mauritius geoportal (layer `primary_schools`:
    259 schools, name and address, no enrolment): authoritative for primary schools on
    Mauritius. Pass it with --official (none for Rodrigues);
  * OpenStreetMap `amenity=school` nodes and ways (ways reduced to their centroid).

OSM schools are classified `primary` or `secondary` by `isced:level`, then by name keywords
(see transport.open_osm.classify_school). Pre-schools, tertiary / vocational institutes and
tuition centres are excluded. Features that cannot be classified (no tag, no keyword, or
both) default to `primary` and are flagged `level_source = default`. An OSM school is dropped
as a duplicate of an official primary school if it is within 150 m and has a similar name (generic
words such as "Government School" ignored) or no name, within 300 m with an identical core name, or
within 40 m and classified primary.

PLACEHOLDER CAPACITY. Enrolment is not published as open data, so `enrolment` is left empty
and `capacity` is set to a flat default per level (PLACEHOLDER_CAPACITY below; an OSM `capacity`
tag is used where it is a plausible number). The school trip model (transport_school_od.py)
uses enrolment, else capacity, as the number of places, so with these defaults every school of a
level attracts the same number of pupils. REPLACE `capacity` by Ministry of Education enrolment
per school (Mauritius: Ministry of Education and Human Resources, Tertiary Education and
Scientific Research, "Education statistics"; Rodrigues: Commission for Education) when available:
put it in `enrolment`.

Output: layer of the GeoPackage (EPSG:4326, Point): school_id, name, level, enrolment (NaN),
capacity, capacity_source (default|osm), level_source (official|isced|name|default), source
(geoportal|osm), osm_id. A summary JSON is written next to it.

Data: (c) OpenStreetMap contributors, ODbL 1.0; the official list is open data of the Government
of Mauritius geoportal (https://geoportal.govmu.org); check its licence before redistributing.
"""

import json
import math
from pathlib import Path

import click
import geopandas
import pandas as pd
from shapely.geometry import Point

from transport import open_osm

# Placeholder places per school (pupils), NOT observed: replace with Ministry of Education
# enrolment. Order of magnitude only: Mauritius has roughly 100k primary pupils in about 300
# primary schools and about 100k secondary pupils in about 180 secondary schools.
PLACEHOLDER_CAPACITY = {"primary": 350, "secondary": 600}
OSM_CAPACITY_RANGE = (30, 5000)  # accept an OSM `capacity` tag within this range
DUPLICATE_RADIUS_M = 150.0  # OSM school this close to an official one with a similar name is a duplicate
DUPLICATE_RADIUS_ANY_NAME_M = 40.0  # ... this close, any name, if classified primary
METRIC_CRS = "EPSG:32740"
COLUMNS = [
    "school_id",
    "name",
    "level",
    "enrolment",
    "capacity",
    "capacity_source",
    "level_source",
    "source",
    "osm_id",
    "geometry",
]


def osm_capacity(tags):
    try:
        value = float(tags.get("capacity", ""))
    except ValueError:
        return math.nan
    return value if OSM_CAPACITY_RANGE[0] <= value <= OSM_CAPACITY_RANGE[1] else math.nan


@click.command()
@click.option("--pbf", required=True)
@click.option("--island", type=click.Choice(["mauritius", "rodrigues"]), required=True)
@click.option("--official", default="", help="geoportal primary_schools.geojson (Mauritius)")
@click.option("--output", required=True, help="schools.gpkg")
@click.option("--summary", required=True)
def main(pbf, island, official, output, summary):
    features = open_osm.read_features(pbf, lambda t: t.get("amenity") == "school")
    stats = {"island": island, "osm_schools_all_islands": len(features)}

    rows = []
    excluded = 0
    for f in features:
        if open_osm.island_of(*f["xy"]) != island:
            continue
        level, how = open_osm.classify_school(f["tags"])
        if level is None:
            excluded += 1
            continue
        cap = osm_capacity(f["tags"])
        rows.append(
            {
                "name": f["tags"].get("name"),
                "level": level,
                "level_source": how,
                "capacity": cap,
                "capacity_source": "osm" if not math.isnan(cap) else "default",
                "source": "osm",
                "osm_id": f"{f['osm_type']}{f['osm_id']}",
                "geometry": Point(f["xy"]),
            }
        )
    osm = geopandas.GeoDataFrame(rows, columns=[c for c in COLUMNS if c not in ("school_id", "enrolment")], crs=4326)
    stats["osm_excluded_not_primary_or_secondary"] = excluded
    stats["osm_on_island"] = len(osm)

    frames = [osm]
    duplicates = 0
    if official:
        off = geopandas.read_file(official).to_crs(4326)
        off = geopandas.GeoDataFrame(
            {
                "name": off["Name"].map(open_osm.clean_official_name),
                "level": "primary",
                "level_source": "official",
                "capacity": math.nan,
                "capacity_source": "default",
                "source": "geoportal",
                "osm_id": None,
                "geometry": off.geometry,
            },
            crs=4326,
        )
        off["official_id"] = [f"geoportal-{i + 1}" for i in range(len(off))]
        off = off[[open_osm.island_of(p.x, p.y) == island for p in off.geometry]].reset_index(drop=True)
        off_m = off.to_crs(METRIC_CRS)
        osm_m = osm.to_crs(METRIC_CRS)
        keep = []
        for idx, row in osm_m.iterrows():
            if row["level"] != "primary":
                keep.append(True)
                continue
            match = open_osm.match_official(
                row["name"] or "", (row.geometry.x, row.geometry.y), off_m, DUPLICATE_RADIUS_M
            )
            if match is None and len(off_m):
                nearest = off_m.geometry.distance(row.geometry).min()
                if nearest <= DUPLICATE_RADIUS_ANY_NAME_M:
                    match = True
            keep.append(match is None)
        duplicates = len(keep) - sum(keep)
        osm = osm[keep]
        frames = [off, osm]
    stats["osm_dropped_as_duplicate_of_official"] = int(duplicates)
    stats["official_on_island"] = len(frames[0]) if official else 0

    schools = pd.concat(frames, ignore_index=True)
    schools = geopandas.GeoDataFrame(schools, geometry="geometry", crs=4326)
    schools["school_id"] = [
        official_id if isinstance(official_id, str) else f"osm-{osm_id}"
        for official_id, osm_id in zip(
            schools.get("official_id", [None] * len(schools)), schools["osm_id"], strict=True
        )
    ]
    schools["name"] = schools["name"].fillna("")
    default = schools["level"].map(PLACEHOLDER_CAPACITY)
    schools["capacity"] = schools["capacity"].fillna(default).astype(float)
    schools["enrolment"] = math.nan
    schools = schools[COLUMNS]
    if schools["school_id"].duplicated().any():
        raise click.ClickException("duplicate school_id")

    stats["total"] = len(schools)
    stats["by_level"] = schools["level"].value_counts().to_dict()
    stats["by_source_and_level"] = {
        f"{s}/{lv}": int(n) for (s, lv), n in schools.groupby(["source", "level"]).size().items()
    }
    stats["by_level_source"] = schools["level_source"].value_counts().to_dict()
    stats["osm_unclassified_defaulted_to_primary"] = int(
        ((schools.source == "osm") & (schools.level_source == "default")).sum()
    )
    stats["placeholder_capacity"] = PLACEHOLDER_CAPACITY
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    schools.to_file(output, layer="schools", driver="GPKG")
    Path(summary).write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
