"""Locate the www.mauritius-buses.com bus stops with OpenStreetMap (open-data fallback, item 12).

The site gives each stop a name and a village but no coordinates. This script
builds a geocoder from the OSM extract only (named bus stops, named features,
named roads, place nodes, Village Council Area and ward polygons) and then
checks the result against the timetables.

1. Village area: the stop page's village (a "[Flacq]" qualifier is dropped) is
   matched by name to an OSM place node and/or admin polygon (level 8 VCA, level 9
   wards merged per town, place polygons); the area is the polygon (+300 m) and/or a
   circle around the place node (radius by place type). A locality at the start of the
   stop name ("Cassis - Royal College") narrows the search when OSM has it.
2. Candidates per stop, best first (location_quality, location_source):
     1 osm_bus_stop      named OSM bus stop / platform matching the stop name in the area
     1 osm_bus_station   amenity=bus_station for "Traffic Centre" / "Bus Station" / "Terminal"
     2 osm_feature       named OSM feature matching the landmark ("Rose Belle (Gebert)" -> Gebert)
     2 osm_junction      "Junction A / B Roads": closest points of the two named roads (< 60 m)
     2 osm_category      the only police station / school / church ... in the area
     3 osm_category_nearest  one of 2-3 such features, nearest the area centre
     3 osm_road          a named road matching the name: its point nearest the area centre
     3 osm_place         an OSM place node matching the stop name (a locality inside a village)
     4 village_centroid  the village's place node, else its polygon's representative point
     4 route_interpolated  between the nearest located stops before and after it on a route,
                       in proportion to the scheduled minutes (median over the routes)
   Stage 1 covers stops whose village or name locality is an OSM area. Stage 2 searches the
   others within 3 km of the point interpolated from their stage-1 neighbours (else
   island-wide, accepting only unique, near-exact matches), with the interpolated point
   itself as the level-4 fallback. Interpolation does not replace a village centroid: on
   the stops located at level 1 it was the less accurate of the two (error_vs_level1_m).
   Name matching uses transport.open_bus.normalise / similarity; when the name has a
   landmark with distinctive words, a match must share one of them.
3. Route check: along every route direction, a hop whose straight-line speed (after
   1000 m slack for location error, over at least 1 min) exceeds 80 km/h counts against
   the worse-located end, and a middle stop far off the line between its neighbours
   (zig-zag) against the middle stop. Each blamed stop is re-resolved: of its candidates
   (all levels, plus the interpolated point) the one with the fewest violations is kept
   (ties: better quality, then score); "+route_check" is added to its source. Three
   passes. A stop still blamed is downgraded one level ("+route_conflict", route_flag =
   number of violations).
4. Snap: a stop located at level 2-4 moves to the nearest OSM bus stop node (named or
   not) within 120 / 250 / 300 m; location_source gets "+osm_stop_snap".
5. Error estimate: for stops located at level 1, the distance from the level-1 point
   to what the other methods would have given (feature, place, road, village centroid,
   route interpolation from level-1 neighbours) estimates their typical error.

Stops with no candidate at all are left out and listed in --review (with the
stops still flagged by the route check). Only stops served by bus lines are
located (metro-only stations are not).

Outputs: --output CSV (site_stop_id, name, village, lon, lat, location_quality,
location_source, match_name, match_osm, match_score, route_flag), --review CSV,
--summary JSON.

Locations: (c) OpenStreetMap contributors, ODbL 1.0.
"""

import itertools
import json
import math
from collections import defaultdict

import click
import geopandas
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.ops import nearest_points, unary_union

from transport import open_bus, open_osm

CRS = "EPSG:32740"
NAME_KEYS = ["name", "alt_name", "official_name", "short_name", "old_name", "loc_name", "name:en", "name:fr"]
SNAP_M = {2: 120.0, 3: 250.0, 4: 300.0}


def is_bus_stop(tags):
    return (
        tags.get("highway") == "bus_stop"
        or tags.get("amenity") == "bus_station"
        or (tags.get("public_transport") in ("platform", "stop_position", "station") and tags.get("bus") == "yes")
    )


def read_osm(pbf):
    """Point features (one row per name variant), named roads and admin areas, in the metric CRS."""
    import osmium
    import shapely.wkb

    def wanted(tags):
        if is_bus_stop(tags):
            return True
        if "boundary" in tags or "admin_level" in tags:
            return False
        return any(k in tags for k in NAME_KEYS)

    rows = []
    for f in open_osm.read_features(pbf, wanted):
        tags = f["tags"]
        if open_osm.island_of(*f["xy"]) != "mauritius":
            continue
        if f["osm_type"] == "w" and "highway" in tags and not is_bus_stop(tags):
            continue  # roads are handled as lines
        kind = "bus_stop" if is_bus_stop(tags) else ("place" if "place" in tags else "poi")
        names = []
        for key in NAME_KEYS:
            names += [n.strip() for n in tags.get(key, "").split(";") if n.strip()]
        names = list(dict.fromkeys(names)) or [""]
        for name in names:
            rows.append(
                {
                    "osm": f"{f['osm_type']}{f['osm_id']}",
                    "name": name,
                    "kind": kind,
                    "category": open_bus.osm_category(tags),
                    "place": tags.get("place", ""),
                    "xy": f["xy"],
                }
            )
    feats = open_osm.point_frame(rows).to_crs(CRS)

    roads = []
    for way in open_osm.read_highway_ways(pbf):
        name = way["tags"].get("name")
        pts = [(lon, lat) for _, lon, lat in way["nodes"]]
        if not name or len(pts) < 2 or open_osm.island_of(*pts[0]) != "mauritius":
            continue
        roads.append({"osm": f"w{way['id']}", "name": name, "geometry": shapely.LineString(pts)})
    roads = geopandas.GeoDataFrame(roads, crs=4326).to_crs(CRS)

    fab = osmium.geom.WKBFactory()
    areas = []

    class Areas(osmium.SimpleHandler):
        def area(self, a):
            t = a.tags
            admin = t.get("boundary") == "administrative" and t.get("admin_level") in ("8", "9", "10")
            place = "place" in t
            if not (admin or place) or "name" not in t:
                return
            try:
                geom = shapely.wkb.loads(fab.create_multipolygon(a), hex=True)
            except Exception:
                return
            name = open_bus.admin_core_name(t["name"]) if admin else t["name"]
            areas.append({"name": name, "level": t.get("admin_level", "place"), "geometry": geom})

    Areas().apply_file(pbf, locations=True)
    areas = geopandas.GeoDataFrame(areas, crs=4326).to_crs(CRS)
    return feats, roads, areas


class Geocoder:
    def __init__(self, feats, roads, areas):
        self.feats = feats.reset_index(drop=True)
        self.feats["tok"] = self.feats["name"].map(open_bus.tokens)
        self.feats["dist"] = self.feats["name"].map(open_bus.distinctive)
        self.feats["norm"] = self.feats["name"].map(open_bus.normalise)
        self.roads = roads.reset_index(drop=True)
        self.roads["tok"] = self.roads["name"].map(open_bus.tokens)
        self.roads["dist"] = self.roads["name"].map(open_bus.distinctive)
        self.roads["norm"] = self.roads["name"].map(open_bus.normalise)
        self.areas = areas.reset_index(drop=True)
        self.areas["norm"] = self.areas["name"].map(open_bus.normalise)
        self.places = self.feats[self.feats["kind"] == "place"]
        self._area_cache = {}

    # --- areas -------------------------------------------------------------
    def area(self, village):
        key = open_bus.normalise(village)
        if not key:
            return None
        if key in self._area_cache:
            return self._area_cache[key]
        polys = self.areas[self.areas["norm"] == key]
        places = self.places[self.places["norm"] == key]
        if polys.empty and places.empty:
            # fuzzy: one clear match only
            scored = [(open_bus.similarity(village, n), i) for i, n in self.places["name"].items()]
            scored = [s for s in scored if s[0] >= 0.9]
            if scored:
                best = max(scored)[0]
                places = self.places.loc[[i for s, i in scored if s == best]]
        result = None
        geom, anchor, source = None, None, ""
        if not polys.empty:
            geom = unary_union(list(polys.geometry)).buffer(300)
            source = f"admin:{polys.iloc[0]['name']}"
        if not places.empty:
            places = places.assign(rank=places["place"].map(lambda p: open_bus.PLACE_RANK.get(p, 9)))
            if geom is not None:
                inside = places[places.geometry.within(geom.buffer(2000))]
                places = inside if not inside.empty else places.iloc[0:0]
            if not places.empty:
                best = places.sort_values("rank").iloc[0]
                anchor = best.geometry
                circle = anchor.buffer(open_bus.PLACE_RADIUS_M.get(best["place"], 1000))
                geom = circle if geom is None else unary_union([geom, circle])
                source = (source + ";" if source else "") + f"place:{best['name']}({best['place']})"
        if geom is not None:
            if anchor is None:
                anchor = geom.centroid if geom.contains(geom.centroid) else geom.representative_point()
            result = {"geometry": geom, "anchor": anchor, "source": source}
        self._area_cache[key] = result
        return result

    # --- candidate search --------------------------------------------------
    def _in(self, frame, region):
        if region is None:
            return frame
        idx = frame.sindex.query(region, predicate="intersects")
        return frame.iloc[np.sort(idx)]

    @staticmethod
    def _score(query_tok, query_dist, row_tok, row_dist):
        if query_dist and not (query_dist & row_dist):
            return 0.0
        return open_bus.similarity_tokens(query_tok, row_tok)

    def _best_named(self, frame, query, threshold, anchor):
        """Rows of `frame` best matching `query`; returns (rows, score) or (None, 0)."""
        qt, qd = open_bus.tokens(query), open_bus.distinctive(query)
        if not qt or frame.empty:
            return None, 0.0
        scores = np.array([self._score(qt, qd, t, d) for t, d in zip(frame["tok"], frame["dist"], strict=True)])
        if scores.max() < threshold:
            return None, 0.0
        best = scores.max()
        rows = frame[scores >= best - 0.02]
        if anchor is not None:
            rows = rows.assign(_d=rows.distance(anchor)).sort_values("_d")
        return rows, float(best)

    def focus(self, name, village):
        """Search area of a stop from its village and the locality at the start of its name."""
        parts = open_bus.split_stop_name(name, village)
        area = self.area(village)
        sub = None
        if parts["area"] and open_bus.normalise(parts["area"]) != open_bus.normalise(village):
            sub = self.area(parts["area"])
            if sub is not None and area is not None and sub["anchor"].distance(area["geometry"]) > 2000:
                sub = None
        return parts, area, sub

    def candidates(self, name, village, hint=None):
        """Candidate locations, best first. `hint` (Point, radius m) is the search area for a
        stop whose village is unknown to OSM (from the route neighbours)."""
        parts, area, sub = self.focus(name, village)
        focus = sub or area
        from_route = False
        if focus is None and hint is not None:
            focus = {"geometry": hint[0].buffer(hint[1]), "anchor": hint[0], "source": "route"}
            from_route = True
        region = focus["geometry"].buffer(800) if focus else None
        anchor = focus["anchor"] if focus else None
        strict = region is None
        landmark = parts["landmark"]
        if not landmark and sub is None and parts["area"] and village:
            landmark = ""  # plain locality name: handled by the place search
        query = landmark or name
        out = []

        def add(geom, quality, source, score, row=None, line=None):
            out.append(
                {
                    "line": line,
                    "x": geom.x,
                    "y": geom.y,
                    "quality": quality,
                    "source": source,
                    "score": round(score, 3),
                    "match_name": row["name"] if row is not None else "",
                    "match_osm": row["osm"] if row is not None else "",
                }
            )

        feats = self._in(self.feats, region)
        stops = feats[feats["kind"] == "bus_stop"]
        threshold = 0.95 if strict else 0.8
        for q in dict.fromkeys([name, query]):
            rows, score = self._best_named(stops[stops["name"] != ""], q, threshold, anchor)
            if rows is not None and (not strict or len(rows) == 1):
                add(rows.iloc[0].geometry, 1, "osm_bus_stop", score, rows.iloc[0])
                break
        words = set(open_bus.tokens(name))
        if {"traffic", "terminal"} & words or {"bus", "station"} <= set(open_bus.normalise(name).split()):
            stations = feats[feats["category"] == "bus_station"]
            if not stations.empty and anchor is not None:
                row = stations.assign(_d=stations.distance(anchor)).sort_values("_d").iloc[0]
                add(row.geometry, 1, "osm_bus_station", 1.0, row)
        pois = feats[(feats["kind"] == "poi") & (feats["name"] != "")]
        if query:
            rows, score = self._best_named(pois, query, 0.95 if strict else 0.85, anchor)
            if rows is not None:
                spread = rows.geometry.distance(rows.geometry.iloc[0]).max()
                if not strict or spread < 1000:
                    add(rows.iloc[0].geometry, 2 if spread < 1000 else 3, "osm_feature", score, rows.iloc[0])
        if parts["junction"]:
            point, score = self._junction(parts["junction"], region, pois)
            if point is not None:
                add(point, 2, "osm_junction", score)
        land_tok = open_bus.tokens(landmark) if landmark else []
        if land_tok and not open_bus.distinctive(landmark) and region is not None:
            cats = {open_bus.CATEGORY_WORDS[w] for w in land_tok if w in open_bus.CATEGORY_WORDS}
            if len(cats) == 1:
                cat = cats.pop()
                hits = feats[feats["category"] == cat].drop_duplicates("osm")
                hits = hits[hits.within(focus["geometry"])]
                if len(hits) == 1:
                    add(hits.iloc[0].geometry, 2, "osm_category", 0.8, hits.iloc[0])
                elif 2 <= len(hits) <= 3:
                    row = hits.assign(_d=hits.distance(anchor)).sort_values("_d").iloc[0]
                    add(row.geometry, 3, "osm_category_nearest", 0.6, row)
        roads = self._in(self.roads, region)
        if query and not roads.empty:
            rows, score = self._best_named(roads, query, 0.95 if strict else 0.85, anchor)
            if rows is not None and anchor is not None:
                line = rows.iloc[0].geometry
                add(nearest_points(line, anchor)[0], 3, "osm_road", score, rows.iloc[0], line)
            elif rows is not None:
                # No village: a road found island-wide is used only if all its matching ways are
                # close together; the point is refined along the road from the route (below).
                line = unary_union(list(rows.geometry))
                if line.envelope.length < 4 * 3000:
                    add(nearest_points(line, line.centroid)[0], 4, "osm_road_unanchored", score, rows.iloc[0], line)
        place_region = focus["geometry"].buffer(2500) if focus else None
        places = self._in(self.places, place_region)
        village_norm = open_bus.normalise(village)
        for q in dict.fromkeys([name, parts["area"], landmark]):
            if not q or open_bus.normalise(q) == village_norm:
                continue
            rows, score = self._best_named(places, q, 0.95 if strict else 0.86, anchor)
            if rows is not None and (not strict or len(rows) == 1):
                add(rows.iloc[0].geometry, 3, "osm_place", score, rows.iloc[0])
                break
        if focus is not None and not from_route:
            out.append(
                {
                    "line": None,
                    "x": focus["anchor"].x,
                    "y": focus["anchor"].y,
                    "quality": 4,
                    "source": "village_centroid",
                    "score": 0.0,
                    "match_name": focus["source"],
                    "match_osm": "",
                }
            )
        out.sort(key=lambda c: (c["quality"], -c["score"]))
        return out

    def _junction(self, pair, region, pois):
        roads = self._in(self.roads, region)
        geoms = []
        for part in pair:
            found = []
            for frame, thr in ((roads, 0.8), (pois, 0.85)):
                rows, _ = self._best_named(frame, part, thr, None)
                if rows is not None:
                    found += list(rows.geometry)
            if not found:
                return None, 0.0
            geoms.append(unary_union(found))
        if geoms[0].distance(geoms[1]) > 60:
            return None, 0.0
        a, b = nearest_points(geoms[0], geoms[1])
        return Point((a.x + b.x) / 2, (a.y + b.y) / 2), 0.9


def route_sequences(line_stops):
    """{(route_id, direction): [(site_stop_id, cum_min), ...]} in stop order."""
    seqs = {}
    for key, part in line_stops.sort_values("stop_sequence").groupby(["route_id", "direction"]):
        seqs[key] = list(zip(part["site_stop_id"].astype(str), part["cum_min"].astype(float), strict=True))
    return seqs


def violations(seq, pos, i, xy=None):
    """Route-check violations around position i of a sequence; `xy` overrides stop i's position."""

    def p(j):
        if j == i and xy is not None:
            return xy
        return pos.get(seq[j][0])

    here = p(i)
    if here is None:
        return 0
    count = 0
    prev_j = next((j for j in range(i - 1, -1, -1) if p(j) is not None), None)
    next_j = next((j for j in range(i + 1, len(seq)) if p(j) is not None), None)
    for j in (prev_j, next_j):
        if j is None:
            continue
        d = math.dist(here, p(j))
        dt = abs(seq[i][1] - seq[j][1]) if not (math.isnan(seq[i][1]) or math.isnan(seq[j][1])) else 5.0
        if open_bus.implausible_jump(d, dt):
            count += 1
    if prev_j is not None and next_j is not None:
        a, b = p(prev_j), p(next_j)
        if open_bus.zigzag(math.dist(a, here), math.dist(here, b), math.dist(a, b)):
            count += 1
    return count


def blame(seqs, pos, quality):
    """Route-check violations per stop: an implausible hop counts against the worse-located
    end (both if equal), a zig-zag against the middle stop."""
    counts = defaultdict(int)
    for seq in seqs.values():
        located = [(sid, t) for sid, t in seq if sid in pos]
        for (a, ta), (b, tb) in itertools.pairwise(located):
            dt = abs(tb - ta) if not (math.isnan(ta) or math.isnan(tb)) else 5.0
            if open_bus.implausible_jump(math.dist(pos[a], pos[b]), dt):
                qa, qb = quality[a], quality[b]
                for sid in [a, b] if qa == qb else [a if qa > qb else b]:
                    counts[sid] += 1
        for (a, _), (m, _), (b, _) in zip(located[:-2], located[1:-1], located[2:], strict=True):
            if open_bus.zigzag(math.dist(pos[a], pos[m]), math.dist(pos[m], pos[b]), math.dist(pos[a], pos[b])):
                counts[m] += 1
    return dict(counts)


def interpolations(stop_id, occurrences, seqs, pos, quality, max_quality=2):
    """Interpolated points for a stop from each route occurrence with well-located neighbours."""
    points = []
    for key, i in occurrences[stop_id]:
        seq = seqs[key]
        prev_j = next((j for j in range(i - 1, -1, -1) if quality.get(seq[j][0], 9) <= max_quality), None)
        next_j = next((j for j in range(i + 1, len(seq)) if quality.get(seq[j][0], 9) <= max_quality), None)
        if prev_j is None or next_j is None:
            continue
        ta, tb, t = seq[prev_j][1], seq[next_j][1], seq[i][1]
        if any(math.isnan(v) for v in (ta, tb, t)) or tb - ta > 30:
            continue
        points.append(open_bus.interpolate_xy(ta, pos[seq[prev_j][0]], tb, pos[seq[next_j][0]], t))
    if not points:
        return None
    arr = np.array(points)
    return float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))


@click.command()
@click.option("--pbf", required=True, help="OSM extract (.osm.pbf)")
@click.option("--stops", "stops_csv", required=True, help="parsed stops.csv")
@click.option("--line-stops", "line_stops_csv", required=True, help="parsed line_stops.csv")
@click.option("--output", required=True, help="located stops CSV")
@click.option("--review", required=True, help="CSV of stops not located or still flagged")
@click.option("--summary", required=True, help="summary JSON")
def main(pbf, stops_csv, line_stops_csv, output, review, summary):
    stops = pd.read_csv(stops_csv, dtype={"site_stop_id": str}, keep_default_na=False)
    stops = stops[stops["served_by_bus"].astype(str) == "True"].copy()
    # "Providence [Flacq]": the bracket disambiguates between villages of the same name.
    stops["village"] = stops["village"].str.replace(r"\s*\[.*?\]", "", regex=True).str.strip()
    line_stops = pd.read_csv(line_stops_csv, dtype={"site_stop_id": str, "route_id": str})
    feats, roads, areas = read_osm(pbf)
    geo = Geocoder(feats, roads, areas)

    seqs = route_sequences(line_stops)
    occurrences = defaultdict(list)
    for key, seq in seqs.items():
        for i, (sid, _) in enumerate(seq):
            occurrences[sid].append((key, i))

    # Stage 1: stops whose village (or name locality) is an OSM area.
    cands, pending = {}, []
    for row in stops.itertuples():
        _, area, sub = geo.focus(row.name, row.village)
        if area is None and sub is None:
            pending.append(row)
            cands[row.site_stop_id] = []
        else:
            cands[row.site_stop_id] = geo.candidates(row.name, row.village)
    pick = {sid: (c[0] if c else None) for sid, c in cands.items()}
    # Stage 2: the others are searched around the point interpolated from their located route
    # neighbours (radius 3 km, or half the neighbours' distance); island-wide (strict) otherwise.
    pos1 = {sid: (c["x"], c["y"]) for sid, c in pick.items() if c is not None}
    qual1 = {sid: c["quality"] for sid, c in pick.items() if c is not None}
    for row in pending:
        xy = interpolations(row.site_stop_id, occurrences, seqs, pos1, qual1, max_quality=4)
        hint = (Point(xy), 3000.0) if xy is not None else None
        found = geo.candidates(row.name, row.village, hint)
        if xy is not None:
            found.append(
                {"x": xy[0], "y": xy[1], "quality": 4, "source": "route_interpolated", "score": 0.0}
                | {"match_name": "", "match_osm": "", "line": None}
            )
        cands[row.site_stop_id] = found
        pick[row.site_stop_id] = found[0] if found else None

    def positions():
        return {sid: (c["x"], c["y"]) for sid, c in pick.items() if c is not None}

    def qualities():
        return {sid: c["quality"] for sid, c in pick.items() if c is not None}

    # Interpolation for stops at level 4 or unlocated.
    pos, qual = positions(), qualities()
    for sid in pick:
        if qual.get(sid, 9) < 4:
            continue
        xy = interpolations(sid, occurrences, seqs, pos, qual)
        if pick[sid] is None:
            if xy is None:
                xy = interpolations(sid, occurrences, seqs, pos, qual, max_quality=3)
            if xy is not None:
                cand = {"x": xy[0], "y": xy[1], "quality": 4, "source": "route_interpolated", "score": 0.0}
                cands[sid].append(cand | {"match_name": "", "match_osm": "", "line": None})
                pick[sid] = cands[sid][-1]
            continue
        if xy is None:
            continue
        if pick[sid].get("line") is not None and pick[sid]["source"] == "osm_road_unanchored":
            on_road = nearest_points(pick[sid]["line"], Point(xy))[0]
            if on_road.distance(Point(xy)) < 1500:
                cand = dict(pick[sid], x=on_road.x, y=on_road.y, quality=3, source="osm_road+route_interpolated")
                cands[sid].append(cand)
                pick[sid] = cand
            continue
        # A village centroid is kept: on the stops located at level 1, interpolation is the
        # less accurate of the two (summary error_vs_level1_m). It stays a candidate for the
        # route check.
        cand = {"x": xy[0], "y": xy[1], "quality": 4, "source": "route_interpolated", "score": 0.0}
        cands[sid].append(cand | {"match_name": "", "match_osm": "", "line": None})

    # Route consistency passes.
    for _ in range(3):
        pos = positions()
        blamed = blame(seqs, pos, qualities())
        total = {sid: sum(violations(seqs[k], pos, i) for k, i in occurrences[sid]) for sid in blamed}
        changed = 0
        for sid in sorted(blamed, key=lambda s: (-blamed[s], -pick[s]["quality"])):
            pos = positions()
            options = list(cands[sid])
            others = {k: v for k, v in pos.items() if k != sid}
            for max_quality in (2, 3, 4):
                xy = interpolations(sid, occurrences, seqs, others, qualities(), max_quality=max_quality)
                if xy is not None:
                    break
            if xy is not None:
                options.append(
                    {"x": xy[0], "y": xy[1], "quality": 4, "source": "route_interpolated", "score": 0.0}
                    | {"match_name": "", "match_osm": "", "line": None}
                )
            scored = []
            for c in options:
                v = sum(violations(seqs[k], pos, i, (c["x"], c["y"])) for k, i in occurrences[sid])
                scored.append((v, c["quality"], -c["score"], len(scored), c))
            best = min(scored)
            if best[4] is not pick[sid] and best[0] < total[sid]:
                new = dict(best[4])
                if not new["source"].endswith("+route_check"):
                    new["source"] += "+route_check"
                pick[sid] = new
                changed += 1
        if not changed:
            break

    flags = blame(seqs, positions(), qualities())
    for sid in flags:
        pick[sid] = dict(pick[sid], quality=min(pick[sid]["quality"] + 1, 4))
        pick[sid]["source"] += "+route_conflict"

    # Snap to the nearest OSM bus stop node.
    osm_stops = feats[feats["kind"] == "bus_stop"].drop_duplicates("osm")
    tree = shapely.STRtree(list(osm_stops.geometry))
    for sid, c in pick.items():
        if c is None or c["quality"] < 2 or c["source"].startswith(("osm_bus_stop", "osm_bus_station")):
            continue
        here = Point(c["x"], c["y"])
        j = tree.nearest(here)
        target = osm_stops.geometry.iloc[j]
        if here.distance(target) <= SNAP_M[c["quality"]]:
            pick[sid] = dict(c, x=target.x, y=target.y, source=c["source"] + "+osm_stop_snap")

    # Error estimate on stops located at level 1 before the route check.
    errors = defaultdict(list)
    level1 = {sid: c[0] for sid, c in cands.items() if c and c[0]["quality"] == 1}
    pos1 = {sid: (c["x"], c["y"]) for sid, c in level1.items()}
    q1 = {sid: 1 for sid in level1}
    for sid, c1 in level1.items():
        truth = (c1["x"], c1["y"])
        for c in cands[sid][1:]:
            if c["quality"] == 1:
                continue
            errors[c["source"]].append(math.dist(truth, (c["x"], c["y"])))
        others = {k: v for k, v in pos1.items() if k != sid}
        xy = interpolations(sid, occurrences, seqs, others, q1, max_quality=1)
        if xy is not None:
            errors["route_interpolated"].append(math.dist(truth, xy))
    error_stats = {
        k: {"n": len(v), "median_m": round(float(np.median(v))), "p80_m": round(float(np.percentile(v, 80)))}
        for k, v in sorted(errors.items())
        if len(v) >= 3
    }

    rows, review_rows = [], []
    for row in stops.itertuples():
        c = pick[row.site_stop_id]
        if c is None:
            review_rows.append({"site_stop_id": row.site_stop_id, "name": row.name, "village": row.village,
                                "issue": "not located (no village area and no name match)"})  # fmt: skip
            continue
        rows.append(
            {
                "site_stop_id": row.site_stop_id,
                "name": row.name,
                "village": row.village,
                "x": c["x"],
                "y": c["y"],
                "location_quality": c["quality"],
                "location_source": c["source"],
                "match_name": c["match_name"],
                "match_osm": c["match_osm"],
                "match_score": c["score"],
                "route_flag": flags.get(row.site_stop_id, 0),
            }
        )
        if row.site_stop_id in flags:
            review_rows.append({"site_stop_id": row.site_stop_id, "name": row.name, "village": row.village,
                                "issue": f"route check: {flags[row.site_stop_id]} violations remain",
                                "location_source": c["source"]})  # fmt: skip
    located = pd.DataFrame(rows)
    pts = geopandas.GeoSeries(geopandas.points_from_xy(located["x"], located["y"]), crs=CRS).to_crs(4326)
    located.insert(3, "lon", pts.x.round(6))
    located.insert(4, "lat", pts.y.round(6))
    located = located.drop(columns=["x", "y"])
    located.to_csv(output, index=False)
    pd.DataFrame(review_rows, columns=["site_stop_id", "name", "village", "issue", "location_source"]).to_csv(
        review, index=False
    )

    base_source = located["location_source"].str.split("+").str[0]
    out = {
        "bus_stops": len(stops),
        "located": len(located),
        "not_located": len(stops) - len(located),
        "by_quality": located["location_quality"].value_counts().sort_index().to_dict(),
        "share_by_quality": (located["location_quality"].value_counts(normalize=True).sort_index().round(3).to_dict()),
        "by_source": base_source.value_counts().to_dict(),
        "re_resolved_by_route_check": int(located["location_source"].str.contains("route_check").sum()),
        "still_flagged_by_route_check": len(flags),
        "snapped_to_osm_stop": int(located["location_source"].str.contains("osm_stop_snap").sum()),
        "quality_levels": open_bus.QUALITY,
        "error_vs_level1_m": error_stats,
        "osm_features": len(feats),
        "osm_bus_stops": len(osm_stops),
        "osm_named_roads": len(roads),
        "osm_areas": len(areas),
        "source": "stop names: www.mauritius-buses.com; locations: (c) OpenStreetMap contributors, ODbL 1.0",
    }
    with open(summary, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "quality_levels"}, indent=2, default=str))


if __name__ == "__main__":
    main()
