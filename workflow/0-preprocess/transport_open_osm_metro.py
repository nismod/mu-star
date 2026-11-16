"""Metro Express lines, stations and station order from OpenStreetMap (transport-data items 15-17).

Mauritius only. The OSM extract maps the Metro Express as two groups of route relations
(`route=light_rail`, one per direction): the main line (the group with the most stops) and the
Rose Hill - Reduit branch (Rose Hill, Ebene Cybercity, Mahatma Gandhi).

For each line, one direction's relation (the higher id: Port Louis to Curepipe, Rose Hill to
Mahatma Gandhi) provides:
  * the alignment: the member ways merged in order into one LineString;
  * the stop order, each stop (platform-side stop_position node) mapped to the metro
    station node (`railway=station|halt`, `station=light_rail`) with a compatible name nearby,
    consecutive repeats merged.
The Place d'Armes terminus (opened after the relation was mapped) is in OSM only as a station
node plus `disused=yes` / yard track beyond Port Louis Victoria: the main line is extended
along that track to the station, and the station is added to the order. Nothing is invented:
if the track cannot be traced to the station, the line stops where the relation does and
Place d'Armes is not added.

Output (under --output-dir): metro_stations.gpkg (station_id = slug of the name, name),
metro_lines.gpkg (line_id main|branch, LineString), metro_line_stations.csv (line_id,
station_id, seq from 1). No run times (the optional column is left out): the consumer then uses
metro.default_speed_kph. Stations on both lines (Rose Hill Central) appear in both.

Data: (c) OpenStreetMap contributors, ODbL 1.0.
"""

import json
from pathlib import Path

import click
import geopandas
import pandas as pd
from shapely.geometry import LineString, Point

from transport import open_osm


def extend_line(chain_nodes, coords, track_ways, station):
    """Extend a merged node chain at the end nearer `station` along any mapped track; None if impossible."""
    head, tail = chain_nodes[0], chain_nodes[-1]
    near_tail = open_osm.approx_metres(coords[tail], station["xy"]) <= open_osm.approx_metres(
        coords[head], station["xy"]
    )
    start = tail if near_tail else head
    path = open_osm.extension_path(track_ways, coords, start, station["xy"])
    if path is None:
        return None
    return (chain_nodes + path[1:]) if near_tail else (path[:0:-1] + chain_nodes)


@click.command()
@click.option("--pbf", required=True)
@click.option("--output-dir", required=True)
@click.option("--terminus-station", "terminus", default="Place d'Armes", show_default=True)
def main(pbf, output_dir, terminus):
    data = open_osm.read_metro(pbf)
    ways, coords, routes = data["ways"], data["coords"], data["routes"]
    stations = [{**s, "station_id": open_osm.slugify(s["name"])} for s in data["stations"]]
    stop_nodes = data["stop_nodes"]

    # Route groups: a pair of opposite directions has the same set of end names.
    groups: dict = {}
    for route in routes:
        key = frozenset([route["tags"].get("from", ""), route["tags"].get("to", "")])
        groups.setdefault(key, []).append(route)
    ranked = sorted(groups.values(), key=lambda g: -len(g[0]["stops"]))
    if len(ranked) < 2:
        raise click.ClickException(f"Expected a main line and a branch, found {len(ranked)} route group(s)")
    chosen = {"main": max(ranked[0], key=lambda r: r["id"]), "branch": max(ranked[1], key=lambda r: r["id"])}

    all_track = [w["nodes"] for w in ways.values()]
    line_rows, order_rows, used = [], [], {}
    notes = {}
    for line_id, route in chosen.items():
        member = [
            ways[w] for w in route["ways"] if w in ways and ways[w]["tags"].get("service") not in ("crossover", "yard")
        ]
        ids = open_osm.merge_way_chain_ids([w["nodes"] for w in member], coords)
        chain = [coords[n] for n in ids]
        stop_stations = []
        for node in route["stops"]:
            stop = stop_nodes.get(node)
            if stop is None:
                continue
            station = open_osm.match_station(stop["name"], stop["xy"], stations)
            if station is None:
                raise click.ClickException(f"No station for stop {stop['name']!r} (node {node}) on {route['name']}")
            stop_stations.append(station)
        order = open_osm.line_station_order(stop_stations)

        if line_id == "main":
            extra = next((s for s in stations if open_osm.norm_name(s["name"]) == open_osm.norm_name(terminus)), None)
            if extra is not None and extra["station_id"] not in {s["station_id"] for s in order}:
                longer = extend_line(ids, coords, all_track, extra)
                if longer is None:
                    notes["terminus"] = f"{terminus}: track to the station not found, not added"
                else:
                    chain = [coords[n] for n in longer]
                    # place the station at whichever end of the stop order is nearer its track end
                    first_stop = open_osm.approx_metres(order[0]["xy"], extra["xy"])
                    last_stop = open_osm.approx_metres(order[-1]["xy"], extra["xy"])
                    order = [extra, *order] if first_stop <= last_stop else [*order, extra]
                    notes["terminus"] = f"{terminus} added from disused/yard track ({len(longer) - len(ids)} nodes)"
        line_rows.append(
            {"line_id": line_id, "osm_relation": route["id"], "name": route["name"], "geometry": LineString(chain)}
        )
        for seq, station in enumerate(order, start=1):
            order_rows.append({"line_id": line_id, "station_id": station["station_id"], "seq": seq})
            used[station["station_id"]] = station

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    station_rows = [
        {"station_id": s["station_id"], "name": s["name"], "osm_id": s["osm_id"], "geometry": Point(s["xy"])}
        for s in used.values()
    ]
    geopandas.GeoDataFrame(station_rows, crs=4326).to_file(out / "metro_stations.gpkg", driver="GPKG")
    geopandas.GeoDataFrame(line_rows, crs=4326).to_file(out / "metro_lines.gpkg", driver="GPKG")
    pd.DataFrame(order_rows).to_csv(out / "metro_line_stations.csv", index=False)
    summary = {
        "relations": {k: {"id": v["id"], "name": v["name"]} for k, v in chosen.items()},
        "stations": len(used),
        "stations_per_line": pd.DataFrame(order_rows).groupby("line_id").size().to_dict(),
        "notes": notes,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
