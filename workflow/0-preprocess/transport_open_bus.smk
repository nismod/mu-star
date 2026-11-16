"""Open-data fallback for the bus inputs (docs/src/transport-data.md, items 12-14).

Bus stops, routes and timings were to be provided by hand. There is no official
machine-readable source (the NLTA publishes scanned timetables only), so these rules
build the three inputs from the third-party site www.mauritius-buses.com (its data are
transcribed from the official NLTA timetables) and locate the stops with OpenStreetMap:

    www.mauritius-buses.com ──> cached pages ──> lines, line stops, stops, fare stages
    OSM extract (ODbL) ─────────────────────────┴─> located stops ──> bus_stops.gpkg     (item 12)
                                                                   ├─> bus_routes.gpkg    (item 13)
                                                                   └─> bus_route_stops.csv (item 14)

Limits (see each script's docstring): the site has no coordinates, so stop locations are
geocoded from names (location_quality 1-4 per stop; most stops are only located to the
village or the route, typically 0.3-1 km off, and some are worse); per-stop minutes are
partly interpolated by the site between official timing points; fare stages exist only
where the official stage table lines up with the stop list. Replace the outputs by
provided data (NLTA stop coordinates) when available: these rules only write files.

Polite fetching: one request at a time, >= 1.2 s apart, cached on disk and never
refetched, stop on HTTP 403/429; /api and /routing/ are not used (robots.txt disallows
/routing/; llms.txt asks for < 60 requests / 60 s on /api). About 1,080 pages, ~25 min.

Wildcards:
    {data}    data root (normally "data")

Licences and attribution:
    Bus data: www.mauritius-buses.com, from the National Land Transport Authority
        timetables; "Data provided without warranty" (site OpenAPI licence note);
        robots.txt content signals search=yes, ai-input=yes, ai-train=no.
    Locations: (c) OpenStreetMap contributors, ODbL 1.0; bus_stops.gpkg is a Derived
        Database and must carry that attribution.

Run everything with:
    snakemake -c1 transport_open_bus_all
"""

TOB_SCRIPTS = "workflow/0-preprocess"
TOB_PBF = "{data}/incoming/Infrastructure/OpenStreetMap/mauritius-latest.osm.pbf"
TOB_BUS = "{data}/incoming/Infrastructure/Transport/mauritius/bus"
TOB_CACHE = TOB_BUS + "/mauritius-buses.com"
TOB_PARSED = TOB_BUS + "/parsed"
TOB_LOCATED = TOB_BUS + "/located"


rule transport_open_bus_fetch:
    """
    Download the line index, every line page and every stop page of
    www.mauritius-buses.com into the cache (raw HTML, one file per page, manifest.csv
    with URL, status, fetch time, size and hash). Resumable: rerun after an interruption;
    cached pages are never fetched again. Stops with exit code 2 on HTTP 403 / 429.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/bus/mauritius-buses.com/fetch-complete.json
    """
    input:
        script=f"{TOB_SCRIPTS}/transport_open_bus_fetch.py",
    output:
        done=f"{TOB_CACHE}/fetch-complete.json",
    params:
        cache=TOB_CACHE,
    resources:
        mauritius_buses_com=1,
    shell:
        """
        python {input.script} --cache-dir {params.cache} --done {output.done} --delay 1.2
        """


rule transport_open_bus_parse:
    """
    Parse the cached pages into tidy tables: lines (metro flagged), bus line stops per
    direction with cumulative minutes and fare stages, metro station order and minutes,
    stops with village, schedules, and validation.json (line count, two directions per
    line, contiguous sequences, non-decreasing minutes, fare-stage alignment).

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/bus/parsed/validation.json
    """
    input:
        done=f"{TOB_CACHE}/fetch-complete.json",
        script=f"{TOB_SCRIPTS}/transport_open_bus_parse.py",
    output:
        lines=f"{TOB_PARSED}/lines.csv",
        line_stops=f"{TOB_PARSED}/line_stops.csv",
        metro=f"{TOB_PARSED}/metro_line_stations.csv",
        stops=f"{TOB_PARSED}/stops.csv",
        fares=f"{TOB_PARSED}/fare_stages.csv",
        schedule=f"{TOB_PARSED}/schedule.csv",
        validation=f"{TOB_PARSED}/validation.json",
    params:
        cache=TOB_CACHE,
        out=TOB_PARSED,
    shell:
        """
        python {input.script} --cache-dir {params.cache} --output-dir {params.out}
        """


rule transport_open_bus_locate:
    """
    Locate the bus stops from their names and villages with OpenStreetMap (named bus
    stops, features, junctions, roads, places, village / ward polygons), check the
    locations against the timetabled minutes along every route, and snap to OSM bus stop
    nodes. location_quality 1 (named OSM stop) .. 4 (village centroid / interpolated).
    Unlocated and still-conflicting stops go to the review CSV.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/bus/located/stops_located.csv
    """
    input:
        pbf=TOB_PBF,
        stops=f"{TOB_PARSED}/stops.csv",
        line_stops=f"{TOB_PARSED}/line_stops.csv",
        script=f"{TOB_SCRIPTS}/transport_open_bus_locate.py",
    output:
        located=f"{TOB_LOCATED}/stops_located.csv",
        review=f"{TOB_LOCATED}/stops_review.csv",
        summary=f"{TOB_LOCATED}/locate_summary.json",
    shell:
        """
        python {input.script} --pbf {input.pbf} --stops {input.stops} --line-stops {input.line_stops} \
            --output {output.located} --review {output.review} --summary {output.summary}
        """


rule transport_open_bus_write:
    """
    Write the bus workflow inputs in the schemas of items 12-14 (metro excluded):
    bus_stops.gpkg, bus_routes.gpkg (headway_min from the average frequency, else the
    listed departures, else times per day over the operating span) and
    bus_route_stops.csv (time_to_next_min from the cumulative minutes, recomputed across
    unlocated stops; fare_stage from the official stage table). Directions with fewer
    than two located stops are dropped and listed in bus_dropped.csv.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/bus/bus_route_stops.csv
    """
    input:
        lines=f"{TOB_PARSED}/lines.csv",
        line_stops=f"{TOB_PARSED}/line_stops.csv",
        located=f"{TOB_LOCATED}/stops_located.csv",
        script=f"{TOB_SCRIPTS}/transport_open_bus_write.py",
    output:
        stops=f"{TOB_BUS}/bus_stops.gpkg",
        routes=f"{TOB_BUS}/bus_routes.gpkg",
        route_stops=f"{TOB_BUS}/bus_route_stops.csv",
        dropped=f"{TOB_LOCATED}/bus_dropped.csv",
        summary=f"{TOB_LOCATED}/write_summary.json",
    shell:
        """
        python {input.script} --lines {input.lines} --line-stops {input.line_stops} \
            --located {input.located} --output-stops {output.stops} --output-routes {output.routes} \
            --output-route-stops {output.route_stops} --dropped {output.dropped} --summary {output.summary}
        """


rule transport_open_bus_all:
    """
    All bus fallback outputs for Mauritius (Rodrigues buses are not on the site).

    Test with:
    snakemake -c1 transport_open_bus_all
    """
    input:
        "data/incoming/Infrastructure/Transport/mauritius/bus/bus_stops.gpkg",
        "data/incoming/Infrastructure/Transport/mauritius/bus/bus_routes.gpkg",
        "data/incoming/Infrastructure/Transport/mauritius/bus/bus_route_stops.csv",
        "data/incoming/Infrastructure/Transport/mauritius/bus/parsed/metro_line_stations.csv",
