"""Tests for transport.open_bus (no network, no OSM file)."""

import math

import pandas as pd
import pytest

from transport import open_bus

LINE_PAGE = """<!doctype html><html><head>
<link rel="canonical" href="https://www.mauritius-buses.com/line/198/"></head><body>
<main class="wrap">
 <div class="line-head">
  <p class="line-chip"><span class="chip chip-lg">198</span></p>
  <h1>Rte No. 198</h1>
  <dl class="facts">
   <dt>Operated by</dt><dd>BOCS (S) &amp; UBS</dd>
   <dt>Route</dt><dd>Port Louis (Victoria Square) <span class="to">via</span> Airport
     <span class="to">to</span> Mahebourg Traffic Centre</dd>
   <dt>Operating days</dt><dd>all days</dd>
   <dt>Average frequency</dt><dd>16 minutes</dd>
  </dl>
 </div>
 <section class="line-stops"><h2>Stops</h2><div class="stop-seqs">
  <div class="stop-seq"><h3>Towards Mahebourg Traffic Centre</h3><ol>
   <li><a href="/stop/58/">Port Louis (Victoria Square)</a></li>
   <li><a href="/stop/411/">Cassis - Royal College / Brabant</a> <span class="muted">2 min</span></li>
   <li><a href="/stop/551/">Mahebourg Traffic Centre</a> <span class="muted">85 min</span></li>
  </ol></div>
  <div class="stop-seq"><h3>Towards Port Louis (Victoria Square)</h3><ol>
   <li><a href="/stop/551/">Mahebourg Traffic Centre</a></li>
   <li><a href="/stop/58/">Port Louis (Victoria Square)</a> <span class="muted">85 min</span></li>
  </ol></div>
 </div></section>
 <section class="timetable"><h2>Official bus schedule</h2>
  <table class="timetable-table"><thead><tr><th>Days</th><th>From</th><th>1st bus</th><th>Last bus</th></tr></thead>
   <tbody><tr><td>Weekdays</td><td>Port Louis</td><td>06h15</td><td>18h45</td></tr>
   <tr><td>Weekdays</td><td>Mahebourg</td><td>05h10</td><td>18h10</td></tr></tbody></table>
  <ul><li>Weekday frequency</li><li>Direction 1 · 06h05 · 08h15 · 09h00</li></ul>
  <h3>Direction 1</h3>
  <table class="timetable-table"><thead><tr><th>Fare stage</th><th>Locality</th><th>Minutes</th></tr></thead>
   <tbody><tr><td>0</td><td>Port Louis</td><td>0</td></tr>
   <tr><td>1</td><td>Cassis (Royal College)</td><td></td></tr>
   <tr><td>29A</td><td>Mahebourg (Bus Station)</td><td>85</td></tr></tbody></table>
  <a href="https://nlta.govmu.org/x/timer5.pdf#page=1">scan</a>
 </section>
</main></body></html>"""

STOP_PAGE = """<html><head><meta property="og:url" content="https://www.mauritius-buses.com/stop/1/"></head>
<body><main class="wrap"><h1 class="page-title">Port Louis (Immigration Square)</h1>
<p class="muted">Bus stop in Port Louis. 59 line directions board here. <a href="/?from=1">Plan a trip</a></p>
<table class="lines stop-lines"><tbody>
<tr><td class="c"><a class="chip" href="/line/19/">19</a></td><td>towards X</td></tr>
<tr><td class="c"><a class="chip" href="/line/24-24A/">24/24A</a></td><td>towards Y</td></tr>
</tbody></table></main></body></html>"""


def test_parse_line_page():
    page = open_bus.parse_line_page(LINE_PAGE)
    line = page["line"]
    assert line["route_id"] == "198"
    assert line["operator"] == "BOCS (S) & UBS"
    assert line["origin"] == "Port Louis (Victoria Square)"
    assert line["via"] == "Airport"
    assert line["destination"] == "Mahebourg Traffic Centre"
    assert line["average_frequency_min"] == 16.0
    assert line["timetable_scan_url"].endswith("timer5.pdf#page=1")
    stops = pd.DataFrame(page["stops"])
    assert stops.groupby("direction").size().tolist() == [3, 2]
    first = stops[stops["direction"] == 0]
    assert first["site_stop_id"].tolist() == ["58", "411", "551"]
    assert first["cum_min"].tolist() == [0.0, 2.0, 85.0]
    assert first["stop_sequence"].tolist() == [1, 2, 3]
    assert page["schedule"][0] == {
        "days": "Weekdays",
        "from": "Port Louis",
        "first_bus_min": 375.0,
        "last_bus_min": 1125.0,
    }
    assert page["departures"] == {1: [365.0, 495.0, 540.0]}
    fares = pd.DataFrame(page["fares"])
    assert fares["fare_stage_label"].tolist() == ["0", "1", "29A"]
    # Row-by-row alignment: same length, minutes agree where the table gives them.
    labels = open_bus.align_fare_stages(first, fares)
    assert labels.tolist() == ["0", "1", "29A"]
    assert [open_bus.fare_stage_number(x) for x in labels] == [0.0, 1.0, 29.0]


def test_stop_links_and_sitemap():
    assert open_bus.stop_links(LINE_PAGE) == ["58", "411", "551"]
    xml = "<urlset><url><loc>https://x/line/1A/</loc></url><url><loc>https://x/stop/12/</loc></url></urlset>"
    assert open_bus.parse_sitemap(xml) == (["1A"], ["12"])
    assert open_bus.line_cache_name("24-24A") == "line/24-24A.html"


def test_parse_stop_page():
    stop = open_bus.parse_stop_page(STOP_PAGE)
    assert stop["site_stop_id"] == "1"
    assert stop["name"] == "Port Louis (Immigration Square)"
    assert stop["village"] == "Port Louis"
    assert stop["line_directions"] == 59
    assert stop["lines"] == "19|24/24A"
    no_village = STOP_PAGE.replace("Bus stop in Port Louis. ", "")
    assert open_bus.parse_stop_page(no_village)["village"] == ""


def test_headway_rule():
    assert open_bus.headway_min(16.0, math.nan) == (16.0, "average_frequency")
    assert open_bus.headway_min(math.nan, math.nan, 600.0, 45.0) == (45.0, "departure_list_gaps")
    # 6 departures between 06:00 and 18:00 -> every 12 / 5 hours.
    assert open_bus.headway_min(math.nan, 6.0, 720.0) == (144.0, "times_per_day_over_span")
    assert open_bus.headway_min(math.nan, 4.0) == (180.0, "times_per_day_over_12h")
    value, rule = open_bus.headway_min(math.nan, math.nan)
    assert math.isnan(value) and rule == "none"
    schedule = [
        {"days": "Weekdays", "from": "A", "first_bus_min": 360.0, "last_bus_min": 1080.0},
        {"days": "Weekdays", "from": "B", "first_bus_min": 420.0, "last_bus_min": 1020.0},
        {"days": "Saturdays", "from": "A", "first_bus_min": 0.0, "last_bus_min": 1400.0},
    ]
    assert open_bus.operating_span_min(schedule) == 660.0
    assert open_bus.departure_gap_min({1: [360.0, 420.0, 540.0]}) == 90.0


def test_gap_recompute():
    cum = [0.0, 2.0, 5.0, 9.0, 12.0]
    assert open_bus.recompute_times(cum, [True] * 5) == [2.0, 3.0, 4.0, 3.0, pytest.approx(math.nan, nan_ok=True)]
    # Stops 2 and 4 (index 1 and 3) dropped: times bridge the gaps; last stays blank.
    out = open_bus.recompute_times(cum, [True, False, True, False, True])
    assert out[:2] == [5.0, 7.0] and math.isnan(out[2])
    # A dropped last stop: the new last stop gets blank.
    out = open_bus.recompute_times(cum, [True, True, True, True, False])
    assert out[:3] == [2.0, 3.0, 4.0] and math.isnan(out[3])
    # Missing minutes give blank times.
    out = open_bus.recompute_times([0.0, math.nan, 4.0], [True, True, True])
    assert math.isnan(out[0]) and math.isnan(out[1])


def test_normaliser_and_similarity():
    assert open_bus.normalise("Mare d'Albert (Govt. School)") == "mare d albert government school"
    assert open_bus.normalise("C. Fouquereaux (Round about)") == "c fouquereaux roundabout"
    assert open_bus.normalise("Rivière du Poste") == "riviere du poste"
    assert open_bus.normalise("St. Pierre / Ste. Croix") == "saint pierre sainte croix"
    assert open_bus.tokens("Plaine Magnien (Police Station)") == ["plaine", "magnien", "police", "station"]
    assert open_bus.distinctive("Plaine Magnien (Police Station)") == {"plaine", "magnien"}
    assert open_bus.similarity("Pont Fer", "Pont-Fer") == 1.0
    assert open_bus.similarity("Crève Coeur", "Creve Coeur") == 1.0
    assert open_bus.similarity("Place Margeot", "Place Taxi Margeot") >= 0.85
    # A generic shared word is not a match.
    assert open_bus.similarity("Overseas Gloves Factory", "The Factory") < 0.8
    assert open_bus.similarity("Rose Belle", "Mahebourg") < 0.5


def test_split_stop_name():
    assert open_bus.split_stop_name("Rose Belle (Gebert)")["landmark"] == "Gebert"
    parts = open_bus.split_stop_name("Cassis - Royal College / Brabant")
    assert (parts["area"], parts["landmark"]) == ("Cassis", "Royal College / Brabant")
    assert open_bus.split_stop_name("Junction Mount Ory / Trunk Road")["junction"] == ("Mount Ory Road", "Trunk Road")
    assert open_bus.split_stop_name("Junction Rouillard & Engrais Martial Roads")["junction"] == (
        "Rouillard Road",
        "Engrais Martial Road",
    )
    assert open_bus.split_stop_name("Highlands") == {"area": "Highlands", "landmark": "", "junction": None}


def test_route_checks():
    assert not open_bus.implausible_jump(3000.0, 4.0)  # 2 km after slack in 4 min = 30 km/h
    assert open_bus.implausible_jump(20000.0, 2.0)
    assert open_bus.zigzag(5000.0, 5000.0, 500.0)
    assert not open_bus.zigzag(1000.0, 1000.0, 2000.0)
    assert open_bus.interpolate_xy(0.0, (0.0, 0.0), 10.0, (10.0, 20.0), 5.0) == (5.0, 10.0)
    assert open_bus.admin_core_name("Town of Port-Louis, Ward 2, West") == "Port-Louis"
    assert open_bus.admin_core_name("Rivière du Poste VCA, East") == "Rivière du Poste"
