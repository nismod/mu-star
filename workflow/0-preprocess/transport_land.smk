"""Land transport, part 1 of 2: zones, networks and journey-to-work demand.

DRAFT. The rule structure, file locations and wildcards are settled enough to
build on. Several scripts are drafts (marked TODO) until the input data and
the open questions in docs/src/infrastructure-transport.md are resolved.

Flow of data (national inputs at the top; everything from zones onwards is
per island):

    census CA27 district OD ─────────────────────────────────────┐ calibration / validation
    census zone table + Appendix II districts + boundaries ──┐   │
    WorldPop ────────────────────────────────────────────────┼─> residents per H3 cell ─┐
    economic_activity_h3 (output by sector) ─────────────────┴─> jobs per H3 cell ──────┼─> zones {island}
                                                                                        │
    roads ──> road network ──┬─> bus network ──┐                                        │
    bus stops/routes/times ──┘                 ├─> multimodal network (+ zone connectors)
    metro stations/lines ──> metro network ────┘                                        │
              road network ──> car base network ──> road skim ─> radiation OD ─> demand by mode

    Part 2 (workflow/2-losses/transport_land.smk): baseline assignment, criticality.

Wildcards:
    {data}    data root (normally "data")
    {island}  "mauritius" or "rodrigues": separate study areas (no commuting
              between islands; inter-island flows: transport_interisland.smk)
    {zoning}  zone system for OD and connectors: "h3-r8" (H3 resolution 8).
              Other resolutions work the same way, e.g. "h3-r7".
    {scenario} sensitivity scenario (transport_land.scenarios, e.g. "central",
              "commute-low"), from the multimodal network onwards. Zones, OD
              estimation and the road skim are shared by all scenarios.

Run all of part 1 for Mauritius with:
    snakemake -c4 data/processed/transport/mauritius/demand/h3-r8/central/demand-by-mode.parquet
"""

from pathlib import Path


configfile: "config/transport/transport.yaml"


TL = config["transport_land"]
TL_INPUTS = TL["inputs"]
TL_ISLANDS = TL["islands"]
TL_CONFIG = "config/transport/transport.yaml"
TL_SCRIPTS = "workflow/0-preprocess"

TL_NATIONAL = "{data}/processed/transport/national"
TL_ISLAND = "{data}/processed/transport/{island}"


def tl_input(key):
    """Path of a national input named in config, under the data root."""
    return "{data}/" + TL_INPUTS[key]


def tl_optional_input(key):
    """Input function: a national input if configured and present, else no input."""

    def resolve(wc):
        rel = TL_INPUTS.get(key)
        path = f"{wc.data}/{rel}" if rel else None
        return [path] if path and Path(path).exists() else []

    return resolve


def tl_island_input(key):
    """Input function: an island's input from config (none if null)."""

    def resolve(wc):
        rel = TL_ISLANDS[wc.island]["inputs"].get(key)
        return [f"{wc.data}/{rel}"] if rel else []

    return resolve


def tl_island_optional(key):
    """Input function: an island's optional input, if configured and present."""

    def resolve(wc):
        rel = TL_ISLANDS[wc.island]["inputs"].get(key)
        path = f"{wc.data}/{rel}" if rel else None
        return [path] if path and Path(path).exists() else []

    return resolve


def tl_flag(name):
    """Shell fragment '--name path' for an optional single input, or nothing."""

    def resolve(wc, input):
        values = input.get(name.replace("-", "_"), [])
        values = [values] if isinstance(values, str) else list(values)
        return f"--{name} {values[0]}" if values else ""

    return resolve


# --- Population (national) ---------------------------------------------------------


rule transport_census_zones:
    """
    Employed residents by census area (2022 census Table CA1: Municipal Wards,
    Village Council Areas, Rodrigues regions), matched by name to the official
    codes (Appendices II-IV), with district and island from the code, joined
    to the area boundaries. Aggregate rows are dropped.

    Test with:
    snakemake -c1 data/processed/transport/national/population/census-zones.geoparquet
    """
    input:
        table=tl_input("census_table"),
        codes=tl_input("census_zone_codes"),
        boundaries=tl_input("census_boundaries"),
        script=f"{TL_SCRIPTS}/transport_census_zones.py",
    output:
        zones=f"{TL_NATIONAL}/population/census-zones.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --table {input.table} --codes {input.codes} --boundaries {input.boundaries} \
            --output {output.zones}
        """


rule transport_census_district_od:
    """
    Parse census Table CA27 (2022; employed population by district of residence
    and of workplace) into a nine-by-nine district table, the calibration and
    validation data for the radiation model. The three non-district workplace
    columns are kept in the summary.

    Test with:
    snakemake -c1 data/processed/transport/national/population/census-district-od.parquet
    """
    input:
        table=tl_input("census_district_od"),
        script=f"{TL_SCRIPTS}/transport_census_district_od.py",
    output:
        od=f"{TL_NATIONAL}/population/census-district-od.parquet",
        summary=f"{TL_NATIONAL}/population/census-district-od-summary.json",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --table {input.table} \
            --output {output.od} --output-summary {output.summary}
        """


rule transport_scale_worldpop:
    """
    Compare WorldPop with the census totals per census zone and write WorldPop
    rescaled so that each zone matches its official total. The check table is
    for review: large ratios point to boundary or vintage mismatches.

    Test with:
    snakemake -c1 data/processed/transport/national/population/worldpop-scaled.tif
    """
    input:
        worldpop=tl_input("worldpop"),
        zones=rules.transport_census_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_scale_worldpop.py",
    output:
        raster=f"{TL_NATIONAL}/population/worldpop-scaled.tif",
        check=f"{TL_NATIONAL}/population/worldpop-census-check.csv",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --worldpop {input.worldpop} --zones {input.zones} \
            --output-raster {output.raster} --output-check {output.check}
        """


rule transport_residents_h3:
    """
    Aggregate scaled population to H3 cells. Employed residents per cell =
    cell population x the employed/population ratio of the census zone that
    each pixel falls in.

    Test with:
    snakemake -c1 data/processed/transport/national/zones/h3-r8/residents.parquet
    """
    input:
        raster=rules.transport_scale_worldpop.output.raster,
        zones=rules.transport_census_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_residents_h3.py",
    output:
        residents=f"{TL_NATIONAL}/zones/{{zoning}}/residents.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --zoning {wildcards.zoning} \
            --raster {input.raster} --zones {input.zones} \
            --output {output.residents}
        """


rule transport_jobs_h3:
    """
    Turn the H3 economic activity layer (output by sector, wide) into jobs
    per cell: output / output-per-worker by sector where the productivity
    table is available. National; scaled per island in transport_zones.

    Test with:
    snakemake -c1 data/processed/transport/national/zones/h3-r8/jobs.parquet
    """
    input:
        activity=rules.economic_activity_h3.output.activity,
        output_per_worker=tl_optional_input("output_per_worker"),
        script=f"{TL_SCRIPTS}/transport_jobs_h3.py",
    output:
        jobs=f"{TL_NATIONAL}/zones/{{zoning}}/jobs.parquet",
    params:
        output_per_worker=tl_flag("output-per-worker"),
    shell:
        """
        python {input.script} --config {TL_CONFIG} --zoning {wildcards.zoning} \
            --activity {input.activity} {params.output_per_worker} \
            --output {output.jobs}
        """


# --- Zones (per island) --------------------------------------------------------------


rule transport_zones:
    """
    One table of analysis zones for an island: H3 cells with residents,
    employed residents, jobs (scaled to the island's employed residents), a
    population-weighted centroid, and the census zone and district of the cell
    centre. Cells with no residents and no jobs are dropped.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/zones/h3-r8/zones.geoparquet
    """
    input:
        residents=rules.transport_residents_h3.output.residents,
        jobs=rules.transport_jobs_h3.output.jobs,
        census_zones=rules.transport_census_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_zones.py",
    output:
        zones=f"{TL_ISLAND}/zones/{{zoning}}/zones.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --island {wildcards.island} \
            --residents {input.residents} --jobs {input.jobs} \
            --census-zones {input.census_zones} \
            --output {output.zones}
        """


# --- Networks (per island) -------------------------------------------------------------


rule transport_road_network:
    """
    Clean the provided road network: check topology and ids, fill speed and
    capacity defaults by road class, and compute length and free-flow time.
    Output edges are undirected (one row per physical edge, with a oneway flag);
    the multimodal build expands them to directed links.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/road/edges.geoparquet
    """
    input:
        roads=tl_island_input("roads"),
        script=f"{TL_SCRIPTS}/transport_road_network.py",
    output:
        edges=f"{TL_ISLAND}/networks/road/edges.geoparquet",
        nodes=f"{TL_ISLAND}/networks/road/nodes.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --roads {input.roads} \
            --output-edges {output.edges} --output-nodes {output.nodes}
        """


rule transport_bus_network:
    """
    Build bus route segments (stop to stop, per route and direction) with
    scheduled between-stop times and route frequencies. Each segment records
    the road edges it runs over, so that a road failure also removes the bus
    segments that use it. Segment capacity is route frequency x vehicle
    capacity over the modelled period.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/bus/segments.geoparquet
    """
    input:
        stops=tl_island_input("bus_stops"),
        routes=tl_island_input("bus_routes"),
        route_stops=tl_island_input("bus_route_stops"),
        road_edges=rules.transport_road_network.output.edges,
        road_nodes=rules.transport_road_network.output.nodes,
        script=f"{TL_SCRIPTS}/transport_bus_network.py",
    output:
        stops=f"{TL_ISLAND}/networks/bus/stops.geoparquet",
        segments=f"{TL_ISLAND}/networks/bus/segments.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --stops {input.stops} --routes {input.routes} --route-stops {input.route_stops} \
            --road-edges {input.road_edges} --road-nodes {input.road_nodes} \
            --output-stops {output.stops} --output-segments {output.segments}
        """


rule transport_metro_network:
    """
    Build metro segments (station to station on each line, both directions)
    with run times and capacity from line headway and vehicle capacity. Islands
    with no metro get empty outputs.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/metro/segments.geoparquet
    """
    input:
        stations=tl_island_input("metro_stations"),
        lines=tl_island_input("metro_lines"),
        line_stations=tl_island_input("metro_line_stations"),
        script=f"{TL_SCRIPTS}/transport_metro_network.py",
    output:
        stations=f"{TL_ISLAND}/networks/metro/stations.geoparquet",
        segments=f"{TL_ISLAND}/networks/metro/segments.geoparquet",
    params:
        stations=tl_flag("stations"),
        lines=tl_flag("lines"),
        line_stations=tl_flag("line-stations"),
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            {params.stations} {params.lines} {params.line_stations} \
            --output-stations {output.stations} --output-segments {output.segments}
        """


rule transport_bus_detours:
    """
    For each road edge that buses use, failed on its own: how each bus route
    over it detours between stops, which stops it skips, and where it is cut
    (src/transport/detours.py). Uses the base settings for the island
    (criticality.bus_detour, bus.speed_factor), which scenarios may not
    change, so it runs once for all scenarios.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/bus/detours.parquet
    """
    input:
        road_edges=rules.transport_road_network.output.edges,
        bus_stops=rules.transport_bus_network.output.stops,
        bus_segments=rules.transport_bus_network.output.segments,
        script=f"{TL_SCRIPTS}/transport_bus_detours.py",
    output:
        detours=f"{TL_ISLAND}/networks/bus/detours.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --island {wildcards.island} \
            --road-edges {input.road_edges} --bus-stops {input.bus_stops} \
            --bus-segments {input.bus_segments} --output {output.detours}
        """


rule transport_multimodal_network:
    """
    Combine road, bus and metro into one directed network for transport_flow_model,
    and connect the zones to it:

    - zone/<id>/car centroids join road nodes (car connectors);
    - zone/<id>/bus centroids walk to bus stops, and zone/<id>/metro
      centroids walk to metro stations;
    - boarding and alighting links carry waiting time and fares. Each stop and
      station has a departure side (walk in, board) and an arrival side
      (alight, walk out), so no trip can walk through a stop without riding.

    Car, bus and metro trips start and end at different centroids, and bus and
    metro do not connect, so one assignment keeps the modes apart without a
    mode attribute in the router.

    Also writes the failure units: one row per physical asset that can fail
    (road edge, metro segment, metro station), listing every network link it
    removes.

    Network costs and capacities depend on the {scenario}. Bus detours come
    from transport_bus_detours (the same for every scenario); the fares on
    detoured bus links use the scenario's fares.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/multimodal/h3-r8/central/links.parquet
    """
    input:
        road_edges=rules.transport_road_network.output.edges,
        road_nodes=rules.transport_road_network.output.nodes,
        bus_stops=rules.transport_bus_network.output.stops,
        bus_segments=rules.transport_bus_network.output.segments,
        bus_detours=rules.transport_bus_detours.output.detours,
        metro_stations=rules.transport_metro_network.output.stations,
        metro_segments=rules.transport_metro_network.output.segments,
        zones=rules.transport_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_multimodal_network.py",
    output:
        links=f"{TL_ISLAND}/networks/multimodal/{{zoning}}/{{scenario}}/links.parquet",
        nodes=f"{TL_ISLAND}/networks/multimodal/{{zoning}}/{{scenario}}/nodes.geoparquet",
        failure_units=f"{TL_ISLAND}/networks/multimodal/{{zoning}}/{{scenario}}/failure-units.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --island {wildcards.island} --scenario {wildcards.scenario} \
            --road-edges {input.road_edges} --road-nodes {input.road_nodes} \
            --bus-stops {input.bus_stops} --bus-segments {input.bus_segments} \
            --bus-detours {input.bus_detours} \
            --metro-stations {input.metro_stations} --metro-segments {input.metro_segments} \
            --zones {input.zones} \
            --output-links {output.links} --output-nodes {output.nodes} \
            --output-failure-units {output.failure_units}
        """


rule transport_car_network:
    """
    The car base network for the road skim: directed road links and car
    connectors (zone/<id>/car to the nearest road nodes), with free-flow time
    and length. It has no scenario settings, so the demand estimation that
    all scenarios share does not depend on any one scenario. Zones with no
    road node within connectors.car_max_km get no connector and are listed
    in zones-without-car-access.csv.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/networks/car/h3-r8/links.parquet
    """
    input:
        road_edges=rules.transport_road_network.output.edges,
        road_nodes=rules.transport_road_network.output.nodes,
        zones=rules.transport_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_car_network.py",
    output:
        links=f"{TL_ISLAND}/networks/car/{{zoning}}/links.parquet",
        no_access=f"{TL_ISLAND}/networks/car/{{zoning}}/zones-without-car-access.csv",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --island {wildcards.island} \
            --road-edges {input.road_edges} --road-nodes {input.road_nodes} --zones {input.zones} \
            --output-links {output.links} --output-no-access {output.no_access}
        """


# --- Demand (per island) -----------------------------------------------------------------


rule transport_road_skim:
    """
    Free-flow shortest-path time and distance between every pair of zone car
    centroids on the car base network (connectors included): the impedance for
    the radiation model and its calibration.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/demand/h3-r8/road-skim.parquet
    """
    input:
        links=rules.transport_car_network.output.links,
        zones=rules.transport_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_road_skim.py",
    output:
        skim=f"{TL_ISLAND}/demand/{{zoning}}/road-skim.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --links {input.links} --zones {input.zones} --output {output.skim}
        """


def tl_district_od(wc):
    """CA27 district OD for islands that use it (Mauritius), else no input."""
    if TL_ISLANDS[wc.island].get("district_od"):
        return [rules.transport_census_district_od.output.od.format(data=wc.data)]
    return []


rule transport_radiation_od:
    """
    Journey-to-work trips between zones (persons per day, one way, home to
    work) from the radiation model: employed residents produce trips and jobs
    attract them. Trips may stay within a cell; the model decides how many.

    For Mauritius, the radiation settings are calibrated against census Table
    CA27 (calibration.csv) and the result compared with it
    (district-od-comparison.csv, fit in the summary). Scaling to the CA27
    totals (radiation.district_od = "constrain") is the fallback.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/demand/h3-r8/commute-od.parquet
    """
    input:
        zones=rules.transport_zones.output.zones,
        skim=rules.transport_road_skim.output.skim,
        district_od=tl_district_od,
        script=f"{TL_SCRIPTS}/transport_radiation_od.py",
    output:
        od=f"{TL_ISLAND}/demand/{{zoning}}/commute-od.parquet",
        summary=f"{TL_ISLAND}/demand/{{zoning}}/commute-od-summary.json",
        comparison=f"{TL_ISLAND}/demand/{{zoning}}/district-od-comparison.csv",
        calibration=f"{TL_ISLAND}/demand/{{zoning}}/radiation-calibration.csv",
    params:
        district_od=tl_flag("district-od"),
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --island {wildcards.island} --zoning {wildcards.zoning} \
            --zones {input.zones} --skim {input.skim} {params.district_od} \
            --output {output.od} --output-summary {output.summary} \
            --output-comparison {output.comparison} --output-calibration {output.calibration}
        """


def tl_school_od(wc):
    """School OD if school trips are enabled, else no input."""
    if (TL.get("school") or {}).get("enabled"):
        return [rules.transport_school_od.output.od.format(**wc)]
    return []


rule transport_school_children:
    """
    Enrolled school-age children by residence, per H3 cell and school level:
    children by census area and age band (2022 census, source TBC), matched to
    census codes, spread by the scaled WorldPop pattern.

    Test with:
    snakemake -c1 data/processed/transport/national/zones/h3-r8/children.parquet
    """
    input:
        children=lambda wc: f"{wc.data}/{TL['school']['children_table']}",
        codes=tl_input("census_zone_codes"),
        zones=rules.transport_census_zones.output.zones,
        raster=rules.transport_scale_worldpop.output.raster,
        script=f"{TL_SCRIPTS}/transport_school_children.py",
    output:
        children=f"{TL_NATIONAL}/zones/{{zoning}}/children.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --zoning {wildcards.zoning} \
            --children {input.children} --codes {input.codes} --zones {input.zones} \
            --raster {input.raster} --output {output.children}
        """


rule transport_school_od:
    """
    Journeys to school per level (primary, secondary): children by residence
    to school places, with the radiation model on the road skim.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/demand/h3-r8/school-od.parquet
    """
    input:
        zones=rules.transport_zones.output.zones,
        children=rules.transport_school_children.output.children,
        schools=tl_island_input("schools"),
        skim=rules.transport_road_skim.output.skim,
        script=f"{TL_SCRIPTS}/transport_school_od.py",
    output:
        od=f"{TL_ISLAND}/demand/{{zoning}}/school-od.parquet",
        summary=f"{TL_ISLAND}/demand/{{zoning}}/school-od-summary.json",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --island {wildcards.island} --zoning {wildcards.zoning} \
            --zones {input.zones} --children {input.children} --schools {input.schools} \
            --skim {input.skim} --output {output.od} --output-summary {output.summary}
        """


rule transport_mode_split:
    """
    Split trips into walk, cycle, car, bus and metro, by purpose (work, and
    school per level if enabled), scale to the peak period, and write the
    demand table for assignment with origin and destination as network node
    ids (zone/<id>/car, zone/<id>/bus or zone/<id>/metro). Mauritius: national
    shares with bus and metro from reported boardings. Rodrigues: shares by
    distance band. Walk and cycle trips are counted, not assigned.

    Test with:
    snakemake -c1 data/processed/transport/mauritius/demand/h3-r8/central/demand-by-mode.parquet
    """
    input:
        od=rules.transport_radiation_od.output.od,
        school_od=tl_school_od,
        skim=rules.transport_road_skim.output.skim,
        nodes=rules.transport_multimodal_network.output.nodes,
        script=f"{TL_SCRIPTS}/transport_mode_split.py",
    output:
        demand=f"{TL_ISLAND}/demand/{{zoning}}/{{scenario}}/demand-by-mode.parquet",
        summary=f"{TL_ISLAND}/demand/{{zoning}}/{{scenario}}/demand-by-mode-summary.json",
    params:
        school_od=tl_flag("school-od"),
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --island {wildcards.island} --scenario {wildcards.scenario} \
            --od {input.od} {params.school_od} --skim {input.skim} --nodes {input.nodes} \
            --output {output.demand} --output-summary {output.summary}
        """
