"""Open-data fallback for the bus inputs (transport-data items 12-14).

Sources:
    - www.mauritius-buses.com: line index, line pages (ordered stops per
      direction with cumulative minutes, the official NLTA timetable with fare
      stages) and stop pages (stop name and village). Third-party site, data
      derived from the official NLTA timetables, "Data provided without
      warranty". The site has no stop coordinates.
    - OpenStreetMap (c) OpenStreetMap contributors, ODbL 1.0, for locations.

The functions here are pure (HTML text or tables in, tables out) so they can
be tested without the network or a PBF file.

Sections:
    pages       link discovery and HTML parsers (BeautifulSoup, stdlib html.parser)
    rules       headway rule, fare-stage alignment, gap recompute
    names       name normaliser and similarity used by the geocoder
    geometry    distances and the route plausibility checks
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd
from bs4 import BeautifulSoup

METRO_ROUTE = re.compile(r"^M\d+$")

# --- pages --------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node is not None else ""


def line_cache_name(slug: str) -> str:
    """Cache file of a line page; the slug is reduced to safe characters."""
    return f"line/{re.sub(r'[^A-Za-z0-9_-]', '_', slug)}.html"


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Line slugs and stop ids listed in sitemap.xml, in order of appearance."""
    lines = re.findall(r"<loc>[^<]*/line/([^/<]+)/</loc>", xml)
    stops = re.findall(r"<loc>[^<]*/stop/(\d+)/</loc>", xml)
    return list(dict.fromkeys(lines)), list(dict.fromkeys(stops))


def stop_links(html: str) -> list[str]:
    """Stop ids linked from a page (/stop/<id>/), in order, unique."""
    ids = []
    for a in _soup(html).find_all("a", href=True):
        m = re.fullmatch(r"(?:https?://[^/]+)?/stop/(\d+)/?", a["href"])
        if m:
            ids.append(m.group(1))
    return list(dict.fromkeys(ids))


def parse_frequency_text(text: str) -> dict:
    """'every 12 min' / '16 minutes' / '6 a day' -> {"every_min", "times_per_day"} (NaN when absent)."""
    out = {"every_min": math.nan, "times_per_day": math.nan}
    text = (text or "").lower()
    m = re.search(r"(?:every\s*~?\s*)?(\d+(?:\.\d+)?)\s*min", text)
    if m:
        out["every_min"] = float(m.group(1))
    m = re.search(r"(\d+)\s*(?:times\s*)?a day", text)
    if m:
        out["times_per_day"] = float(m.group(1))
    return out


def parse_line_index(html: str) -> pd.DataFrame:
    """The /all-buslines/ list: one row per line."""
    rows = []
    for li in _soup(html).select("li.lr"):
        a = li.find("a", href=True)
        m = re.search(r"/line/([^/]+)/", a["href"]) if a else None
        if not m:
            continue
        route = li.select_one(".lr-route")
        parts = [_text(s) for s in li.select(".lr-meta > span")]
        operator = _text(li.select_one(".lr-op"))
        freq = {"every_min": math.nan, "times_per_day": math.nan}
        notes = []
        for part in parts[1:]:
            parsed = parse_frequency_text(part)
            if not (math.isnan(parsed["every_min"]) and math.isnan(parsed["times_per_day"])):
                freq = parsed
            else:
                notes.append(part)
        origin = destination = ""
        if route is not None:
            pieces = [p.strip() for p in re.split(r"\bto\b", _text(route)) if p.strip()]
            origin, destination = (pieces[0], pieces[-1]) if len(pieces) >= 2 else (_text(route), "")
        rows.append(
            {
                "slug": m.group(1),
                "route_id": _text(li.select_one(".chip")) or m.group(1),
                "title": a.get("title", ""),
                "index_origin": origin,
                "index_destination": destination,
                "index_operator": operator,
                "index_every_min": freq["every_min"],
                "index_times_per_day": freq["times_per_day"],
                "index_days_note": "; ".join(notes),
            }
        )
    return pd.DataFrame(rows)


def _minutes(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*min", text or "")
    return float(m.group(1)) if m else math.nan


def _clock_minutes(text: str) -> float:
    """'06h15' / '6:15' / '~05:25' -> minutes after midnight (NaN if none)."""
    m = re.search(r"(\d{1,2})\s*[h:.]\s*(\d{2})", text or "")
    return float(int(m.group(1)) * 60 + int(m.group(2))) if m else math.nan


def parse_line_page(html: str) -> dict:
    """Parse a /line/<slug>/ page.

    Returns {"line": {...}, "stops": [...], "fares": [...], "schedule": [...]}:
        line      route_id, title, operator, origin, via, destination,
                  operating_days, average_frequency_min, times_per_day,
                  frequency_text, timetable_scan_url, canonical
        stops     direction (0, 1, ... in page order), towards, stop_sequence (from 1),
                  stop_name, site_stop_id, cum_min (first stop 0)
        fares     direction, row (from 1), fare_stage_label, locality, minutes
                  (the "Official bus schedule" stage tables, when present)
        schedule  days, from, first_bus_min, last_bus_min (minutes after midnight)
        departures  {direction number: [minutes after midnight]} when the page lists the
                  weekday departure times instead of a frequency
    """
    soup = _soup(html)
    main = soup.find("main") or soup
    line: dict = {
        "route_id": _text(main.select_one(".line-chip .chip")) or _text(main.select_one(".chip")),
        "title": _text(main.find("h1")),
    }
    canonical = soup.find("link", rel="canonical")
    line["canonical"] = canonical.get("href", "") if canonical else ""
    facts = {}
    dl = main.select_one("dl.facts")
    if dl is not None:
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            facts[_text(dt).lower()] = dd
    line["operator"] = _text(facts.get("operated by"))
    line["origin"] = line["via"] = line["destination"] = ""
    route = facts.get("route")
    if route is not None:
        chunks, current, role = {"origin": [], "via": [], "destination": []}, "origin", None
        for child in route.children:
            if getattr(child, "name", None) == "span" and "to" in (child.get("class") or []):
                word = _text(child).lower()
                role = "via" if word == "via" else "destination"
                current = role
                continue
            piece = _text(child) if getattr(child, "name", None) else str(child).strip()
            if piece:
                chunks[current].append(piece)
        line.update({k: re.sub(r"\s+", " ", " ".join(v)).strip() for k, v in chunks.items()})
    line["operating_days"] = _text(facts.get("operating days"))
    freq_text = _text(facts.get("average frequency")) or _text(facts.get("frequency"))
    for key, dd in facts.items():
        if not freq_text and ("frequency" in key or "a day" in _text(dd)):
            freq_text = _text(dd)
    parsed = parse_frequency_text(freq_text)
    line["frequency_text"] = freq_text
    line["average_frequency_min"] = parsed["every_min"]
    line["times_per_day"] = parsed["times_per_day"]
    scan = [a["href"] for a in main.find_all("a", href=True) if a["href"].lower().split("#")[0].endswith(".pdf")]
    line["timetable_scan_url"] = scan[0] if scan else ""

    stops = []
    for direction, block in enumerate(main.select(".line-stops .stop-seq")):
        towards = re.sub(r"^\s*towards\s+", "", _text(block.find(["h3", "h2"])), flags=re.I)
        for seq, li in enumerate(block.select("ol > li"), start=1):
            a = li.find("a", href=True)
            m = re.search(r"/stop/(\d+)/", a["href"]) if a else None
            name = _text(a) if a else _text(li)
            minutes = _minutes(_text(li.select_one(".muted")))
            if math.isnan(minutes) and seq == 1:
                minutes = 0.0
            stops.append(
                {
                    "direction": direction,
                    "towards": towards,
                    "stop_sequence": seq,
                    "stop_name": name,
                    "site_stop_id": m.group(1) if m else "",
                    "cum_min": minutes,
                }
            )

    fares, schedule = [], []
    timetable = main.select_one("section.timetable")
    if timetable is not None:
        fare_dir = -1
        for table in timetable.find_all("table"):
            head = [_text(th).lower() for th in table.select("thead th")] or [
                _text(td).lower() for td in table.find("tr").find_all(["th", "td"])
            ]
            body = [[_text(td) for td in tr.find_all("td")] for tr in table.select("tbody tr")]
            if head and head[0].startswith("fare stage"):
                fare_dir += 1
                for i, cells in enumerate(body, start=1):
                    if len(cells) >= 3:
                        fares.append(
                            {
                                "direction": fare_dir,
                                "row": i,
                                "fare_stage_label": cells[0],
                                "locality": cells[1],
                                "minutes": _minutes(cells[2] + " min"),
                            }
                        )
            elif "1st bus" in head or "first bus" in head:
                for cells in body:
                    if len(cells) >= 4:
                        schedule.append(
                            {
                                "days": cells[0],
                                "from": cells[1],
                                "first_bus_min": _clock_minutes(cells[2]),
                                "last_bus_min": _clock_minutes(cells[3]),
                            }
                        )
    departures = {}
    if timetable is not None:
        for li in timetable.select("li"):
            text = _text(li)
            m = re.match(r"^Direction\s+(\d+)\s*·(.*)$", text)
            if m and "minute" not in m.group(2).lower():
                times = [_clock_minutes(t) for t in re.findall(r"\d{1,2}h\d{2}", m.group(2))]
                if times:
                    departures[int(m.group(1))] = times
    return {"line": line, "stops": stops, "fares": fares, "schedule": schedule, "departures": departures}


def departure_gap_min(departures: dict) -> float:
    """Mean gap (minutes) between consecutive listed weekday departures, over the directions."""
    gaps = []
    for times in departures.values():
        times = sorted(t for t in times if not math.isnan(t))
        gaps += [b - a for a, b in itertools.pairwise(times) if b > a]
    return float(sum(gaps) / len(gaps)) if gaps else math.nan


def parse_stop_page(html: str) -> dict:
    """Parse a /stop/<id>/ page: site_stop_id, name, village, line_directions, lines (route ids)."""
    soup = _soup(html)
    main = soup.find("main") or soup
    url = ""
    for tag in (soup.find("link", rel="canonical"), soup.find("meta", property="og:url")):
        if tag is not None:
            url = tag.get("href") or tag.get("content") or ""
            if url:
                break
    m = re.search(r"/stop/(\d+)/", url)
    intro = _text(main.select_one("p.muted"))
    village = ""
    vm = re.search(r"(?:Bus|Metro)?\s*stop(?: and station)? in (.+?)\.(?:\s|$)", intro) or re.search(
        r"\bin ([^.]+)\.", intro
    )
    if vm:
        village = vm.group(1).strip()
    dirs = re.search(r"(\d+) line directions?", intro)
    lines = []
    for a in main.select("table.stop-lines a[href], table.lines a[href]"):
        lm = re.search(r"/line/([^/]+)/", a["href"])
        if lm:
            lines.append(_text(a))
    return {
        "site_stop_id": m.group(1) if m else "",
        "name": _text(main.find("h1")),
        "village": village,
        "line_directions": int(dirs.group(1)) if dirs else None,
        "lines": "|".join(dict.fromkeys(lines)),
    }


# --- rules --------------------------------------------------------------------

# Operating span assumed when a line only gives "N a day" and no first/last bus.
DEFAULT_SPAN_MIN = 12 * 60.0


def headway_min(
    average_frequency_min: float, times_per_day: float, span_min: float = math.nan, departure_gap: float = math.nan
) -> tuple[float, str]:
    """Headway (minutes) of a line and the rule used.

    1. The page's "Average frequency N minutes" (or the index's "every N min"): headway = N.
    2. Else the listed weekday departure times: the mean gap between consecutive departures.
    3. Else "N a day" (departures per direction per day): N departures spread over the
       weekday operating span (last minus first bus from the same terminus, mean over the
       termini) are span / (N - 1) apart; with no span (or N = 1), 12 hours / N.
    4. Else NaN (the workflow then uses bus.default_headway_min).
    """
    if average_frequency_min and not math.isnan(average_frequency_min) and average_frequency_min > 0:
        return float(average_frequency_min), "average_frequency"
    if departure_gap and not math.isnan(departure_gap) and departure_gap > 0:
        return float(departure_gap), "departure_list_gaps"
    if times_per_day and not math.isnan(times_per_day) and times_per_day > 0:
        if span_min and not math.isnan(span_min) and span_min > 0 and times_per_day > 1:
            return float(span_min) / (times_per_day - 1), "times_per_day_over_span"
        return DEFAULT_SPAN_MIN / times_per_day, "times_per_day_over_12h"
    return math.nan, "none"


def operating_span_min(schedule: list[dict]) -> float:
    """Weekday span (last - first bus) averaged over termini; falls back to any day type."""
    rows = [r for r in schedule if not math.isnan(r["first_bus_min"]) and not math.isnan(r["last_bus_min"])]
    weekday = [r for r in rows if "week" in r["days"].lower() or "monday" in r["days"].lower()] or rows
    spans = [r["last_bus_min"] - r["first_bus_min"] for r in weekday if r["last_bus_min"] > r["first_bus_min"]]
    return float(sum(spans) / len(spans)) if spans else math.nan


def fare_stage_number(label: str) -> float:
    """'12' -> 12, '29A' -> 29 (sub-stage A counted with its stage number), '' -> NaN."""
    m = re.match(r"\s*(\d+)", str(label or ""))
    return float(m.group(1)) if m else math.nan


def align_fare_stages(stops: pd.DataFrame, fares: pd.DataFrame) -> pd.Series:
    """Fare stage label per stop row of one direction, from the official stage table.

    The stage table is matched row by row when it has as many rows as the stop list and
    the minutes agree (within 1 min) on every row where both give minutes (the official
    table often gives minutes only at timing points); otherwise each stop takes the stage of the table row
    with the same minutes, when exactly one row has them. Unmatched stops get "".
    """
    out = pd.Series("", index=stops.index, dtype=object)
    if fares.empty or stops.empty:
        return out
    s_min = stops["cum_min"].to_numpy()
    f_min = fares["minutes"].to_numpy()
    if len(stops) == len(fares) and all(
        math.isnan(a) or math.isnan(b) or abs(a - b) <= 1.0 for a, b in zip(s_min, f_min, strict=True)
    ):
        return pd.Series(fares["fare_stage_label"].to_numpy(), index=stops.index, dtype=object)
    counts = fares["minutes"].value_counts()
    by_min = fares.drop_duplicates("minutes").set_index("minutes")["fare_stage_label"]
    for idx, minutes in zip(stops.index, s_min, strict=True):
        if not math.isnan(minutes) and counts.get(minutes, 0) == 1:
            out[idx] = by_min[minutes]
    return out


def recompute_times(cum_min: list[float], keep: list[bool]) -> list[float]:
    """time_to_next_min over the kept stops of one direction, bridging dropped stops.

    `cum_min` are cumulative minutes from the first stop, in stop order; `keep` marks the
    stops that stay. Returns one value per kept stop: cum(next kept) - cum(this), NaN on
    the last kept stop or where either value is missing.
    """
    kept = [c for c, k in zip(cum_min, keep, strict=True) if k]
    out = []
    for i, c in enumerate(kept):
        if i + 1 == len(kept) or c is None or kept[i + 1] is None or math.isnan(c) or math.isnan(kept[i + 1]):
            out.append(math.nan)
        else:
            out.append(float(kept[i + 1] - c))
    return out


# --- names --------------------------------------------------------------------

ABBREVIATIONS = {
    "st": "saint",
    "ste": "sainte",
    "govt": "government",
    "gov": "government",
    "rd": "road",
    "rds": "roads",
    "jn": "junction",
    "jct": "junction",
    "jnct": "junction",
    "junc": "junction",
    "mt": "mount",
    "pte": "pointe",
    "sch": "school",
    "ssr": "ssr",
    "av": "avenue",
    "ave": "avenue",
    "hosp": "hospital",
    "stn": "station",
    "pol": "police",
    "rbt": "roundabout",
    "lakaz": "lakaz",
    "n": "n",
}
PHRASES = [
    (r"\bround\s*about\b", "roundabout"),
    (r"\bs\s*s\s*r\b", "ssr"),
    (r"\bcoll\b", "college"),
    (r"\bc\s*e\s*b\b", "ceb"),
]
STOPWORDS = {
    "de", "du", "des", "la", "le", "les", "l", "d", "the", "of", "aux", "a", "and", "et", "en",
    "near", "opposite", "opp", "bus", "stop", "shelter", "terminal", "down", "up",
}  # fmt: skip
GENERIC = {
    "road", "roads", "street", "avenue", "lane", "junction", "school", "government", "police", "station",
    "church", "temple", "mosque", "college", "village", "hall", "roundabout", "market", "hospital", "centre",
    "center", "traffic", "bridge", "saint", "sainte", "royal", "main", "new", "old", "primary",
    "secondary", "state", "public", "beach", "post", "office", "branch", "estate", "cite", "camp",
    "factory", "industry", "industries", "limited", "ltd", "company", "shopping", "store", "shop", "house",
    "garage", "hotel", "supermarket", "terminus", "beginning", "end", "filling", "parking", "health",
    "community", "social", "welfare", "club", "ground", "park", "residence", "morcellement",
}  # fmt: skip


def normalise(text: str) -> str:
    """Lower case ASCII words: accents and punctuation removed, common abbreviations expanded.

    "Mare d'Albert (Govt. School)" -> "mare d albert government school";
    "C. Fouquereaux (Round about)" -> "c fouquereaux roundabout".
    """
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    for pattern, repl in PHRASES:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    words = [ABBREVIATIONS.get(w, w) for w in text.split()]
    return " ".join(words)


def tokens(text: str) -> list[str]:
    """Normalised words without stop words."""
    return [w for w in normalise(text).split() if w not in STOPWORDS]


def distinctive(text: str) -> set[str]:
    """Tokens that are neither stop words nor generic (road, school, ...)."""
    return {w for w in tokens(text) if w not in GENERIC and len(w) > 1}


def similarity(a: str, b: str) -> float:
    """Name similarity in [0, 1] on the normalised token strings (order-insensitive)."""
    return similarity_tokens(tokens(a), tokens(b))


def similarity_tokens(ta: list[str], tb: list[str]) -> float:
    """similarity() on token lists that are already normalised."""
    if not ta or not tb:
        return 0.0
    sa, sb = " ".join(sorted(ta)), " ".join(sorted(tb))
    ratio = SequenceMatcher(None, sa, sb).ratio()
    set_a, set_b = set(ta), set(tb)
    if not (set_a <= set_b or set_b <= set_a):
        return ratio
    # One name's words are all in the other ("Place Margeot" in "Place Taxi Margeot"): a
    # bonus that grows with the share of the longer name's distinctive words covered.
    shared = {w for w in set_a & set_b if w not in GENERIC and len(w) > 1}
    longer = {w for w in max(set_a, set_b, key=len) if w not in GENERIC and len(w) > 1}
    if not shared or not longer:
        return ratio
    return max(ratio, 0.8 + 0.1 * len(shared) / len(longer))


def split_stop_name(name: str, village: str = "") -> dict:
    """Split a stop name into an area part and a landmark part.

    "Rose Belle (Gebert)" -> area "Rose Belle", landmark "Gebert";
    "Cassis - Royal College / Brabant" -> area "Cassis", landmark "Royal College / Brabant";
    "Junction Mount Ory / Trunk Road" -> junction ("Mount Ory", "Trunk Road");
    "Highlands" -> area "Highlands", landmark "".
    The village from the stop page is removed from the front of the name when present.
    """
    text = re.sub(r"\s+", " ", name or "").strip()
    out = {"area": "", "landmark": "", "junction": None}
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", text)
    if m:
        out["area"], out["landmark"] = m.group(1).strip(), m.group(2).strip()
    elif " - " in text:
        head, tail = text.split(" - ", 1)
        out["area"], out["landmark"] = head.strip(), tail.strip()
    else:
        out["area"] = text
    if village and not out["landmark"] and normalise(text).startswith(normalise(village) + " "):
        rest = text[len(village) :].strip(" -,")
        if rest:
            out["area"], out["landmark"] = village, rest
    jtext = out["landmark"] or out["area"]
    jm = re.match(r"^(?:junction|jn\.?|jct\.?|jnct\.?)\s*(?:of\s+)?(.+?)\s*(?:/|&|\band\b)\s*(.+)$", jtext, flags=re.I)
    if jm:
        first, second = jm.group(1).strip(), jm.group(2).strip()
        # "Junction Rouillard & Engrais Martial Roads": a plural road type applies to both
        # parts; "Junction Mount Ory / Trunk Road": a singular "Road" too, when the first
        # part has no road type of its own.
        kind = re.search(r"\b(road|street|avenue|lane)(s?)$", second, flags=re.I)
        has_type = re.search(r"\b(road|street|avenue|lane|rd|st|ave)\.?$", first, flags=re.I)
        if kind and not has_type and (kind.group(2) or kind.group(1).lower() == "road"):
            singular = kind.group(1).title()
            first = f"{first} {singular}"
            second = second[: kind.start()] + singular
        out["junction"] = (first, second)
    return out


# --- geometry -----------------------------------------------------------------


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Straight-line distance is shorter than road distance: the speed check uses a detour
# factor, a floor on the scheduled minutes, and a small distance allowance.
ROAD_DETOUR = 1.3
MAX_SPEED_KPH = 80.0


def implied_speed_kph(distance_m: float, minutes: float, detour: float = ROAD_DETOUR) -> float:
    """Road-equivalent speed for a straight-line distance covered in `minutes` (floor 0.5 min)."""
    minutes = max(minutes if minutes == minutes else 0.0, 0.5)
    return distance_m * detour / 1000.0 / (minutes / 60.0)


def implausible_jump(
    distance_m: float, minutes: float, max_kph: float = MAX_SPEED_KPH, slack_m: float = 1000.0
) -> bool:
    """True if a hop is too fast for the timetable.

    Straight-line speed (no detour factor: the site's per-stop minutes are partly
    interpolated between timing points) over at least 1 minute, after removing a slack
    for location error, above `max_kph`.
    """
    speed = implied_speed_kph(max(distance_m - slack_m, 0.0), max(minutes if minutes == minutes else 0.0, 1.0), 1.0)
    return speed > max_kph


def zigzag(d_prev: float, d_next: float, d_skip: float, slack_m: float = 1500.0) -> bool:
    """True if a middle stop sits far off the line between its neighbours."""
    return d_prev + d_next > 2.0 * d_skip + slack_m


def interpolate_xy(t_a: float, xy_a, t_b: float, xy_b, t: float) -> tuple[float, float]:
    """Point between two located stops in proportion to scheduled minutes (midpoint if equal times)."""
    frac = 0.5 if t_b == t_a else min(max((t - t_a) / (t_b - t_a), 0.0), 1.0)
    return (xy_a[0] + frac * (xy_b[0] - xy_a[0]), xy_a[1] + frac * (xy_b[1] - xy_a[1]))


# --- geocoder -----------------------------------------------------------------

# location_quality levels (1 = most confident) and their sources.
QUALITY = {
    1: "named OSM bus stop or bus station matching the stop name in the stop's village",
    2: "named OSM feature (amenity, shop, building, landmark) or junction of two named roads in the village",
    3: "OSM place node or named road matching the stop name, or one of 2-3 features of the named kind",
    4: "village centroid (OSM place node or Village Council Area / ward polygon), or interpolated along "
    "the route between located neighbours; also any stop still in conflict with the timetable",
}

PLACE_RADIUS_M = {
    "city": 3000, "town": 2000, "suburb": 1200, "village": 1500, "quarter": 800, "neighbourhood": 700,
    "hamlet": 700, "locality": 700, "isolated_dwelling": 300, "square": 200,
}  # fmt: skip
PLACE_RANK = {"city": 0, "town": 1, "village": 2, "suburb": 3, "quarter": 4, "neighbourhood": 5, "hamlet": 6}

# Landmark words that name a kind of feature, and the OSM tags that identify it.
CATEGORY_WORDS = {
    "police": "police",
    "school": "school",
    "college": "school",
    "church": "church",
    "chapel": "church",
    "temple": "temple",
    "kovil": "temple",
    "mandir": "temple",
    "shivala": "temple",
    "mosque": "mosque",
    "hospital": "hospital",
    "market": "market",
    "post": "post_office",
    "hall": "hall",
    "cemetery": "cemetery",
    "stadium": "stadium",
    "beach": "beach",
    "traffic": "bus_station",
    "terminal": "bus_station",
}


def osm_category(tags: dict) -> str:
    """Coarse feature category of an OSM object, matching CATEGORY_WORDS values ('' if none)."""
    amenity, religion = tags.get("amenity", ""), tags.get("religion", "")
    if amenity == "police":
        return "police"
    if amenity in ("school", "college", "kindergarten"):
        return "school"
    if amenity == "place_of_worship":
        return {"christian": "church", "hindu": "temple", "muslim": "mosque"}.get(religion, "worship")
    if amenity in ("hospital", "clinic"):
        return "hospital"
    if amenity == "marketplace":
        return "market"
    if amenity == "post_office":
        return "post_office"
    if amenity in ("community_centre", "townhall", "social_centre"):
        return "hall"
    if amenity == "bus_station":
        return "bus_station"
    if amenity == "grave_yard" or tags.get("landuse") == "cemetery":
        return "cemetery"
    if tags.get("leisure") == "stadium":
        return "stadium"
    if tags.get("natural") == "beach" or tags.get("leisure") == "beach_resort":
        return "beach"
    return ""


def admin_core_name(name: str) -> str:
    """'Town of Port-Louis, Ward 2, West' -> 'Port-Louis'; 'Mahébourg VCA' -> 'Mahébourg'."""
    text = re.sub(r"^\s*(town|city|municipality) of\s+", "", name or "", flags=re.I)
    text = re.sub(r",\s*ward\b.*$", "", text, flags=re.I)
    text = re.sub(r"\bVCA\b.*$", "", text).strip(" ,")
    text = re.sub(r",\s*(north|south|east|west|central)\s*$", "", text, flags=re.I)
    return text.strip()
