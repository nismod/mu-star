"""Land transport, part 1 of 2: zones, networks and journey-to-work demand.

DRAFT. The rule structure, file locations and wildcards are settled enough to
build on. Several scripts are stubs (marked TODO) until the input data and the
open questions in docs/src/infrastructure-transport.md are resolved.

Flow of data:

    census table + boundaries ──┐
    WorldPop ───────────────────┼─> residents per H3 cell ─┐
    H3 economic activity ───────┴─> jobs per H3 cell ──────┼─> zones
                                                           │
    roads ──> road network ──┬─> bus network ──┐           │
    bus stops/routes ────────┘                 ├─> multimodal network (+ zone connectors)
    metro stations/line ──> metro network ─────┘           │
                                                           ├─> road skim ─> radiation OD ─> demand by mode
                                                           │
    Part 2 (workflow/2-losses/transport_land.smk): baseline assignment, criticality.

Wildcards:
    {data}    data root (normally "data")
    {zoning}  zone system for OD and connectors: "h3-r8" (H3 resolution 8).
              Other resolutions work the same way, e.g. "h3-r7".

Run all of part 1 with:
    snakemake -c4 data/processed/transport/demand/h3-r8/demand-by-mode.parquet
"""

from pathlib import Path


configfile: "config/transport/transport.yaml"


TL = config["transport_land"]
TL_INPUTS = TL["inputs"]
TL_CONFIG = "config/transport/transport.yaml"
TL_SCRIPTS = "workflow/0-preprocess"

TL_PROCESSED = "{data}/processed/transport"


def tl_input(key):
    """Path of a provided input named in config, under the data root."""
    return "{data}/" + TL_INPUTS[key]


# --- Population ---------------------------------------------------------------


rule transport_census_zones:
    """
    Join the census residential-zone table (official population and employed
    residents) to its zone boundaries. Check that every zone has a boundary and
    every boundary a census row.

    Test with:
    snakemake -c1 data/processed/transport/population/census-zones.geoparquet
    """
    input:
        table=tl_input("census_table"),
        boundaries=tl_input("census_boundaries"),
        script=f"{TL_SCRIPTS}/transport_census_zones.py",
    output:
        zones="{data}/processed/transport/population/census-zones.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --table {input.table} --boundaries {input.boundaries} \
            --output {output.zones}
        """


rule transport_scale_worldpop:
    """
    Compare WorldPop with the census totals per census zone and write WorldPop
    rescaled so that each zone matches its official total. The check table is
    for review: large ratios point to boundary or vintage mismatches.

    Test with:
    snakemake -c1 data/processed/transport/population/worldpop-scaled.tif
    """
    input:
        worldpop=tl_input("worldpop"),
        zones=rules.transport_census_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_scale_worldpop.py",
    output:
        raster="{data}/processed/transport/population/worldpop-scaled.tif",
        check="{data}/processed/transport/population/worldpop-census-check.csv",
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
    snakemake -c1 data/processed/transport/zones/h3-r8/residents.parquet
    """
    input:
        raster=rules.transport_scale_worldpop.output.raster,
        zones=rules.transport_census_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_residents_h3.py",
    output:
        residents="{data}/processed/transport/zones/{zoning}/residents.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --zoning {wildcards.zoning} \
            --raster {input.raster} --zones {input.zones} \
            --output {output.residents}
        """


# --- Jobs -----------------------------------------------------------------------


rule transport_jobs_h3:
    """
    Turn the H3 economic activity layer (output by sector, wide) into jobs
    per cell: output / output-per-worker by sector where the productivity
    table is available, then (optionally) scale to total employed residents.

    Test with:
    snakemake -c1 data/processed/transport/zones/h3-r8/jobs.parquet
    """
    input:
        activity=rules.economic_activity_h3.output.activity,
        residents=rules.transport_residents_h3.output.residents,
        script=f"{TL_SCRIPTS}/transport_jobs_h3.py",
    params:
        # Optional input: an empty string means "not available".
        output_per_worker=lambda wc: (
            f"{wc.data}/{TL_INPUTS['output_per_worker']}"
            if TL_INPUTS.get("output_per_worker") and Path(f"{wc.data}/{TL_INPUTS['output_per_worker']}").exists()
            else ""
        ),
    output:
        jobs="{data}/processed/transport/zones/{zoning}/jobs.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --zoning {wildcards.zoning} \
            --activity {input.activity} --residents {input.residents} \
            --output-per-worker "{params.output_per_worker}" \
            --output {output.jobs}
        """


rule transport_zones:
    """
    One table of analysis zones: H3 cells with residents, employed residents,
    jobs, a population-weighted centroid and the census zone they mostly fall in.
    Cells with no residents and no jobs are dropped.

    Test with:
    snakemake -c1 data/processed/transport/zones/h3-r8/zones.geoparquet
    """
    input:
        residents=rules.transport_residents_h3.output.residents,
        jobs=rules.transport_jobs_h3.output.jobs,
        script=f"{TL_SCRIPTS}/transport_zones.py",
    output:
        zones="{data}/processed/transport/zones/{zoning}/zones.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --residents {input.residents} --jobs {input.jobs} \
            --output {output.zones}
        """


# --- Networks ---------------------------------------------------------------------


rule transport_road_network:
    """
    Clean the provided road network: check topology and ids, fill speed and
    capacity defaults by road class, and compute length and free-flow time.
    Output edges are undirected (one row per physical edge, with a oneway flag);
    the multimodal build expands them to directed links.

    Test with:
    snakemake -c1 data/processed/transport/networks/road/edges.geoparquet
    """
    input:
        roads=tl_input("roads"),
        script=f"{TL_SCRIPTS}/transport_road_network.py",
    output:
        edges="{data}/processed/transport/networks/road/edges.geoparquet",
        nodes="{data}/processed/transport/networks/road/nodes.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --roads {input.roads} \
            --output-edges {output.edges} --output-nodes {output.nodes}
        """


rule transport_bus_network:
    """
    Build bus route segments (stop to stop, per route and direction) on the road
    network. Each segment records the road edges it runs over, so that a road
    failure also removes the bus segments that use it. Segment capacity is
    route frequency x vehicle capacity over the modelled period.

    Test with:
    snakemake -c1 data/processed/transport/networks/bus/segments.geoparquet
    """
    input:
        stops=tl_input("bus_stops"),
        routes=tl_input("bus_routes"),
        route_stops=tl_input("bus_route_stops"),
        road_edges=rules.transport_road_network.output.edges,
        road_nodes=rules.transport_road_network.output.nodes,
        script=f"{TL_SCRIPTS}/transport_bus_network.py",
    output:
        stops="{data}/processed/transport/networks/bus/stops.geoparquet",
        segments="{data}/processed/transport/networks/bus/segments.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --stops {input.stops} --routes {input.routes} --route-stops {input.route_stops} \
            --road-edges {input.road_edges} --road-nodes {input.road_nodes} \
            --output-stops {output.stops} --output-segments {output.segments}
        """


rule transport_metro_network:
    """
    Build metro segments (station to station, both directions) with run times
    and capacity from headway and train capacity.

    Test with:
    snakemake -c1 data/processed/transport/networks/metro/segments.geoparquet
    """
    input:
        stations=tl_input("metro_stations"),
        line=tl_input("metro_line"),
        line_stations=tl_input("metro_line_stations"),
        script=f"{TL_SCRIPTS}/transport_metro_network.py",
    output:
        stations="{data}/processed/transport/networks/metro/stations.geoparquet",
        segments="{data}/processed/transport/networks/metro/segments.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --stations {input.stations} --line {input.line} --line-stations {input.line_stations} \
            --output-stations {output.stations} --output-segments {output.segments}
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

    Test with:
    snakemake -c1 data/processed/transport/networks/multimodal/h3-r8/links.parquet
    """
    input:
        road_edges=rules.transport_road_network.output.edges,
        road_nodes=rules.transport_road_network.output.nodes,
        bus_stops=rules.transport_bus_network.output.stops,
        bus_segments=rules.transport_bus_network.output.segments,
        metro_stations=rules.transport_metro_network.output.stations,
        metro_segments=rules.transport_metro_network.output.segments,
        zones=rules.transport_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_multimodal_network.py",
    output:
        links="{data}/processed/transport/networks/multimodal/{zoning}/links.parquet",
        nodes="{data}/processed/transport/networks/multimodal/{zoning}/nodes.geoparquet",
        failure_units="{data}/processed/transport/networks/multimodal/{zoning}/failure-units.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --road-edges {input.road_edges} --road-nodes {input.road_nodes} \
            --bus-stops {input.bus_stops} --bus-segments {input.bus_segments} \
            --metro-stations {input.metro_stations} --metro-segments {input.metro_segments} \
            --zones {input.zones} \
            --output-links {output.links} --output-nodes {output.nodes} \
            --output-failure-units {output.failure_units}
        """


# --- Demand -----------------------------------------------------------------------


rule transport_road_skim:
    """
    Free-flow shortest-path time and distance between every pair of zone car
    centroids on the road network (connectors included). This is the impedance
    for the radiation model and the distance for distance-banded mode shares.

    Test with:
    snakemake -c1 data/processed/transport/demand/h3-r8/road-skim.parquet
    """
    input:
        links=rules.transport_multimodal_network.output.links,
        zones=rules.transport_zones.output.zones,
        script=f"{TL_SCRIPTS}/transport_road_skim.py",
    output:
        skim="{data}/processed/transport/demand/{zoning}/road-skim.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --links {input.links} --zones {input.zones} --output {output.skim}
        """


rule transport_radiation_od:
    """
    Journey-to-work trips between zones (persons per day, one way, home to
    work) from the radiation model: employed residents produce trips and jobs
    attract them.

    Test with:
    snakemake -c1 data/processed/transport/demand/h3-r8/commute-od.parquet
    """
    input:
        zones=rules.transport_zones.output.zones,
        skim=rules.transport_road_skim.output.skim,
        script=f"{TL_SCRIPTS}/transport_radiation_od.py",
    output:
        od="{data}/processed/transport/demand/{zoning}/commute-od.parquet",
        summary="{data}/processed/transport/demand/{zoning}/commute-od-summary.json",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --zones {input.zones} --skim {input.skim} \
            --output {output.od} --output-summary {output.summary}
        """


rule transport_mode_split:
    """
    Split zone-to-zone commuting trips into car, bus, metro and walk using fixed
    shares by distance band, scale to the peak period, and write the demand
    table for assignment with origin and destination as network node ids
    (zone/<id>/car, zone/<id>/bus or zone/<id>/metro). Metro applies only
    where both ends can walk to a station. Walk-only trips are dropped and
    counted in the summary.

    Test with:
    snakemake -c1 data/processed/transport/demand/h3-r8/demand-by-mode.parquet
    """
    input:
        od=rules.transport_radiation_od.output.od,
        skim=rules.transport_road_skim.output.skim,
        nodes=rules.transport_multimodal_network.output.nodes,
        script=f"{TL_SCRIPTS}/transport_mode_split.py",
    output:
        demand="{data}/processed/transport/demand/{zoning}/demand-by-mode.parquet",
        summary="{data}/processed/transport/demand/{zoning}/demand-by-mode-summary.json",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --od {input.od} --skim {input.skim} --nodes {input.nodes} \
            --output {output.demand} --output-summary {output.summary}
        """
