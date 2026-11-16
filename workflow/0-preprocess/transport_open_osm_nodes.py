"""Airports and ports for the inter-island network from OpenStreetMap (transport-data item 20).

Airports: every `aeroway=aerodrome` feature in the extract (nodes and ways; a way is reduced
to its centroid). node_id is the IATA code, else the ICAO code, else a slug of the name. In the
Mauritius extract these are Sir Seewoosagur Ramgoolam International (MRU), Sir Gaetan Duval,
Rodrigues (RRG) and the Agalega airstrip (FIMA). St Brandon has no aerodrome in OSM.

Ports: OSM maps the ports poorly. Features that mark a port (`harbour=yes`, `industrial=port`,
`landuse=port`, `seamark:type=harbour` except marinas, and `amenity=ferry_terminal`) are read,
but only named ones are kept, and none of them is a complete list of the commercial and passenger
ports: Port Louis is a single fishing-category harbour node, Port Mathurin an industrial=port area, and
the Agalega and St Brandon landings are not mapped as ports at all. The result is therefore the
documented fallback list MANUAL_PORTS (transport.open_osm) -- Port Louis, Port Mathurin and Port
Saint James (Agalega) -- each located from the named OSM feature it refers to (node or way id in the
list), plus any additional named port features found by the tags above that are not within 2 km
of a listed port (named ferry terminals, such as the Ile aux Cerfs boats, are reported but marked
as `ferry_terminal` in the `osm_tags` column rather than added as ports; see --include-ferry).
Mahebourg has no port feature; St Brandon has no feature at all, so neither is included.

Port node_ids (PLU, PMA, AGA-PSJ) are local ids, not UN/LOCODEs.

Output: layer of nodes.gpkg (EPSG:4326, Point): node_id, name, island, kind (airport|port),
osm_id, source (osm | manual-fallback), plus notes.

Data: (c) OpenStreetMap contributors, ODbL 1.0.
"""

import json
from pathlib import Path

import click
import pandas as pd

from transport import open_osm

PORT_TAGS = (
    ("harbour", "yes"),
    ("industrial", "port"),
    ("landuse", "port"),
    ("seamark:type", "harbour"),
    ("amenity", "ferry_terminal"),
)


def select(tags):
    if tags.get("aeroway") == "aerodrome":
        return True
    if tags.get("leisure") == "marina":
        return False
    return any(tags.get(k) == v for k, v in PORT_TAGS)


@click.command()
@click.option("--pbf", required=True)
@click.option("--output", required=True, help="nodes.gpkg")
@click.option("--summary", required=True)
@click.option("--include-ferry/--no-include-ferry", default=False, help="also add named ferry terminals as ports")
def main(pbf, output, summary, include_ferry):
    manual_refs = {tuple(m["osm_ref"]) for m in open_osm.MANUAL_PORTS}
    features = open_osm.read_features(pbf, select, include=manual_refs)
    by_ref = {(f["osm_type"], f["osm_id"]): f for f in features}

    rows = []
    for f in features:
        if f["tags"].get("aeroway") == "aerodrome":
            island = open_osm.island_of(*f["xy"])
            rows.append(
                {
                    "node_id": open_osm.aerodrome_id(f["tags"], f["osm_id"]),
                    "name": f["tags"].get("name:en") or f["tags"].get("name"),
                    "island": island,
                    "kind": "airport",
                    "osm_id": f"{f['osm_type']}{f['osm_id']}",
                    "source": "osm",
                    "note": f"aerodrome:type={f['tags'].get('aerodrome:type', '')}; "
                    f"operator={f['tags'].get('operator', '')}",
                    "xy": f["xy"],
                }
            )
    ports = []
    for m in open_osm.MANUAL_PORTS:
        f = by_ref.get(tuple(m["osm_ref"]))
        if f is None:
            raise click.ClickException(f"OSM feature {m['osm_ref']} for {m['name']} not found in the extract")
        ports.append(
            {
                "node_id": m["node_id"],
                "name": m["name"],
                "island": m["island"],
                "kind": "port",
                "osm_id": f"{f['osm_type']}{f['osm_id']}",
                "source": "manual-fallback",
                "note": m["note"],
                "xy": f["xy"],
            }
        )
    rows += ports

    named_ports = []
    for f in features:
        tags = f["tags"]
        if tags.get("aeroway") or not tags.get("name") or (f["osm_type"], f["osm_id"]) in manual_refs:
            continue
        near_listed = any(open_osm.approx_metres(f["xy"], p["xy"]) < 2000 for p in ports)
        named_ports.append(
            {"osm_id": f"{f['osm_type']}{f['osm_id']}", "name": tags["name"], "near_listed_port": near_listed}
        )
        if include_ferry and not near_listed and tags.get("amenity") == "ferry_terminal":
            rows.append(
                {
                    "node_id": f"osm-{f['osm_type']}{f['osm_id']}",
                    "name": tags["name"],
                    "island": open_osm.island_of(*f["xy"]),
                    "kind": "port",
                    "osm_id": f"{f['osm_type']}{f['osm_id']}",
                    "source": "osm",
                    "note": "named ferry terminal",
                    "xy": f["xy"],
                }
            )

    nodes = open_osm.point_frame(rows)
    nodes = nodes[nodes["island"].notna()].reset_index(drop=True)
    if nodes["node_id"].duplicated().any():
        raise click.ClickException("duplicate node_id")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    nodes.to_file(output, layer="nodes", driver="GPKG")
    stats = {
        "nodes": nodes.drop(columns="geometry").to_dict("records"),
        "named_port_like_features_seen": named_ports,
    }
    Path(summary).write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print(pd.DataFrame(stats["nodes"]).to_string())
    print(f"named port-like OSM features seen (not all added): {named_ports}")


if __name__ == "__main__":
    main()
