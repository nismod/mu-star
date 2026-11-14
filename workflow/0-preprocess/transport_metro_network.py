"""Metro segments between consecutive stations, both directions.

Provided schema:
    metro_stations.gpkg        station_id, name, geometry (Point)
    metro_line.gpkg            one LineString for the line (single line)
    metro_line_stations.csv    station_id, seq (order along the line),
                               run_time_min_to_next (optional), headway_min (optional)

Output segments: segment_id (shared by both directions: <from>-<to> in line
order), direction (0 = increasing seq, 1 = decreasing), from_station,
to_station, length_km, time_min (in-vehicle), headway_min, capacity_per_train, geometry.
"""

import click
import geopandas
import pandas as pd
from shapely.ops import substring

from transport.land import read_config, require_columns


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--stations", required=True)
@click.option("--line", required=True)
@click.option("--line-stations", required=True)
@click.option("--output-stations", required=True)
@click.option("--output-segments", required=True)
def main(config_path, stations, line, line_stations, output_stations, output_segments):
    cfg = read_config(config_path)
    metro = cfg["metro"]
    crs = cfg["crs_metric"]

    stations = geopandas.read_file(stations).to_crs(crs)
    require_columns(stations, ["station_id", "geometry"], "metro_stations")
    stations["station_id"] = stations["station_id"].astype(str)
    order = pd.read_csv(line_stations, dtype={"station_id": str}).sort_values("seq")
    require_columns(order, ["station_id", "seq"], "metro_line_stations")
    geom = geopandas.read_file(line).to_crs(crs).geometry.union_all()
    # TODO: if the line is a MultiLineString, linemerge it first.

    stations = stations.merge(order, on="station_id", how="inner")
    stations["chainage_m"] = [geom.project(point) for point in stations.geometry]
    stations = stations.sort_values("seq")
    stations.to_parquet(output_stations)

    rows = []
    s = stations.reset_index(drop=True)
    for i in range(len(s) - 1):
        a, b = s.iloc[i], s.iloc[i + 1]
        piece = substring(geom, min(a.chainage_m, b.chainage_m), max(a.chainage_m, b.chainage_m))
        length_km = piece.length / 1000.0
        run = a.get("run_time_min_to_next")
        time_min = run if pd.notna(run) else length_km / metro["default_speed_kph"] * 60.0
        headway = a.get("headway_min")
        headway = headway if pd.notna(headway) else metro["default_headway_min"]
        segment_id = f"{a.station_id}-{b.station_id}"
        for direction, (u, v) in enumerate([(a.station_id, b.station_id), (b.station_id, a.station_id)]):
            rows.append(
                {
                    "segment_id": segment_id,
                    "direction": direction,
                    "from_station": u,
                    "to_station": v,
                    "length_km": length_km,
                    "time_min": time_min,
                    "headway_min": headway,
                    "capacity_per_train": metro["train_capacity"],
                    "geometry": piece,
                }
            )
    geopandas.GeoDataFrame(rows, crs=crs).to_parquet(output_segments)


if __name__ == "__main__":
    main()
