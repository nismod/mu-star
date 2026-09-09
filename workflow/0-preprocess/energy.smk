"""Prepare the provided energy assets and build the network products.

Each network is built through ``energy.network_source.build_network``, chosen by
its ``--source``:

- ``base``: built directly from the provided transmission assets.
- ``inferred-osm``: OpenStreetMap substations, plants and generators as power
  terminals, connected across an OSM road subnetwork filtered to VIIRS
  night-light targets.
- ``inferred-provided``: the same OSM-road-and-night-light method as
  ``inferred-osm``, but rooted on the provided substations and generators and
  keeping the provided transmission backbone.

Disruption analysis (reporting metrics for a set of disrupted assets) is not
part of this workflow yet; see docs/src/infrastructure-energy.md.
"""


import re
from pathlib import Path


ENERGY_CONFIG = config.get("energy", {})
ENERGY_BASE_NETWORK = ENERGY_CONFIG.get("base_network", {})
ENERGY_INFERRED = ENERGY_CONFIG.get("inferred", {})
ENERGY_NIGHTLIGHT = ENERGY_CONFIG.get("nightlight", {})
ENERGY_DATA_ROOT = config.get("data_root", "data")


def _energy_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    if not slug:
        raise ValueError("energy region must contain letters or numbers")
    return slug


# --- Inferred (OSM road envelope) settings ---------------------------------
ENERGY_INFERRED_REGION = str(
    ENERGY_INFERRED.get("region", "mauritius-rodrigues")
).strip()
if not ENERGY_INFERRED_REGION:
    raise ValueError("energy.inferred.region must not be empty")
ENERGY_INFERRED_REGION_SLUG = _energy_slug(ENERGY_INFERRED_REGION)
# osmnx road detail filtered by the nightlight targets. "drive" (default) keeps
# the drivable network and drops footpaths, tracks and hiking trails that the
# distribution-line proxy should not follow; "all" keeps every mapped way.
ENERGY_INFERRED_NETWORK_TYPE = str(
    ENERGY_INFERRED.get("network_type", "drive")
).strip()
if not ENERGY_INFERRED_NETWORK_TYPE:
    raise ValueError("energy.inferred.network_type must not be empty")

# --- Product result names --------------------------------------------------
ENERGY_BASE_NAME = "base-mauritius"
ENERGY_INFERRED_OSM_NAME = f"inferred-osm-{ENERGY_INFERRED_REGION_SLUG}"
ENERGY_INFERRED_PROVIDED_NAME = f"inferred-provided-{ENERGY_INFERRED_REGION_SLUG}"

# --- Provided and cached inputs --------------------------------------------
# The provided asset tables and the offline OSM cache are inputs to
# the builds. OSM roads and power features are cached under
# incoming/energy/osm/<region>/; acquire them once with allow_osm_download
# then keep runs offline (see energy.osm.fetch_osm_roads).
ENERGY_PROVIDED_DIR = f"{{data}}/processed/energy/provided"
ENERGY_NETWORKS_DIR = f"{{data}}/processed/energy/networks"
ENERGY_TABLES_DIR = f"{{data}}/out/energy"
ENERGY_OSM_ROOT = f"incoming/energy/osm/{ENERGY_INFERRED_REGION_SLUG}"
# Mirror energy.osm.osm_roads_path: "drive" caches to roads.parquet, any other
# network type to roads-<type>.parquet. This is the file build_network reads
# internally, so the declared input and the internal fetch stay in step.
_ENERGY_ROADS_SUFFIX = (
    "" if ENERGY_INFERRED_NETWORK_TYPE == "drive" else f"-{_energy_slug(ENERGY_INFERRED_NETWORK_TYPE)}"
)
# A user-supplied energy.nightlight.roads path (relative to data_root) overrides
# the cached OSM extract. Both inferred rules declare it as the roads input and
# pass it into build_network, so the declared input and the file the builder
# reads stay in step. Leave it null to use the cached OSM roads.
ENERGY_OSM_ROADS = (
    ENERGY_NIGHTLIGHT.get("roads") or f"{ENERGY_OSM_ROOT}/roads{_ENERGY_ROADS_SUFFIX}.parquet"
)
ENERGY_OSM_POWER = f"{ENERGY_OSM_ROOT}/power.parquet"

# --- Nightlight target settings --------------------------------------------
ENERGY_NIGHTLIGHT_REGION = str(
    ENERGY_NIGHTLIGHT.get("region", ENERGY_INFERRED_REGION)
).strip()
ENERGY_NIGHTLIGHT_REGION_SLUG = _energy_slug(ENERGY_NIGHTLIGHT_REGION)
ENERGY_NIGHTLIGHT_DIR = (
    f"{{data}}/processed/energy/nightlight/{ENERGY_NIGHTLIGHT_REGION_SLUG}"
)
ENERGY_NIGHTLIGHT_TARGETS = f"{ENERGY_NIGHTLIGHT_DIR}/targets.geoparquet"

# The radiance composite is built from cached monthly VIIRS tiles unless the user
# supplies their own via energy.nightlight.nightlights (relative to data_root).
# Region/year specifics live in config so the acquisition code stays reusable.
ENERGY_NIGHTLIGHT_SOURCE = ENERGY_NIGHTLIGHT.get("source", {})
ENERGY_NIGHTLIGHT_MONTHLY_DIR = ENERGY_NIGHTLIGHT_SOURCE.get(
    "monthly_dir", "incoming/energy/nightlights/viirs-2024-monthly"
)
ENERGY_NIGHTLIGHT_OBJECT_IDS = list(ENERGY_NIGHTLIGHT_SOURCE.get("object_ids", range(120, 132)))
ENERGY_NIGHTLIGHT_MONTHS = [
    f"{{data}}/{ENERGY_NIGHTLIGHT_MONTHLY_DIR}/{index:02d}.tif"
    for index in range(1, len(ENERGY_NIGHTLIGHT_OBJECT_IDS) + 1)
]
ENERGY_NIGHTLIGHT_BUILT_COMPOSITE = f"{ENERGY_NIGHTLIGHT_DIR}/viirs-composite.tif"

ENERGY_NIGHTLIGHT_OVERRIDE = ENERGY_NIGHTLIGHT.get("nightlights")
if ENERGY_NIGHTLIGHT_OVERRIDE:
    if Path(ENERGY_NIGHTLIGHT_OVERRIDE).is_absolute():
        raise ValueError("energy.nightlight.nightlights must be relative to the selected data root")
    if Path(ENERGY_NIGHTLIGHT_OVERRIDE).suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("energy.nightlight.nightlights must be a .tif or .tiff raster")
    ENERGY_NIGHTLIGHT_COMPOSITE = f"{{data}}/{ENERGY_NIGHTLIGHT_OVERRIDE}"
else:
    ENERGY_NIGHTLIGHT_COMPOSITE = ENERGY_NIGHTLIGHT_BUILT_COMPOSITE

ENERGY_NIGHTLIGHT_AOI_RELATIVE = (
    ENERGY_NIGHTLIGHT.get("aoi")
    or f"incoming/energy/osm/{ENERGY_NIGHTLIGHT_REGION_SLUG}/aoi.parquet"
)
if Path(ENERGY_NIGHTLIGHT_AOI_RELATIVE).is_absolute():
    raise ValueError("energy.nightlight.aoi must be relative to the selected data root")
_ENERGY_AOI_SUFFIXES = {".geojson", ".gpkg", ".parquet", ".geoparquet"}
if Path(ENERGY_NIGHTLIGHT_AOI_RELATIVE).suffix.lower() not in _ENERGY_AOI_SUFFIXES:
    raise ValueError(f"energy.nightlight.aoi must use one of: {', '.join(sorted(_ENERGY_AOI_SUFFIXES))}")


# Sidecars required to read a provided ESRI shapefile. The optional .cpg
# (codepage) sidecar is deliberately excluded: energy.intake.prepare_provided_data
# reads these shapefiles without it, so requiring it here would reject
# otherwise-valid inputs that omit it.
PROVIDED_SHAPEFILE_EXTENSIONS = ("shp", "shx", "dbf", "prj")


rule prepare_energy_assets:
    """
    Clean the provided energy source data and write reviewable asset tables.

    Test with:
    snakemake -c1 data/processed/energy/provided/generators.csv
    """
    input:
        workbook="{data}/incoming/energy/provided/power_demand/Power Demand.xlsx",
        substations=[
            f"{{data}}/incoming/energy/provided/substation/Substation.{extension}"
            for extension in PROVIDED_SHAPEFILE_EXTENSIONS
        ],
        routes=[
            f"{{data}}/incoming/energy/provided/power_transmission/PowerGrid.{extension}"
            for extension in PROVIDED_SHAPEFILE_EXTENSIONS
        ],
        generation_points=[
            f"{{data}}/incoming/energy/provided/generation_source/GenSource1.{extension}"
            for extension in PROVIDED_SHAPEFILE_EXTENSIONS
        ],
        generation_areas=[
            f"{{data}}/incoming/energy/provided/generation_source/GenSource2.{extension}"
            for extension in PROVIDED_SHAPEFILE_EXTENSIONS
        ],
        capacity_reference="src/energy/resources/generator_capacity_reference.csv",
        script="workflow/0-preprocess/energy_prepare_assets.py",
    output:
        substations="{data}/processed/energy/provided/substations.parquet",
        snapped_substations="{data}/processed/energy/provided/snapped_substations.parquet",
        snap_distances="{data}/processed/energy/provided/substation_snap_distances.csv",
        routes="{data}/processed/energy/provided/transmission_routes.parquet",
        generation_points="{data}/processed/energy/provided/generation_points.parquet",
        generation_areas="{data}/processed/energy/provided/generation_areas.parquet",
        generators="{data}/processed/energy/provided/generators.csv",
        service_weights="{data}/processed/energy/provided/service_weights.csv",
        monthly_peak="{data}/processed/energy/provided/monthly_peak_demand_mw.csv",
        annual_demand="{data}/processed/energy/provided/annual_sector_demand_gwh.csv",
        generator_template="{data}/processed/energy/templates/generators.csv",
        line_template="{data}/processed/energy/templates/lines.csv",
    params:
        input_dir="{data}/incoming/energy/provided",
        output_dir="{data}/processed/energy/provided",
    shell:
        """
        python {input.script} \
            --input-dir {params.input_dir} \
            --output-dir {params.output_dir}
        """


rule build_base_energy_network:
    """
    Build the canonical provided CEB routed transmission topology.

    Methodology: ceb-routed-topology-v3. Test with:
    snakemake -c1 data/processed/energy/networks/base-mauritius/base-mauritius.nc
    """
    input:
        buses="{data}/processed/energy/provided/snapped_substations.parquet",
        routes="{data}/processed/energy/provided/transmission_routes.parquet",
        generators="{data}/processed/energy/provided/generators.csv",
        script="workflow/0-preprocess/energy_build_network.py",
    output:
        network=f"{ENERGY_NETWORKS_DIR}/{ENERGY_BASE_NAME}/{ENERGY_BASE_NAME}.nc",
        metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_BASE_NAME}/{ENERGY_BASE_NAME}_metadata.json",
        spatial_nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_BASE_NAME}/geoparquet/{ENERGY_BASE_NAME}-nodes.geoparquet",
        spatial_edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_BASE_NAME}/geoparquet/{ENERGY_BASE_NAME}-edges.geoparquet",
        spatial_manifest=f"{ENERGY_NETWORKS_DIR}/{ENERGY_BASE_NAME}/geoparquet/{ENERGY_BASE_NAME}-spatial-manifest.json",
        generators=f"{ENERGY_TABLES_DIR}/{ENERGY_BASE_NAME}/generators.csv",
        lines=f"{ENERGY_TABLES_DIR}/{ENERGY_BASE_NAME}/lines.csv",
        validation=f"{ENERGY_TABLES_DIR}/{ENERGY_BASE_NAME}/validation.json",
    params:
        input_dir=ENERGY_PROVIDED_DIR,
        output_dir=ENERGY_NETWORKS_DIR,
        export_root=ENERGY_TABLES_DIR,
        output_name=ENERGY_BASE_NAME,
        route_gap_tolerance_m=ENERGY_BASE_NETWORK.get("route_gap_tolerance_m", 75),
        default_voltage_kv=ENERGY_BASE_NETWORK.get("default_voltage_kv", 66),
        topology_capacity_mva=ENERGY_BASE_NETWORK.get("topology_capacity_mva", 10000),
    shell:
        """
        python {input.script} \
            --source base \
            --input-dir {params.input_dir} \
            --output-dir {params.output_dir} \
            --export-root {params.export_root} \
            --output-name {params.output_name} \
            --overwrite \
            --base-route-gap-tolerance-m {params.route_gap_tolerance_m} \
            --base-default-voltage-kv {params.default_voltage_kv} \
            --base-topology-capacity-mva {params.topology_capacity_mva}
        """


rule fetch_energy_nightlights:
    """
    Cache the monthly VIIRS radiance tiles (opt-in download).

    Offline-first: this reaches the image service only when a tile is missing
    AND energy.nightlight.source.allow_download is true; otherwise it explains
    how to enable the fetch. Reproduce the tiles from scratch with:
    snakemake -c1 fetch_energy_nightlights
    """
    input:
        script="workflow/0-preprocess/energy_fetch_nightlights.py",
    output:
        months=ENERGY_NIGHTLIGHT_MONTHS,
    params:
        out_dir=f"{{data}}/{ENERGY_NIGHTLIGHT_MONTHLY_DIR}",
        object_ids=",".join(str(value) for value in ENERGY_NIGHTLIGHT_OBJECT_IDS),
        bbox=",".join(str(value) for value in ENERGY_NIGHTLIGHT_SOURCE.get("bbox", [57, -21, 64, -19])),
        pixel_size_degrees=ENERGY_NIGHTLIGHT_SOURCE.get("pixel_size_degrees", 0.004166666666666667),
        service=ENERGY_NIGHTLIGHT_SOURCE.get("service") or "",
        rendering_rule=ENERGY_NIGHTLIGHT_SOURCE.get("rendering_rule") or "",
        allow_download="--allow-download" if bool(ENERGY_NIGHTLIGHT_SOURCE.get("allow_download", False)) else "",
    shell:
        """
        python {input.script} \
            --out-dir {params.out_dir} \
            --object-ids {params.object_ids} \
            --bbox {params.bbox} \
            --pixel-size-degrees {params.pixel_size_degrees} \
            --service {params.service:q} \
            --rendering-rule {params.rendering_rule:q} \
            {params.allow_download}
        """


rule build_energy_nightlight_composite:
    """
    Reduce the cached monthly VIIRS tiles to one radiance composite.

    Tiles come from fetch_energy_nightlights (or the shared data store); this
    writes the pixelwise-median composite the target step reads. Test with:
    snakemake -c1 data/processed/energy/nightlight/mauritius-rodrigues/viirs-composite.tif
    """
    input:
        months=ENERGY_NIGHTLIGHT_MONTHS,
        script="workflow/0-preprocess/energy_build_nightlight_composite.py",
    output:
        composite=ENERGY_NIGHTLIGHT_BUILT_COMPOSITE,
    shell:
        """
        python {input.script} {input.months} --output {output.composite}
        """


rule build_energy_nightlight_targets:
    """
    Extract VIIRS nightlight connection targets (replaces GridFinder rasters).

    Only target points, their raster mask and provenance metadata are written;
    the inferred builds consume these targets to retain the OSM road subnetwork
    they support. Test with:
    snakemake -c1 data/processed/energy/nightlight/mauritius-rodrigues/targets.geoparquet
    """
    input:
        nightlights=ENERGY_NIGHTLIGHT_COMPOSITE,
        aoi=f"{{data}}/{ENERGY_NIGHTLIGHT_AOI_RELATIVE}",
        script="workflow/0-preprocess/energy_build_nightlight_targets.py",
    output:
        targets_raster=f"{ENERGY_NIGHTLIGHT_DIR}/targets.tif",
        targets=f"{ENERGY_NIGHTLIGHT_DIR}/targets.geoparquet",
        metadata=f"{ENERGY_NIGHTLIGHT_DIR}/metadata.json",
    params:
        output_dir=ENERGY_NIGHTLIGHT_DIR,
        region=ENERGY_NIGHTLIGHT_REGION,
        nightlight_threshold=ENERGY_NIGHTLIGHT.get("nightlight_threshold", 0.1),
    shell:
        """
        python {input.script} \
            --nightlights {input.nightlights} \
            --aoi {input.aoi} \
            --output-dir {params.output_dir} \
            --region {params.region:q} \
            --nightlight-threshold {params.nightlight_threshold}
        """


rule build_inferred_osm_energy_network:
    """
    Build the OSM-power inferred topology from a nightlight-supported road subnetwork.

    Methodology: nightlight-roads-osm-power-v1. Test with:
    snakemake -c1 data/processed/energy/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc
    """
    input:
        roads=f"{{data}}/{ENERGY_OSM_ROADS}",
        power=f"{{data}}/{ENERGY_OSM_POWER}",
        nightlight_targets=ENERGY_NIGHTLIGHT_TARGETS,
        script="workflow/0-preprocess/energy_build_network.py",
    output:
        network=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/{ENERGY_INFERRED_OSM_NAME}.nc",
        metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/{ENERGY_INFERRED_OSM_NAME}_metadata.json",
        spatial_nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/geoparquet/{ENERGY_INFERRED_OSM_NAME}-nodes.geoparquet",
        spatial_edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/geoparquet/{ENERGY_INFERRED_OSM_NAME}-edges.geoparquet",
        spatial_manifest=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/geoparquet/{ENERGY_INFERRED_OSM_NAME}-spatial-manifest.json",
        nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/inferred_distribution/inferred_distribution_nodes.csv",
        edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/inferred_distribution/inferred_distribution_edges.csv",
        graph_metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/inferred_distribution/inferred_distribution_metadata.json",
        service_weights=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_OSM_NAME}/inferred_distribution/service_weights.csv",
        generators=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_OSM_NAME}/generators.csv",
        lines=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_OSM_NAME}/lines.csv",
        validation=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_OSM_NAME}/validation.json",
    params:
        input_dir=ENERGY_PROVIDED_DIR,
        output_dir=ENERGY_NETWORKS_DIR,
        export_root=ENERGY_TABLES_DIR,
        output_name=ENERGY_INFERRED_OSM_NAME,
        region=ENERGY_INFERRED_REGION,
        network_type=ENERGY_INFERRED_NETWORK_TYPE,
        max_anchor_distance_m=ENERGY_INFERRED.get("max_anchor_distance_m", 1000),
        inferred_voltage_kv=ENERGY_INFERRED.get("topology_voltage_kv", 11),
        inferred_capacity_mva=ENERGY_INFERRED.get("topology_capacity_mva", 5),
        reference_line_length_km=ENERGY_INFERRED.get("ceb_total_line_length_km", 10492.2),
        line_length_tolerance_fraction=ENERGY_INFERRED.get("line_length_tolerance_fraction", 0.10),
        nightlight_support_distance_m=ENERGY_NIGHTLIGHT.get("nightlight_support_distance_m", 1000),
    shell:
        """
        python {input.script} \
            --source inferred-osm \
            --data-root {wildcards.data} \
            --input-dir {params.input_dir} \
            --output-dir {params.output_dir} \
            --export-root {params.export_root} \
            --output-name {params.output_name} \
            --overwrite \
            --region {params.region:q} \
            --network-type {params.network_type} \
            --roads-path {input.roads} \
            --nightlight-targets {input.nightlight_targets} \
            --nightlight-support-distance-m {params.nightlight_support_distance_m} \
            --max-anchor-distance-m {params.max_anchor_distance_m} \
            --inferred-voltage-kv {params.inferred_voltage_kv} \
            --inferred-capacity-mva {params.inferred_capacity_mva} \
            --inferred-reference-line-length-km {params.reference_line_length_km} \
            --line-length-tolerance-fraction {params.line_length_tolerance_fraction}
        """


rule build_inferred_provided_energy_network:
    """
    Build the provided-data inferred topology using the same nightlight road method.

    Provided substations and generators are the power targets and the CEB
    backbone is preserved. Methodology: nightlight-roads-provided-power-v1.
    Test with:
    snakemake -c1 data/processed/energy/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc
    """
    input:
        buses="{data}/processed/energy/provided/snapped_substations.parquet",
        routes="{data}/processed/energy/provided/transmission_routes.parquet",
        generators="{data}/processed/energy/provided/generators.csv",
        roads=f"{{data}}/{ENERGY_OSM_ROADS}",
        nightlight_targets=ENERGY_NIGHTLIGHT_TARGETS,
        script="workflow/0-preprocess/energy_build_network.py",
    output:
        network=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/{ENERGY_INFERRED_PROVIDED_NAME}.nc",
        metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/{ENERGY_INFERRED_PROVIDED_NAME}_metadata.json",
        spatial_nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/geoparquet/{ENERGY_INFERRED_PROVIDED_NAME}-nodes.geoparquet",
        spatial_edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/geoparquet/{ENERGY_INFERRED_PROVIDED_NAME}-edges.geoparquet",
        spatial_manifest=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/geoparquet/{ENERGY_INFERRED_PROVIDED_NAME}-spatial-manifest.json",
        nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/inferred_distribution/inferred_distribution_nodes.csv",
        edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/inferred_distribution/inferred_distribution_edges.csv",
        graph_metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/inferred_distribution/inferred_distribution_metadata.json",
        service_weights=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/inferred_distribution/service_weights.csv",
        generators=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/generators.csv",
        lines=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/lines.csv",
        validation=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_PROVIDED_NAME}/validation.json",
    params:
        input_dir=ENERGY_PROVIDED_DIR,
        output_dir=ENERGY_NETWORKS_DIR,
        export_root=ENERGY_TABLES_DIR,
        output_name=ENERGY_INFERRED_PROVIDED_NAME,
        region=ENERGY_INFERRED_REGION,
        network_type=ENERGY_INFERRED_NETWORK_TYPE,
        max_anchor_distance_m=ENERGY_INFERRED.get("max_anchor_distance_m", 1000),
        inferred_voltage_kv=ENERGY_INFERRED.get("topology_voltage_kv", 11),
        inferred_transmission_voltage_kv=ENERGY_INFERRED.get("transmission_voltage_kv", 66),
        inferred_capacity_mva=ENERGY_INFERRED.get("topology_capacity_mva", 5),
        reference_line_length_km=ENERGY_INFERRED.get("ceb_total_line_length_km", 10492.2),
        line_length_tolerance_fraction=ENERGY_INFERRED.get("line_length_tolerance_fraction", 0.10),
        generation_capacity_tolerance_fraction=ENERGY_INFERRED.get(
            "generation_capacity_tolerance_fraction", 0.10
        ),
        nightlight_support_distance_m=ENERGY_NIGHTLIGHT.get("nightlight_support_distance_m", 1000),
    shell:
        """
        python {input.script} \
            --source inferred-provided \
            --data-root {wildcards.data} \
            --input-dir {params.input_dir} \
            --output-dir {params.output_dir} \
            --export-root {params.export_root} \
            --output-name {params.output_name} \
            --overwrite \
            --region {params.region:q} \
            --network-type {params.network_type} \
            --roads-path {input.roads} \
            --nightlight-targets {input.nightlight_targets} \
            --nightlight-support-distance-m {params.nightlight_support_distance_m} \
            --max-anchor-distance-m {params.max_anchor_distance_m} \
            --inferred-voltage-kv {params.inferred_voltage_kv} \
            --inferred-transmission-voltage-kv {params.inferred_transmission_voltage_kv} \
            --inferred-capacity-mva {params.inferred_capacity_mva} \
            --inferred-reference-line-length-km {params.reference_line_length_km} \
            --line-length-tolerance-fraction {params.line_length_tolerance_fraction} \
            --generation-capacity-tolerance-fraction {params.generation_capacity_tolerance_fraction}
        """


rule energy_base_network:
    """Build the canonical base-mauritius network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_BASE_NAME}/{ENERGY_BASE_NAME}.nc",


rule energy_inferred_osm_network:
    """Build the OSM-power inferred network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_OSM_NAME}/{ENERGY_INFERRED_OSM_NAME}.nc",


rule energy_inferred_provided_network:
    """Build the provided-data inferred network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_PROVIDED_NAME}/{ENERGY_INFERRED_PROVIDED_NAME}.nc",


rule build_energy_networks:
    """Build all three energy networks (base and both inferred products)."""
    input:
        rules.energy_base_network.input,
        rules.energy_inferred_osm_network.input,
        rules.energy_inferred_provided_network.input,