"""Write the bus workflow inputs (transport-data items 12-14) from the parsed and located tables.

Inputs: lines.csv and line_stops.csv (transport_open_bus_parse.py) and the located
stops (transport_open_bus_locate.py). Metro lines (M1, M2) are left out.

Outputs (exact schemas of docs/src/transport-data.md, items 12-14):
    bus_stops.gpkg        stop_id (site stop id), name, geometry (Point, EPSG:4326), plus
                          village, location_quality (1 best .. 4), location_source
    bus_routes.gpkg       route_id (short name as printed: 198, 101A, 24/24A), name
                          ("origin - destination"), operator, headway_min (rule in
                          transport.open_bus.headway_min; blank -> bus.default_headway_min),
                          geometry None
    bus_route_stops.csv   route_id, direction (0/1), stop_sequence (1..n, renumbered over
                          the located stops), stop_id, time_to_next_min (difference of the
                          cumulative minutes, recomputed across stops that could not be
                          located; blank on the last stop), fare_stage (official NLTA stage
                          number; "29A" counts as 29; blank where the stage table could not
                          be aligned to the stop list)
    --dropped CSV         routes / directions dropped (fewer than two located stops) and
                          stops removed from routes because they could not be located
    --summary JSON        counts

Sources: www.mauritius-buses.com (from the NLTA timetables, "Data provided without
warranty"); stop locations (c) OpenStreetMap contributors, ODbL 1.0.
"""

import json
import math

import click
import geopandas
import pandas as pd

from transport import open_bus


@click.command()
@click.option("--lines", "lines_csv", required=True)
@click.option("--line-stops", "line_stops_csv", required=True)
@click.option("--located", "located_csv", required=True)
@click.option("--output-stops", required=True, help="bus_stops.gpkg")
@click.option("--output-routes", required=True, help="bus_routes.gpkg")
@click.option("--output-route-stops", required=True, help="bus_route_stops.csv")
@click.option("--dropped", required=True, help="CSV of dropped routes and stops")
@click.option("--summary", required=True, help="summary JSON")
def main(lines_csv, line_stops_csv, located_csv, output_stops, output_routes, output_route_stops, dropped, summary):
    lines = pd.read_csv(lines_csv, dtype={"route_id": str})
    lines = lines[~lines["is_metro"].astype(bool)]
    seq = pd.read_csv(line_stops_csv, dtype={"route_id": str, "site_stop_id": str})
    located = pd.read_csv(located_csv, dtype={"site_stop_id": str})
    ok = set(located["site_stop_id"])

    rows, dropped_rows = [], []
    for (route_id, direction), part in seq.sort_values("stop_sequence").groupby(["route_id", "direction"]):
        keep = part["site_stop_id"].isin(ok).tolist()
        for r in part[~part["site_stop_id"].isin(ok)].itertuples():
            dropped_rows.append({"route_id": route_id, "direction": direction, "stop_id": r.site_stop_id,
                                 "name": r.stop_name, "reason": "stop not located"})  # fmt: skip
        if sum(keep) < 2:
            dropped_rows.append({"route_id": route_id, "direction": direction, "stop_id": "", "name": "",
                                 "reason": f"direction dropped: {sum(keep)} located stops"})  # fmt: skip
            continue
        times = open_bus.recompute_times(part["cum_min"].astype(float).tolist(), keep)
        kept = part[keep]
        for n, (r, t) in enumerate(zip(kept.itertuples(), times, strict=True), start=1):
            rows.append(
                {
                    "route_id": route_id,
                    "direction": int(direction),
                    "stop_sequence": n,
                    "stop_id": r.site_stop_id,
                    "time_to_next_min": t,
                    "fare_stage": r.fare_stage,
                }
            )
    route_stops = pd.DataFrame(rows)
    route_stops["fare_stage"] = route_stops["fare_stage"].astype("Int64")
    served = set(route_stops["route_id"])
    for r in lines[~lines["route_id"].isin(served)].itertuples():
        dropped_rows.append({"route_id": r.route_id, "direction": "", "stop_id": "", "name": "",
                             "reason": "route dropped: no direction with two located stops"})  # fmt: skip
    route_stops.to_csv(output_route_stops, index=False)

    routes = lines[lines["route_id"].isin(served)]
    routes = geopandas.GeoDataFrame(
        {
            "route_id": routes["route_id"],
            "name": routes["origin"].fillna("") + " - " + routes["destination"].fillna(""),
            "operator": routes["operator"],
            "headway_min": routes["headway_min"].astype(float),
            "headway_rule": routes["headway_rule"],
            "operating_days": routes["operating_days"],
        },
        geometry=[None] * len(routes),
        crs=4326,
    )
    routes.to_file(output_routes, driver="GPKG")

    used = located[located["site_stop_id"].isin(set(route_stops["stop_id"]))]
    stops = geopandas.GeoDataFrame(
        {
            "stop_id": used["site_stop_id"],
            "name": used["name"],
            "village": used["village"],
            "location_quality": used["location_quality"].astype(int),
            "location_source": used["location_source"],
        },
        geometry=geopandas.points_from_xy(used["lon"], used["lat"]),
        crs=4326,
    )
    stops.to_file(output_stops, driver="GPKG")

    dropped_df = pd.DataFrame(dropped_rows, columns=["route_id", "direction", "stop_id", "name", "reason"])
    dropped_df.to_csv(dropped, index=False)
    staged = route_stops["fare_stage"].notna()
    out = {
        "routes": len(routes),
        "routes_dropped": int(dropped_df["reason"].str.startswith("route dropped").sum()),
        "directions": int(route_stops.groupby(["route_id", "direction"]).ngroups),
        "directions_dropped": int(dropped_df["reason"].str.startswith("direction dropped").sum()),
        "route_stop_rows": len(route_stops),
        "route_stop_rows_removed_not_located": int((dropped_df["reason"] == "stop not located").sum()),
        "stops": len(stops),
        "stops_by_quality": stops["location_quality"].value_counts().sort_index().to_dict(),
        "fare_stage_share": round(float(staged.mean()), 3),
        "routes_without_headway": int(routes["headway_min"].isna().sum()),
        "time_to_next_min_missing_non_last": int(
            route_stops.groupby(["route_id", "direction"])["time_to_next_min"]
            .apply(lambda s: s.iloc[:-1].isna().sum())
            .sum()
        ),
        "mean_time_to_next_min": round(float(route_stops["time_to_next_min"].mean()), 2),
    }
    out = {k: (v if not (isinstance(v, float) and math.isnan(v)) else None) for k, v in out.items()}
    with open(summary, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
