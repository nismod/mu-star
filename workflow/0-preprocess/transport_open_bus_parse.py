"""Parse the cached www.mauritius-buses.com pages into tidy tables (open-data fallback, items 12-14).

Input: the cache written by transport_open_bus_fetch.py (all-buslines.html,
line/<slug>.html, stop/<id>.html). Nothing is fetched here.

Outputs (CSV, in --output-dir):
    lines.csv         one row per line (metro included, is_metro flag): route_id (short name as
                      printed, e.g. 198, 101A, 24/24A), slug, operator, origin, via, destination,
                      operating_days, average_frequency_min, times_per_day, first_bus_min /
                      last_bus_min (weekday, minutes after midnight, earliest / latest over the
                      termini), span_min, headway_min, headway_rule, n_directions, flags
    line_stops.csv    route_id, direction (0/1 in page order), towards, stop_sequence (from 1),
                      stop_name, site_stop_id, cum_min (minutes from the first stop),
                      fare_stage_label (official stage, e.g. "29A"), fare_stage (its number),
                      official_locality (stage table name) - bus lines only
    metro_line_stations.csv  the same for the metro lines (M1, M2): station order and minutes
    stops.csv         site_stop_id, name, village, line_directions, lines, served_by_bus,
                      served_by_metro
    schedule.csv      route_id, days, from, first_bus_min, last_bus_min
    validation.json   counts and the checks below

Checks: number of lines against the index heading ("All N bus lines"); every line
has exactly two directions (others flagged); stop sequences contiguous from 1;
cumulative minutes non-decreasing (flagged per direction); fare-stage tables
aligned to the stop lists (row by row when minutes agree).

Headway rule: transport.open_bus.headway_min (average frequency; else the mean gap
between the listed weekday departures; else times per day over the weekday
operating span; else blank -> bus.default_headway_min).
"""

import json
import math
import re
from pathlib import Path

import click
import pandas as pd

from transport import open_bus


@click.command()
@click.option("--cache-dir", required=True)
@click.option("--output-dir", required=True)
def main(cache_dir, output_dir):
    cache, out = Path(cache_dir), Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    index_html = (cache / "all-buslines.html").read_text(errors="replace")
    index = open_bus.parse_line_index(index_html)
    heading = re.search(r"All (\d+) bus lines", index_html)

    lines, stops, fares, schedules, problems = [], [], [], [], []
    for row in index.itertuples():
        path = cache / open_bus.line_cache_name(row.slug)
        if not path.exists():
            problems.append({"route_id": row.route_id, "problem": "line page missing from cache"})
            continue
        page = open_bus.parse_line_page(path.read_text(errors="replace"))
        line = page["line"]
        route_id = line["route_id"] or row.route_id
        if route_id != row.route_id:
            problems.append({"route_id": row.route_id, "problem": f"page shows route {route_id!r}"})
            route_id = row.route_id
        avg = line["average_frequency_min"]
        if math.isnan(avg):
            avg = row.index_every_min
        per_day = line["times_per_day"]
        if math.isnan(per_day):
            per_day = row.index_times_per_day
        span = open_bus.operating_span_min(page["schedule"])
        headway, rule = open_bus.headway_min(avg, per_day, span, open_bus.departure_gap_min(page["departures"]))
        weekday = [s for s in page["schedule"] if "week" in s["days"].lower()] or page["schedule"]
        firsts = [s["first_bus_min"] for s in weekday if not math.isnan(s["first_bus_min"])]
        lasts = [s["last_bus_min"] for s in weekday if not math.isnan(s["last_bus_min"])]
        directions = sorted({s["direction"] for s in page["stops"]})
        flags = []
        if len(directions) != 2:
            flags.append(f"{len(directions)} directions")
        lines.append(
            {
                "route_id": route_id,
                "slug": row.slug,
                "title": line["title"] or row.title,
                "operator": line["operator"] or row.index_operator,
                "origin": line["origin"] or row.index_origin,
                "via": line["via"],
                "destination": line["destination"] or row.index_destination,
                "operating_days": line["operating_days"],
                "index_days_note": row.index_days_note,
                "frequency_text": line["frequency_text"],
                "average_frequency_min": avg,
                "times_per_day": per_day,
                "first_bus_min": min(firsts) if firsts else math.nan,
                "last_bus_min": max(lasts) if lasts else math.nan,
                "span_min": span,
                "headway_min": headway,
                "headway_rule": rule,
                "n_directions": len(directions),
                "timetable_scan_url": line["timetable_scan_url"],
                "is_metro": bool(open_bus.METRO_ROUTE.match(route_id)),
                "flags": "; ".join(flags),
            }
        )
        line_stops = pd.DataFrame(page["stops"])
        line_fares = pd.DataFrame(page["fares"])
        for direction in directions:
            part = line_stops[line_stops["direction"] == direction].copy()
            fare_part = line_fares[line_fares["direction"] == direction] if len(line_fares) else line_fares
            part["fare_stage_label"] = open_bus.align_fare_stages(part, fare_part).to_numpy()
            locality = pd.Series("", index=part.index, dtype=object)
            if len(fare_part) == len(part):
                locality = pd.Series(fare_part["locality"].to_numpy(), index=part.index)
            part["official_locality"] = locality.to_numpy()
            part["route_id"] = route_id
            stops.append(part)
            seq = part["stop_sequence"].tolist()
            if seq != list(range(1, len(seq) + 1)):
                flags.append(f"dir {direction}: sequence not contiguous")
            cum = part["cum_min"].to_numpy(dtype=float)
            if pd.isna(cum).any():
                flags.append(f"dir {direction}: {int(pd.isna(cum).sum())} stops without minutes")
            finite = cum[~pd.isna(cum)]
            if (finite[1:] < finite[:-1]).any():
                flags.append(f"dir {direction}: minutes decrease")
        for f in page["fares"]:
            fares.append({"route_id": route_id, **f})
        for s in page["schedule"]:
            schedules.append({"route_id": route_id, **s})
        lines[-1]["flags"] = "; ".join(flags)

    lines = pd.DataFrame(lines)
    stops_df = pd.concat(stops, ignore_index=True)
    stops_df["fare_stage"] = stops_df["fare_stage_label"].map(open_bus.fare_stage_number)
    cols = [
        "route_id", "direction", "towards", "stop_sequence", "stop_name", "site_stop_id", "cum_min",
        "fare_stage_label", "fare_stage", "official_locality",
    ]  # fmt: skip
    stops_df = stops_df[cols]
    metro_ids = set(lines.loc[lines["is_metro"], "route_id"])
    is_metro = stops_df["route_id"].isin(metro_ids)
    stops_df[~is_metro].to_csv(out / "line_stops.csv", index=False)
    stops_df[is_metro].drop(columns=["fare_stage_label", "fare_stage", "official_locality"]).to_csv(
        out / "metro_line_stations.csv", index=False
    )
    lines.to_csv(out / "lines.csv", index=False)
    pd.DataFrame(fares).to_csv(out / "fare_stages.csv", index=False)
    pd.DataFrame(schedules).to_csv(out / "schedule.csv", index=False)

    bus_ids = set(stops_df.loc[~is_metro, "site_stop_id"])
    metro_stop_ids = set(stops_df.loc[is_metro, "site_stop_id"])
    stop_rows = []
    for path in sorted((cache / "stop").glob("*.html"), key=lambda p: int(p.stem)):
        info = open_bus.parse_stop_page(path.read_text(errors="replace"))
        info["site_stop_id"] = info["site_stop_id"] or path.stem
        stop_rows.append(info)
    stop_table = pd.DataFrame(stop_rows)
    known = set(stop_table["site_stop_id"]) if len(stop_table) else set()
    for sid in sorted((bus_ids | metro_stop_ids) - known - {""}, key=int):
        names = stops_df.loc[stops_df["site_stop_id"] == sid, "stop_name"]
        stop_rows.append({"site_stop_id": sid, "name": names.iloc[0], "village": "", "line_directions": None})
        problems.append({"route_id": "", "problem": f"stop {sid} has no cached stop page"})
    stop_table = pd.DataFrame(stop_rows)
    stop_table["served_by_bus"] = stop_table["site_stop_id"].isin(bus_ids)
    stop_table["served_by_metro"] = stop_table["site_stop_id"].isin(metro_stop_ids)
    stop_table.to_csv(out / "stops.csv", index=False)

    bus_lines = lines[~lines["is_metro"]]
    bus_stops_df = stops_df[~is_metro]
    aligned = bus_stops_df["fare_stage_label"].astype(str).str.len() > 0
    validation = {
        "lines_in_index": len(index),
        "lines_announced_in_index_heading": int(heading.group(1)) if heading else None,
        "lines_parsed": len(lines),
        "metro_lines": sorted(metro_ids),
        "bus_lines": len(bus_lines),
        "bus_lines_with_two_directions": int((bus_lines["n_directions"] == 2).sum()),
        "bus_lines_flagged": bus_lines.loc[bus_lines["flags"] != "", ["route_id", "flags"]].to_dict("records"),
        "bus_directions": int(bus_stops_df.groupby(["route_id", "direction"]).ngroups),
        "bus_route_stop_rows": len(bus_stops_df),
        "bus_stops_distinct": len(bus_ids),
        "metro_stations_distinct": len(metro_stop_ids),
        "stop_pages_parsed": len(known),
        "stops_without_village": int((stop_table["village"].fillna("") == "").sum()),
        "fare_stage_rows_aligned_share": round(float(aligned.mean()), 4),
        "directions_fully_fare_staged": int(
            bus_stops_df.assign(a=aligned).groupby(["route_id", "direction"])["a"].all().sum()
        ),
        "headway_rules": bus_lines["headway_rule"].value_counts().to_dict(),
        "problems": problems,
    }
    (out / "validation.json").write_text(json.dumps(validation, indent=2, default=str))
    print(json.dumps({k: v for k, v in validation.items() if k not in ("problems", "bus_lines_flagged")}, indent=2))
    print(f"{len(validation['bus_lines_flagged'])} bus lines flagged, {len(problems)} problems")


if __name__ == "__main__":
    main()
