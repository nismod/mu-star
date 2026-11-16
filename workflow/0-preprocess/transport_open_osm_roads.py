"""Road network of one island from OpenStreetMap (open-data fallback for transport-data item 11).

Reads the OSM extract, keeps drivable highways (motorway ... service; `*_link` folded
into the parent class, `living_street` -> residential; footways, paths, cycleways, steps,
tracks, pedestrian, construction and proposed ways dropped, as are `access=private|no`
ways), cuts every way at each node shared with another kept way (so each edge runs
junction to junction), keeps the island's ways (island boxes in transport.open_osm:
Mauritius main island, Rodrigues; Agalega and St Brandon are excluded) and keeps only the
largest connected component (edges taken as undirected). Service ways are all kept (except
emergency-only access): they are cheap, and the component filter removes disconnected ones.

Output: layer "edges" of the GeoPackage (EPSG:4326), columns edge_id (`<way>_<index>`),
from_node, to_node (OSM node ids), road_class, oneway, speed_kph (numeric maxspeed, else NaN),
lanes, name, bridge, osm_id, geometry. A summary JSON is written next to it.

Data: (c) OpenStreetMap contributors, ODbL 1.0.
"""

import json
import time
from pathlib import Path

import click

from transport import open_osm

METRIC_CRS = {"mauritius": "EPSG:32740", "rodrigues": "EPSG:32741"}  # UTM zone of each island


@click.command()
@click.option("--pbf", required=True, help="OSM extract (.osm.pbf)")
@click.option("--island", type=click.Choice(["mauritius", "rodrigues"]), required=True)
@click.option("--output", required=True, help="roads.gpkg")
@click.option("--summary", required=True, help="summary JSON")
def main(pbf, island, output, summary):
    started = time.time()
    ways = open_osm.read_highway_ways(pbf)
    edges = open_osm.edges_from_ways(ways)
    in_osm = len(edges)
    edges = edges[edges["island"] == island].drop(columns="island")
    on_island = len(edges)
    edges, dropped = open_osm.largest_component(edges)
    stats = open_osm.road_summary(edges, METRIC_CRS[island])
    stats.update(
        {
            "island": island,
            "source": "OpenStreetMap extract (c) OpenStreetMap contributors, ODbL 1.0",
            "highway_ways_kept": len(ways),
            "edges_all_islands": in_osm,
            "edges_on_island": on_island,
            "dropped": dropped,
            "runtime_s": round(time.time() - started, 1),
        }
    )
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    edges.to_file(output, layer="edges", driver="GPKG")
    Path(summary).write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
