"""Open-data fallback for the transport inputs: build them from OpenStreetMap.

OpenStreetMap data are (c) OpenStreetMap contributors, licensed ODbL 1.0
(https://www.openstreetmap.org/copyright). Everything derived from the extract
(roads, metro, schools, airports and ports) is a Derived Database under the
ODbL and has to carry that attribution.

The functions here are pure (tags, node lists and coordinates in, tables
out) so they can be tested without a PBF file. The readers at the bottom use
pyosmium (`import osmium`) and are the only code that touches a file.

Sections:
    islands      which island a coordinate belongs to
    roads        highway ways -> noded, classified edges (transport-data item 11)
    metro        light-rail route relations -> lines, stations, station order (items 15-17)
    schools      classifying and de-duplicating schools (item 10)
    nodes        airports and ports (item 20)
    readers      pyosmium readers
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from difflib import SequenceMatcher

import geopandas
import networkx as nx
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import LineString, Point, Polygon

# --- islands ------------------------------------------------------------------

# (min_lon, min_lat, max_lon, max_lat) boxes, generous around each island's
# coast. Agalega (56.6E, 10.4S) and St Brandon (59.6E, 16.5S) are far away.
ISLAND_BOXES = {
    "mauritius": (57.2, -20.6, 57.9, -19.9),
    "rodrigues": (63.2, -19.85, 63.65, -19.6),
    "agalega": (56.4, -10.6, 56.8, -10.2),
    "st-brandon": (59.3, -17.0, 59.9, -16.2),
}


def island_of(lon: float, lat: float) -> str | None:
    """Island whose box contains the coordinate, or None (e.g. open sea, other islands)."""
    for island, (x0, y0, x1, y1) in ISLAND_BOXES.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return island
    return None


# --- roads --------------------------------------------------------------------

ROAD_CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service"]

# OSM highway value -> schema road_class. Anything not listed is dropped
# (footway, path, cycleway, steps, track, pedestrian, bridleway, construction,
# proposed, busway, raceway, ...).
HIGHWAY_TO_CLASS = {
    "motorway": "motorway",
    "motorway_link": "motorway",
    "trunk": "trunk",
    "trunk_link": "trunk",
    "primary": "primary",
    "primary_link": "primary",
    "secondary": "secondary",
    "secondary_link": "secondary",
    "tertiary": "tertiary",
    "tertiary_link": "tertiary",
    "unclassified": "unclassified",
    "road": "unclassified",  # OSM: "unknown road type, to be surveyed"
    "residential": "residential",
    "living_street": "residential",
    "service": "service",
}

# service=* values that are not drivable network (emergency-only gates).
DROPPED_SERVICE_TYPES = {"emergency_access"}

ACCESS_BLOCKING = {"no", "private"}
ACCESS_OVERRIDING = {"yes", "designated", "permissive", "destination", "customers"}


def road_class(tags: dict) -> str | None:
    """Schema road class of a way, or None if the way is not part of the drivable network.

    Drops: non-motor highway values (see HIGHWAY_TO_CLASS), area features,
    `access=private|no` (unless `motor_vehicle` or `vehicle` explicitly allows
    motor traffic), `motor_vehicle=no|private`, and emergency-only service ways.
    Service ways are otherwise all kept (driveways and parking aisles too: cheap,
    and the largest connected component drops the ones that are not connected).
    """
    cls = HIGHWAY_TO_CLASS.get(tags.get("highway", ""))
    if cls is None:
        return None
    if tags.get("area") == "yes":
        return None
    if cls == "service" and tags.get("service") in DROPPED_SERVICE_TYPES:
        return None
    motor = tags.get("motor_vehicle")
    if motor in ACCESS_BLOCKING:
        return None
    if motor not in ACCESS_OVERRIDING and tags.get("vehicle") not in ACCESS_OVERRIDING:
        if tags.get("access") in ACCESS_BLOCKING or tags.get("vehicle") in ACCESS_BLOCKING:
            return None
    return cls


def oneway_direction(tags: dict, cls: str | None = None) -> str:
    """Direction of travel relative to the way's node order: `forward`, `reverse` or `both`.

    `oneway=yes|true|1` is forward and `oneway=-1|reverse` is reverse. Without an
    explicit oneway tag, roundabouts (`junction=roundabout|circular`) and motorways
    (and their links) are one-way forward. `oneway=no` overrides these defaults.
    Anything else (including `reversible` and `alternating`) is two-way.
    """
    value = str(tags.get("oneway", "")).strip().lower()
    if value in ("yes", "true", "1"):
        return "forward"
    if value in ("-1", "reverse"):
        return "reverse"
    if value in ("no", "false", "0"):
        return "both"
    if value:  # reversible, alternating, unknown
        return "both"
    if tags.get("junction") in ("roundabout", "circular"):
        return "forward"
    if tags.get("highway") in ("motorway", "motorway_link") or cls == "motorway":
        return "forward"
    return "both"


def parse_speed_kph(value) -> float:
    """Numeric `maxspeed` in km/h ("50", "50 km/h", "30 mph"), else NaN (e.g. "signals", "none")."""
    if value is None:
        return math.nan
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(km/h|kmh|kph|mph)?\s*", str(value).lower())
    if not match:
        return math.nan
    speed = float(match.group(1))
    if match.group(2) == "mph":
        speed *= 1.609344
    return speed if speed > 0 else math.nan


def parse_lanes(value):
    """Integer `lanes`, else None (pandas nullable Int on output)."""
    try:
        lanes = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return lanes if lanes > 0 else None


def is_bridge(tags: dict) -> bool:
    """True where `bridge` is set to anything but `no`."""
    return tags.get("bridge", "no") not in ("no", "")


def split_way(node_ids: list, junctions: set) -> list[tuple[int, int]]:
    """Index ranges (start, stop inclusive) that cut a way at every junction node.

    `node_ids` are the way's node ids in order. A cut is made at the first and last
    node and at every interior node that is in `junctions` (a node shared with another
    kept way, or visited twice by this way). Consecutive duplicates are not expected.
    """
    cuts = [0] + [i for i in range(1, len(node_ids) - 1) if node_ids[i] in junctions] + [len(node_ids) - 1]
    return [(a, b) for a, b in itertools.pairwise(cuts) if b > a]


def junction_nodes(ways: list[dict]) -> set:
    """Nodes used by two or more node slots of the kept ways (way ends are cut anyway)."""
    counts: dict = {}
    for way in ways:
        for node in way["nodes"]:
            counts[node[0]] = counts.get(node[0], 0) + 1
    return {node for node, n in counts.items() if n >= 2}


EDGE_COLUMNS = [
    "edge_id",
    "from_node",
    "to_node",
    "road_class",
    "oneway",
    "speed_kph",
    "lanes",
    "name",
    "bridge",
    "osm_id",
    "island",
    "geometry",
]


def edges_from_ways(ways: list[dict]) -> geopandas.GeoDataFrame:
    """Noded road edges (EPSG:4326) from OSM ways.

    Each way is a dict `{"id": int, "tags": dict, "nodes": [(node_id, lon, lat), ...]}`.
    Ways without a road class are dropped. The rest are cut at every node shared by two
    or more kept ways, at self-intersections, and at the way ends, so every edge runs
    junction to junction. Edge ids are `<way id>_<index>`, the index counting segments
    along the way as mapped. `oneway=-1` ways are reversed, so `oneway` always means
    "from_node to to_node". Edges whose two ends are the same node (closed loops with no
    junction) are dropped, as the road network cannot use them.
    """
    kept = []
    for way in ways:
        cls = road_class(way["tags"])
        if cls is not None and len(way["nodes"]) >= 2:
            kept.append({**way, "class": cls})
    junctions = junction_nodes(kept)

    rows = []
    for way in kept:
        nodes = way["nodes"]
        tags = way["tags"]
        direction = oneway_direction(tags, way["class"])
        speed = parse_speed_kph(tags.get("maxspeed"))
        lanes = parse_lanes(tags.get("lanes"))
        bridge = is_bridge(tags)
        for index, (a, b) in enumerate(split_way([n[0] for n in nodes], junctions)):
            piece = nodes[a : b + 1]
            if direction == "reverse":
                piece = piece[::-1]
            if piece[0][0] == piece[-1][0]:
                continue
            coords = [(lon, lat) for _, lon, lat in piece]
            rows.append(
                {
                    "edge_id": f"{way['id']}_{index}",
                    "from_node": str(piece[0][0]),
                    "to_node": str(piece[-1][0]),
                    "road_class": way["class"],
                    "oneway": direction != "both",
                    "speed_kph": speed,
                    "lanes": lanes,
                    "name": tags.get("name"),
                    "bridge": bridge,
                    "osm_id": way["id"],
                    "island": island_of(*coords[0]),
                    "geometry": LineString(coords),
                }
            )
    edges = geopandas.GeoDataFrame(rows, columns=EDGE_COLUMNS, geometry="geometry", crs=4326)
    edges["lanes"] = edges["lanes"].astype("Int64")
    return edges


def component_labels(from_node, to_node) -> np.ndarray:
    """Connected-component label of every edge, treating edges as undirected."""
    ids, inverse = np.unique(np.concatenate([np.asarray(from_node), np.asarray(to_node)]), return_inverse=True)
    n_edges = len(from_node)
    a, b = inverse[:n_edges], inverse[n_edges:]
    graph = coo_matrix((np.ones(n_edges), (a, b)), shape=(len(ids), len(ids)))
    _, labels = connected_components(graph, directed=False)
    return labels[a]


def largest_component(edges: geopandas.GeoDataFrame):
    """Edges of the largest (by edge count) undirected connected component, and a summary of what was dropped."""
    if edges.empty:
        return edges, {"components": 0, "edges_dropped": 0}
    labels = component_labels(edges["from_node"].to_numpy(), edges["to_node"].to_numpy())
    sizes = np.bincount(labels)
    keep = labels == sizes.argmax()
    dropped = {
        "components": len(sizes),
        "components_dropped": int(len(sizes) - 1),
        "edges_dropped": int((~keep).sum()),
        "largest_component_edges": int(sizes.max()),
    }
    return edges[keep].copy(), dropped


def road_summary(edges: geopandas.GeoDataFrame, metric_crs) -> dict:
    """Edge counts and km by class, node count and total km."""
    km = edges.to_crs(metric_crs).geometry.length / 1000.0
    by_class = edges.assign(km=km).groupby("road_class").agg(edges=("edge_id", "size"), km=("km", "sum"))
    by_class = by_class.reindex([c for c in ROAD_CLASSES if c in by_class.index])
    return {
        "edges": len(edges),
        "nodes": len(set(edges["from_node"]) | set(edges["to_node"])),
        "total_km": round(float(km.sum()), 1),
        "oneway_edges": int(edges["oneway"].sum()),
        "edges_by_class": {c: int(r.edges) for c, r in by_class.iterrows()},
        "km_by_class": {c: round(float(r.km), 1) for c, r in by_class.iterrows()},
        "metric_crs": str(metric_crs),
    }


# --- metro --------------------------------------------------------------------


def slugify(text: str) -> str:
    """Lower-case ASCII slug: "Ébène Cybercity" -> "ebene-cybercity"."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def merge_way_chain(way_nodes: list[list], coords: dict) -> list[tuple[float, float]]:
    """Coordinates of the merged chain (see `merge_way_chain_ids`)."""
    return [coords[n] for n in merge_way_chain_ids(way_nodes, coords)]


def merge_way_chain_ids(way_nodes: list[list], coords: dict) -> list:
    """Merge ways given in route order into one list of node ids.

    `way_nodes` holds the node ids of each member way (relation order, any direction
    per way); `coords` maps node id to (lon, lat). Each way is oriented so that it starts
    where the previous one ended. The first way is oriented to join the second. If two
    consecutive ways share no node, the gap is bridged with a straight segment.
    """
    ways = [list(w) for w in way_nodes if len(w) >= 2]
    if not ways:
        return []
    if len(ways) > 1:
        nxt = {ways[1][0], ways[1][-1]}
        if ways[0][0] in nxt and ways[0][-1] not in nxt:
            ways[0].reverse()
        elif ways[0][-1] not in nxt:
            # no shared node: orient the first way so its end is nearer the second way
            def gap(w, v):
                return math.dist(coords[w[-1]], coords[v[0]])

            if min(gap(ways[0][::-1], ways[1]), gap(ways[0][::-1], ways[1][::-1])) < min(
                gap(ways[0], ways[1]), gap(ways[0], ways[1][::-1])
            ):
                ways[0].reverse()
    chain = list(ways[0])
    for way in ways[1:]:
        end = chain[-1]
        if way[0] == end:
            chain.extend(way[1:])
        elif way[-1] == end:
            chain.extend(way[::-1][1:])
        else:
            forward = math.dist(coords[end], coords[way[0]]) <= math.dist(coords[end], coords[way[-1]])
            chain.extend(way if forward else way[::-1])
    return chain


def approx_metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance in metres between two (lon, lat) points (fine below ~100 km)."""
    kx = 111320.0 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * 110540.0)


def extension_path(ways: list[list], coords: dict, start_node, station_xy, max_station_m: float = 150.0):
    """Node path along track ways from `start_node` to the track node nearest a station.

    Used for a terminus that is mapped (often `disused=yes` or as yard track) but not in
    the route relation. Returns the node id list (including `start_node`), or None if no
    track node is within `max_station_m` of the station or the nodes are not connected.
    """
    graph = nx.Graph()
    for way in ways:
        for u, v in itertools.pairwise(way):
            graph.add_edge(u, v, weight=approx_metres(coords[u], coords[v]))
    if start_node not in graph:
        return None
    target = min(graph.nodes, key=lambda n: approx_metres(coords[n], station_xy))
    if approx_metres(coords[target], station_xy) > max_station_m:
        return None
    try:
        return nx.shortest_path(graph, start_node, target, weight="weight")
    except nx.NetworkXNoPath:
        return None


def norm_name(text: str) -> str:
    """Lower-case ASCII letters and digits only, for comparing names."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text)


def match_station(stop_name: str, stop_xy, stations: list[dict], max_m: float = 250.0):
    """Station dict for a route stop: nearest station within `max_m` whose name is compatible.

    Names are compatible when one normalised name starts with the other ("Rose Hill" and
    "Rose Hill Central"). If no compatible station is in range, the nearest station within
    100 m is used regardless of name. Returns None if there is none.
    """
    ranked = sorted(stations, key=lambda s: approx_metres(s["xy"], stop_xy))
    key = norm_name(stop_name)
    for station in ranked:
        if approx_metres(station["xy"], stop_xy) > max_m:
            break
        other = norm_name(station["name"])
        if key and (other.startswith(key) or key.startswith(other)):
            return station
    if ranked and approx_metres(ranked[0]["xy"], stop_xy) <= 100.0:
        return ranked[0]
    return None


def line_station_order(stop_stations: list[dict]) -> list[dict]:
    """Stations in order, with consecutive repeats (platform nodes of one station) merged."""
    order: list[dict] = []
    for station in stop_stations:
        if not order or order[-1]["station_id"] != station["station_id"]:
            order.append(station)
    return order


# --- schools ------------------------------------------------------------------

# Tag and name rules, in this order: strong exclusions (pre-school), isced:level,
# name keywords, weak exclusions (tertiary and vocational), then the default.
PRESCHOOL_WORDS = (
    "preschool",
    "pre-school",
    "pre school",
    "pre-primary",
    "preprimary",
    "maternelle",
    "nursery",
    "kindergarten",
    "day care",
    "daycare",
    "tiny tots",
    "early years",
    "playschool",
    "creche",
    "crèche",
    "little darlings",
)
TERTIARY_WORDS = (
    "institute",
    "institut ",
    "university",
    "universit",
    "training",
    "vocational",
    "mitd",
    "ivtb",
    "vti",
    "polytechnic",
    "conservatoire",
    "supérieures",
    "superieures",
    "sunday",
    "makeup",
    "fashion",
    "school of",
)
SECONDARY_PATTERNS = (
    r"\bcollege\b",
    r"\bcollège\b",
    r"\bsecondary\b",
    r"\bsss\b",
    r"\blyc[ée]e\b",
    r"\bgrammar\b",
    r"\bhigh school\b",
    r"\bconvent\b",
    r"\bacademy\b",
)
PRIMARY_PATTERNS = (
    r"\bprimary\b",
    r"\bprimaire\b",
    r"\bgovernment school\b",
    r"\b(gov|govt|gouv|gouvernement|goverment|governement|govenment|government)\b.*\b(school|ecole|école)\b",
    r"\brca\b",
    r"\bgs\b",
    r"\belementary\b",
    r"\bécole primaire\b",
)


def isced_levels(value) -> set[int]:
    """Digits in an `isced:level` value ("1", "2;3", "0-1")."""
    return {int(d) for d in re.findall(r"\d", str(value))} if value else set()


def classify_school(tags: dict) -> tuple[str | None, str]:
    """Level and the evidence for it: (level, how).

    `level` is `primary`, `secondary`, or None for features that are not primary or
    secondary schools (pre-schools, tertiary, vocational and tuition centres). `how` is
    one of `isced`, `name`, `excluded` or `default`. Ambiguous or unrecognised features
    return `("primary", "default")`: callers flag them rather than guess silently.

    1. pre-school words in the name -> excluded;
    2. `isced:level` (digits): only level 1 -> primary; only 2-3 -> secondary; only 0 or
       only 4+ -> excluded; a mix -> falls through to the name;
    3. name keywords: primary and secondary patterns (a name matching both is ambiguous);
    4. tertiary / vocational words -> excluded;
    5. otherwise the default.
    """
    name = str(tags.get("name") or "").lower()
    if any(w in name for w in PRESCHOOL_WORDS):
        return None, "excluded"
    levels = isced_levels(tags.get("isced:level"))
    if levels:
        if levels == {1}:
            return "primary", "isced"
        if levels <= {2, 3} and levels:
            return "secondary", "isced"
        if levels <= {0}:
            return None, "excluded"
        if min(levels) >= 4:
            return None, "excluded"
    primary = any(re.search(p, name) for p in PRIMARY_PATTERNS)
    secondary = any(re.search(p, name) for p in SECONDARY_PATTERNS)
    if secondary and not primary:
        return "secondary", "name"
    if primary and not secondary:
        return "primary", "name"
    if not primary and not secondary and any(w in name for w in TERTIARY_WORDS):
        return None, "excluded"
    return "primary", "default"


SCHOOL_STOPWORDS = {
    "government",
    "govt",
    "gov",
    "governement",
    "goverment",
    "govenment",
    "gouvernement",
    "school",
    "ecole",
    "primary",
    "rca",
    "gs",
    "the",
    "de",
    "du",
    "la",
    "le",
    "des",
    "d",
    "l",
    "st",
    "saint",
    "sir",
    "dr",
    "aided",
    "primaire",
    "roman",
    "catholic",
}


def school_core_name(name: str) -> str:
    """Name with generic words removed ("Barlow Govt School" -> "barlow"), accents and punctuation ignored."""
    name = name if isinstance(name, str) else ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in SCHOOL_STOPWORDS]
    return "".join(words)


def names_similar(a: str, b: str, threshold: float = 0.75) -> bool:
    """True if two school names are close after removing generic words (fuzzy match)."""
    ca, cb = school_core_name(a), school_core_name(b)
    if not ca or not cb:
        return False
    if ca in cb or cb in ca:
        return True
    return SequenceMatcher(None, ca, cb).ratio() >= threshold


def match_official(
    osm_name: str,
    osm_xy_m,
    official: geopandas.GeoDataFrame,
    radius_m: float = 150.0,
    same_name_radius_m: float = 300.0,
) -> int | None:
    """Index of the official school an OSM school duplicates, else None.

    `official` must be in a metric CRS with a `name` column; `osm_xy_m` is in the same CRS.
    Duplicate means: within `radius_m` and a similar name (generic words such as "Government
    School" ignored), or within `radius_m` and unnamed, or within `same_name_radius_m` and an
    identical core name (official coordinates are sometimes a few hundred metres off). The
    nearest candidate wins.
    """
    point = Point(osm_xy_m)
    distance = official.geometry.distance(point)
    core = school_core_name(osm_name)
    for idx in distance[distance <= max(radius_m, same_name_radius_m)].sort_values().index:
        if distance[idx] <= radius_m and (not core or names_similar(osm_name, official.at[idx, "name"])):
            return idx
        if core and core == school_core_name(official.at[idx, "name"]):
            return idx
    return None


def clean_official_name(name: str) -> str:
    """Official names have lost accents ("�cole du Centre"): restore ecole and drop stray marks."""
    name = re.sub("�cole", "École", name or "")
    return re.sub("�", "", name).strip()


# --- airports and ports -------------------------------------------------------

# Documented fallback for ports that OSM does not map as a port feature. The
# coordinates are not typed in from memory: they come from OSM features in the
# same extract, looked up by `osm_ref` (type/id) at run time (see
# `transport_open_osm_nodes.py`). Entries: node_id, name, island, osm place feature.
MANUAL_PORTS = [
    {
        "node_id": "PLU",
        "name": "Port Louis",
        "island": "mauritius",
        "osm_ref": ("n", 1402034717),  # harbour=yes, name=Port Louis (seamark harbour, category "fishing")
        "note": "harbour node; the Port Louis harbour is mapped only as a point (quays are untagged)",
    },
    {
        "node_id": "PMA",
        "name": "Port Mathurin",
        "island": "rodrigues",
        "osm_ref": ("w", 300090875),  # industrial=port way, name=Port Mathurin
        "note": "industrial=port area",
    },
    {
        "node_id": "AGA-PSJ",
        "name": "Port Saint James",
        "island": "agalega",
        "osm_ref": ("n", 4947424953),  # place=hamlet, name=Port Saint James (North Island)
        "note": "place=hamlet, used as the port location; no harbour feature mapped",
    },
]


def aerodrome_id(tags: dict, osm_id) -> str:
    """IATA, else ICAO, else a slug of the name, else the OSM id."""
    return tags.get("iata") or tags.get("icao") or slugify(tags.get("name") or "") or f"osm-{osm_id}"


# --- readers ------------------------------------------------------------------


def _way_nodes(way) -> list:
    return [(n.ref, n.lon, n.lat) for n in way.nodes]


def read_highway_ways(pbf: str) -> list[dict]:
    """Highway ways of an extract that have a road class, with node coordinates."""
    import osmium

    out: list[dict] = []

    class Handler(osmium.SimpleHandler):
        def way(self, w):
            if "highway" not in w.tags:
                return
            tags = dict(w.tags)
            if road_class(tags) is None:
                return
            out.append({"id": w.id, "tags": tags, "nodes": _way_nodes(w)})

    Handler().apply_file(pbf, locations=True)
    return out


def read_metro(pbf: str) -> dict:
    """Light-rail data: track ways, stations, and route relations with their stops and ways.

    Returns a dict with `ways` {id: {"tags", "nodes": [ids]}}, `coords` {node id: (lon, lat)}
    for track nodes, `stations` (nodes with railway=station|halt on a light-rail line:
    `station_id`, `name`, `xy`, `osm_id`), `stop_nodes` {id: {"name", "xy"}} and `routes`
    (route=light_rail relations: id, name, tags, `stops` [node ids], `ways` [way ids]).
    """
    import osmium

    ways: dict = {}
    coords: dict = {}
    stations: list = []
    stop_nodes: dict = {}
    routes: list = []

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            tags = n.tags
            if tags.get("railway") in ("station", "halt") and (
                tags.get("station") == "light_rail" or tags.get("light_rail") == "yes"
            ):
                name = tags.get("name") or f"osm-{n.id}"
                stations.append({"osm_id": n.id, "name": name, "xy": (n.location.lon, n.location.lat)})
            if tags.get("railway") == "stop" and tags.get("light_rail") == "yes":
                stop_nodes[n.id] = {"name": tags.get("name", ""), "xy": (n.location.lon, n.location.lat)}

        def way(self, w):
            if w.tags.get("railway") != "light_rail":
                return
            nodes = _way_nodes(w)
            for ref, lon, lat in nodes:
                coords[ref] = (lon, lat)
            ways[w.id] = {"tags": dict(w.tags), "nodes": [n[0] for n in nodes]}

        def relation(self, r):
            if r.tags.get("type") == "route" and r.tags.get("route") == "light_rail":
                routes.append(
                    {
                        "id": r.id,
                        "name": r.tags.get("name", ""),
                        "tags": dict(r.tags),
                        "stops": [m.ref for m in r.members if m.type == "n" and m.role.startswith("stop")],
                        "ways": [m.ref for m in r.members if m.type == "w"],
                    }
                )

    Handler().apply_file(pbf, locations=True)
    return {"ways": ways, "coords": coords, "stations": stations, "stop_nodes": stop_nodes, "routes": routes}


def centroid_xy(nodes: list) -> tuple[float, float]:
    """Centroid (lon, lat) of a way: polygon centroid if closed, else the vertex mean."""
    pts = [(lon, lat) for _, lon, lat in nodes]
    if len(pts) >= 4 and pts[0] == pts[-1]:
        c = Polygon(pts).centroid
        return c.x, c.y
    return float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))


def read_features(pbf: str, selector, include: set | None = None) -> list[dict]:
    """Nodes and ways whose tag dict satisfies `selector(tags)`, as points.

    `include` is an optional set of (osm_type, osm_id) pairs that are always returned.

    Each result: `{"osm_type": "n"|"w", "osm_id", "tags", "xy": (lon, lat)}`; ways are
    reduced to a centroid.
    """
    import osmium

    out: list[dict] = []

    class Handler(osmium.SimpleHandler):
        def node(self, n):
            if (include and ("n", n.id) in include) or (len(n.tags) and selector(dict(n.tags))):
                out.append(
                    {"osm_type": "n", "osm_id": n.id, "tags": dict(n.tags), "xy": (n.location.lon, n.location.lat)}
                )

        def way(self, w):
            if (include and ("w", w.id) in include) or (len(w.tags) and selector(dict(w.tags))):
                out.append({"osm_type": "w", "osm_id": w.id, "tags": dict(w.tags), "xy": centroid_xy(_way_nodes(w))})

    Handler().apply_file(pbf, locations=True)
    return out


def point_frame(rows: list[dict], columns: dict | None = None) -> geopandas.GeoDataFrame:
    """GeoDataFrame (EPSG:4326) from feature dicts with an `xy` key; other keys become columns."""
    frame = pd.DataFrame([{k: v for k, v in r.items() if k != "xy"} for r in rows])
    geometry = [Point(r["xy"]) for r in rows]
    return geopandas.GeoDataFrame(frame, geometry=geometry, crs=4326)
