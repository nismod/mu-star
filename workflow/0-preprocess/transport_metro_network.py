"""Metro segments between consecutive stations on each line, both directions.

Provided schema:
    metro_stations.gpkg        station_id, name, geometry (Point)
    metro_lines.gpkg           line_id (e.g. main, branch), geometry (LineString)
    metro_line_stations.csv    line_id, station_id, seq (order along the line),
                               run_time_min_to_next (optional)

Metro Express has a main line (Port Louis - Curepipe) and a branch (Rose Hill
- Reduit). Headways come from metro.headway_min by line_id; train capacity
from metro.vehicle_capacity. Stations shared by two lines are interchanges.

Islands with no metro (e.g. Rodrigues): run without inputs, and empty
outputs are written.

Output segments: segment_id (<line>/<from>-<to> in line order, shared by
both directions), line_id, direction (0 = increasing seq, 1 = decreasing),
from_station, to_station, length_km, time_min (in-vehicle), headway_min,
capacity_per_train, geometry.
"""

import click
import geopandas
import pandas as pd
from shapely.ops import linemerge, substring

from transport.land import read_config, require_columns

SEGMENT_COLUMNS = [
    "segment_id",
    "line_id",
    "direction",
    "from_station",
    "to_station",
    "length_km",
    "time_min",
    "headway_min",
    "capacity_per_train",
    "geometry",
]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--stations", default="")
@click.option("--lines", default="")
@click.option("--line-stations", default="")
@click.option("--output-stations", required=True)
@click.option("--output-segments", required=True)
def main(config_path, stations, lines, line_stations, output_stations, output_segments):
    cfg = read_config(config_path)
    metro = cfg["metro"]
    crs = cfg["crs_metric"]

    if not stations:
        text = {c: pd.Series(dtype=str) for c in ("segment_id", "line_id", "from_station", "to_station")}
        numbers = {c: pd.Series(dtype=float) for c in ("length_km", "time_min", "headway_min", "capacity_per_train")}
        empty_stations = {"station_id": pd.Series(dtype=str), "line_id": pd.Series(dtype=str), "seq": []}
        geopandas.GeoDataFrame(empty_stations, geometry=[], crs=crs).to_parquet(output_stations)
        empty_segments = {**text, "direction": pd.Series(dtype=int), **numbers}
        geopandas.GeoDataFrame(empty_segments, geometry=[], crs=crs)[SEGMENT_COLUMNS].to_parquet(output_segments)
        return

    stations = geopandas.read_file(stations).to_crs(crs)
    require_columns(stations, ["station_id", "geometry"], "metro_stations")
    stations["station_id"] = stations["station_id"].astype(str)
    order = pd.read_csv(line_stations, dtype={"station_id": str, "line_id": str})
    require_columns(order, ["line_id", "station_id", "seq"], "metro_line_stations")
    line_geoms = geopandas.read_file(lines).to_crs(crs)
    require_columns(line_geoms, ["line_id"], "metro_lines")
    line_geoms["line_id"] = line_geoms["line_id"].astype(str)
    geometry_of = {line: linemerge(list(g.geometry)) for line, g in line_geoms.groupby("line_id")}

    calls = stations.merge(order, on="station_id", how="inner")
    calls["chainage_m"] = [geometry_of[line].project(point) for line, point in zip(calls.line_id, calls.geometry)]
    calls = calls.sort_values(["line_id", "seq"])
    calls.to_parquet(output_stations)

    rows = []
    for line, s in calls.groupby("line_id"):
        geom = geometry_of[line]
        headway = metro["headway_min"].get(line, metro["default_headway_min"])
        s = s.reset_index(drop=True)
        for i in range(len(s) - 1):
            a, b = s.iloc[i], s.iloc[i + 1]
            piece = substring(geom, min(a.chainage_m, b.chainage_m), max(a.chainage_m, b.chainage_m))
            length_km = piece.length / 1000.0
            run = a.get("run_time_min_to_next")
            time_min = run if pd.notna(run) else length_km / metro["default_speed_kph"] * 60.0
            segment_id = f"{line}/{a.station_id}-{b.station_id}"
            for direction, (u, v) in enumerate([(a.station_id, b.station_id), (b.station_id, a.station_id)]):
                rows.append(
                    {
                        "segment_id": segment_id,
                        "line_id": line,
                        "direction": direction,
                        "from_station": u,
                        "to_station": v,
                        "length_km": length_km,
                        "time_min": time_min,
                        "headway_min": headway,
                        "capacity_per_train": metro["vehicle_capacity"],
                        "geometry": piece,
                    }
                )
    geopandas.GeoDataFrame(rows, columns=SEGMENT_COLUMNS, crs=crs).to_parquet(output_segments)


if __name__ == "__main__":
    main()
