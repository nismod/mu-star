"""Energy rules: build three models of the electricity network.

- base-mauritius: CEB substations, 66 kV lines and power plants.
- inferred-provided-<region>: the same CEB assets plus the estimated
  distribution network (OpenStreetMap roads near lit areas).
- inferred-osm-<region>: OpenStreetMap substations and power plants plus the
  estimated distribution network.

Each network is written to {data}/processed/energy/<model_data>/networks/<name>/
by workflow/0-preprocess/energy_build_network.py, which calls
energy.build.build_network. Settings are in config/energy/energy.yaml, where
model_data names the model-data pack.

Source data is read from {data}/incoming/Infrastructure/Energy/, laid out as on
the shared drive. OpenStreetMap, VIIRS night lights and WorldPop are downloaded
when missing. If the processed files already exist, for example a pack copied
or linked from the shared drive, nothing is downloaded or rebuilt.

All networks:  snakemake -c1 build_energy_networks
One network:   snakemake -c1 data/processed/energy/<model_data>/networks/base-mauritius/base-mauritius.nc

Setup, commands, method and limitations: docs/src/infrastructure-energy.md.
"""

import shlex
from pathlib import Path

from energy import osm as energy_osm
from energy.nightlights import tile_name
from energy.paths import INCOMING_ENERGY_RELATIVE, model_data
from energy.provided import (
    DEMAND_FOLDER,
    DEMAND_LEVELS_FILE,
    GENERATION_FOLDER,
    PLANT_CAPACITIES_FILE,
    PLANT_SITES_FILE,
    SHAPEFILE_EXTENSIONS,
    SUBSTATION_FOLDER,
    TRANSMISSION_FOLDER,
)


configfile: "config/energy/energy.yaml"


ENERGY = config["energy"]
# Data folder used when a target is named by rule rather than by path.
ENERGY_DATA_ROOT = config.get("data_root", "data")

REGION = str(ENERGY.get("region", "mauritius-rodrigues")).strip()
if not REGION:
    raise ValueError("energy.region must not be empty (config/energy/energy.yaml)")
REGION_SLUG = energy_osm.region_slug(REGION)

OSM_SETTINGS = ENERGY.get("osm", {})
NETWORK_TYPE = str(OSM_SETTINGS.get("network_type", "drive")).strip() or "drive"

# Source data, laid out as on the shared drive.
INCOMING_DIR = f"{{data}}/{INCOMING_ENERGY_RELATIVE.as_posix()}"
# Processed files, in a folder named after the model-data pack.
MODEL_DATA = model_data(ENERGY)
PROCESSED_DIR = f"{{data}}/processed/energy/{MODEL_DATA}"

# Scripts listed as rule inputs are wrapped in ancient(): a fresh checkout gives
# them today's date, which would make existing processed data look out of date.
# After editing a script, rerun its rule with `snakemake -R <rule>`.

VECTOR_SUFFIXES = {".parquet", ".geoparquet", ".gpkg", ".geojson"}


def _relative_data_path(value, key, suffixes):
    """Return the configured path, checked to be relative to the data folder and to end in one of ``suffixes``."""
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        raise ValueError(f"{key} must be relative to the data root, got {value!r}")
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"{key} must end with one of {sorted(suffixes)}, got {value!r}")
    return path.as_posix()


# --- OpenStreetMap files (relative to the data folder) ------------------------
# Paths come from energy.osm, as in the Python code.
OSM_ROADS_CACHE = energy_osm.roads_cache_relative(REGION, NETWORK_TYPE).as_posix()
OSM_POWER_CACHE = energy_osm.power_cache_relative(REGION).as_posix()
OSM_AOI_CACHE = energy_osm.aoi_cache_relative(REGION).as_posix()
# energy.osm.roads and energy.osm.aoi, if set, replace the downloaded files.
OSM_ROADS = _relative_data_path(OSM_SETTINGS.get("roads"), "energy.osm.roads", VECTOR_SUFFIXES) or OSM_ROADS_CACHE
OSM_AOI = _relative_data_path(OSM_SETTINGS.get("aoi"), "energy.osm.aoi", VECTOR_SUFFIXES) or OSM_AOI_CACHE

# --- Night lights -------------------------------------------------------------
NIGHTLIGHT = ENERGY.get("nightlight", {})
NIGHTLIGHT_SOURCE = NIGHTLIGHT.get("source", {})
NIGHTLIGHT_DIR = f"{PROCESSED_DIR}/nightlight/{REGION_SLUG}"
NIGHTLIGHT_TARGETS = f"{NIGHTLIGHT_DIR}/targets.geoparquet"
NIGHTLIGHT_MONTHLY_DIR = NIGHTLIGHT_SOURCE.get(
    "monthly_dir", f"{INCOMING_ENERGY_RELATIVE.as_posix()}/Nighttime Lights/viirs-2024-monthly"
)
NIGHTLIGHT_OBJECT_IDS = [int(value) for value in NIGHTLIGHT_SOURCE.get("object_ids", range(120, 132))]
NIGHTLIGHT_MONTHS = [
    f"{{data}}/{NIGHTLIGHT_MONTHLY_DIR}/{tile_name(index, object_id)}"
    for index, object_id in enumerate(NIGHTLIGHT_OBJECT_IDS, start=1)
]
NIGHTLIGHT_BUILT_COMPOSITE = f"{NIGHTLIGHT_DIR}/viirs-composite.tif"
_NIGHTLIGHT_OVERRIDE = _relative_data_path(
    NIGHTLIGHT.get("nightlights"), "energy.nightlight.nightlights", {".tif", ".tiff"}
)
NIGHTLIGHT_COMPOSITE = f"{{data}}/{_NIGHTLIGHT_OVERRIDE}" if _NIGHTLIGHT_OVERRIDE else NIGHTLIGHT_BUILT_COMPOSITE

# --- Population and demand ------------------------------------------------------
POPULATION_URL = ENERGY["population"]["url"]
POPULATION_RASTER = f"{INCOMING_DIR}/Population/{POPULATION_URL.rsplit('/', 1)[-1]}"
DEMAND = ENERGY.get("demand", {})
DEMAND_DIR = f"{PROCESSED_DIR}/demand"

# --- Networks -----------------------------------------------------------------
BASE_NETWORK = ENERGY.get("base_network", {})
INFERRED = ENERGY.get("inferred", {})
PROVIDED_DIR = f"{PROCESSED_DIR}/provided"
NETWORKS_DIR = f"{PROCESSED_DIR}/networks"
TABLES_DIR = f"{{data}}/out/energy/{MODEL_DATA}"
BASE_NAME = "base-mauritius"
INFERRED_OSM_NAME = f"inferred-osm-{REGION_SLUG}"
INFERRED_PROVIDED_NAME = f"inferred-provided-{REGION_SLUG}"


def network_outputs(name, *, inferred):
    """Return the files that energy.build.build_network writes for network ``name``."""
    outputs = {
        "network": f"{NETWORKS_DIR}/{name}/{name}.nc",
        "metadata": f"{NETWORKS_DIR}/{name}/{name}_metadata.json",
        "spatial_nodes": f"{NETWORKS_DIR}/{name}/geoparquet/{name}-nodes.geoparquet",
        "spatial_edges": f"{NETWORKS_DIR}/{name}/geoparquet/{name}-edges.geoparquet",
        "spatial_manifest": f"{NETWORKS_DIR}/{name}/geoparquet/{name}-spatial-manifest.json",
        "generators": f"{TABLES_DIR}/{name}/generators.csv",
        "lines": f"{TABLES_DIR}/{name}/lines.csv",
        "validation": f"{TABLES_DIR}/{name}/validation.json",
    }
    if inferred:
        graph_dir = f"{NETWORKS_DIR}/{name}/inferred_distribution"
        outputs.update(
            {
                "nodes": f"{graph_dir}/inferred_distribution_nodes.csv",
                "edges": f"{graph_dir}/inferred_distribution_edges.csv",
                "graph_metadata": f"{graph_dir}/inferred_distribution_metadata.json",
                "service_weights": f"{graph_dir}/service_weights.csv",
            }
        )
    return outputs


rule fetch_energy_osm:
    """
    Download the OpenStreetMap roads, power features and island outlines. Needs
    internet. Runs only when one of these files is missing.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Energy/OpenStreetMap/mauritius-rodrigues/roads.parquet
    """
    output:
        roads=f"{{data}}/{OSM_ROADS_CACHE}",
        power=f"{{data}}/{OSM_POWER_CACHE}",
        aoi=f"{{data}}/{OSM_AOI_CACHE}",
    params:
        region=REGION,
        network_type=NETWORK_TYPE,
    shell:
        """
        python workflow/0-preprocess/energy_fetch_osm.py \
            --region {params.region:q} \
            --network-type {params.network_type:q} \
            --data-root {wildcards.data:q} \
            --allow-download
        """


rule prepare_energy_assets:
    """
    Clean the CEB data and write the asset tables.

    generators.csv has every plant in CEB Annual Report/ceb_plant_capacities_2023_24.csv,
    placed using ceb_plant_sites.csv. Some plants are placed by their
    OpenStreetMap name, so this rule also reads the OSM power features.
    generation_sites.csv keeps the CEB generation sites from the shapefiles.

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/provided/generators.csv
    """
    input:
        workbook=f"{INCOMING_DIR}/{DEMAND_FOLDER}/Power Demand.xlsx",
        substations=[
            f"{INCOMING_DIR}/{SUBSTATION_FOLDER}/Substation.{extension}" for extension in SHAPEFILE_EXTENSIONS
        ],
        routes=[
            f"{INCOMING_DIR}/{TRANSMISSION_FOLDER}/PowerGrid.{extension}" for extension in SHAPEFILE_EXTENSIONS
        ],
        generation_points=[
            f"{INCOMING_DIR}/{GENERATION_FOLDER}/GenSource1.{extension}" for extension in SHAPEFILE_EXTENSIONS
        ],
        generation_areas=[
            f"{INCOMING_DIR}/{GENERATION_FOLDER}/GenSource2.{extension}" for extension in SHAPEFILE_EXTENSIONS
        ],
        plant_capacities=f"{INCOMING_DIR}/{PLANT_CAPACITIES_FILE}",
        plant_sites=f"{INCOMING_DIR}/{PLANT_SITES_FILE}",
        osm_power=f"{{data}}/{OSM_POWER_CACHE}",
        script=ancient("workflow/0-preprocess/energy_prepare_assets.py"),
    output:
        substations=f"{PROVIDED_DIR}/substations.parquet",
        snapped_substations=f"{PROVIDED_DIR}/snapped_substations.parquet",
        snap_distances=f"{PROVIDED_DIR}/substation_snap_distances.csv",
        routes=f"{PROVIDED_DIR}/transmission_routes.parquet",
        generation_points=f"{PROVIDED_DIR}/generation_points.parquet",
        generation_areas=f"{PROVIDED_DIR}/generation_areas.parquet",
        generation_sites=f"{PROVIDED_DIR}/generation_sites.csv",
        generators=f"{PROVIDED_DIR}/generators.csv",
        service_weights=f"{PROVIDED_DIR}/service_weights.csv",
        monthly_peak=f"{PROVIDED_DIR}/monthly_peak_demand_mw.csv",
        annual_demand=f"{PROVIDED_DIR}/annual_sector_demand_gwh.csv",
        generator_template=f"{PROCESSED_DIR}/templates/generators.csv",
        line_template=f"{PROCESSED_DIR}/templates/lines.csv",
    params:
        input_dir=INCOMING_DIR,
        output_dir=PROVIDED_DIR,
    shell:
        """
        python {input.script:q} \
            --input-dir {params.input_dir:q} \
            --output-dir {params.output_dir:q} \
            --osm-power {input.osm_power:q}
        """


rule build_base_energy_network:
    """
    Build base-mauritius from the CEB substations, 66 kV lines and power plants.

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/networks/base-mauritius/base-mauritius.nc
    """
    input:
        buses=f"{PROVIDED_DIR}/snapped_substations.parquet",
        routes=f"{PROVIDED_DIR}/transmission_routes.parquet",
        generators=f"{PROVIDED_DIR}/generators.csv",
        script=ancient("workflow/0-preprocess/energy_build_network.py"),
    output:
        **network_outputs(BASE_NAME, inferred=False),
    params:
        input_dir=PROVIDED_DIR,
        output_dir=NETWORKS_DIR,
        export_root=TABLES_DIR,
        output_name=BASE_NAME,
        route_gap_tolerance_m=BASE_NETWORK.get("route_gap_tolerance_m", 75),
        default_voltage_kv=BASE_NETWORK.get("default_voltage_kv", 66),
        topology_capacity_mva=BASE_NETWORK.get("topology_capacity_mva", 10000),
    shell:
        """
        python {input.script:q} \
            --source base \
            --input-dir {params.input_dir:q} \
            --output-dir {params.output_dir:q} \
            --export-root {params.export_root:q} \
            --output-name {params.output_name:q} \
            --overwrite \
            --base-route-gap-tolerance-m {params.route_gap_tolerance_m} \
            --base-default-voltage-kv {params.default_voltage_kv} \
            --base-topology-capacity-mva {params.topology_capacity_mva}
        """


rule fetch_energy_nightlights:
    """
    Download the twelve monthly VIIRS images. Needs internet. Runs only when an
    image is missing.

    Test with:
    snakemake -c1 "data/incoming/Infrastructure/Energy/Nighttime Lights/viirs-2024-monthly/01-120.tif"
    """
    output:
        months=NIGHTLIGHT_MONTHS,
    params:
        out_dir=f"{{data}}/{NIGHTLIGHT_MONTHLY_DIR}",
        object_ids=",".join(str(value) for value in NIGHTLIGHT_OBJECT_IDS),
        bbox=",".join(str(value) for value in NIGHTLIGHT_SOURCE.get("bbox", [57, -21, 64, -19])),
        pixel_size_degrees=NIGHTLIGHT_SOURCE.get("pixel_size_degrees", 0.004166666666666667),
        service=NIGHTLIGHT_SOURCE.get("service") or "",
        rendering_rule=NIGHTLIGHT_SOURCE.get("rendering_rule") or "",
    shell:
        """
        python workflow/0-preprocess/energy_fetch_nightlights.py \
            --out-dir {params.out_dir:q} \
            --object-ids {params.object_ids} \
            --bbox {params.bbox} \
            --pixel-size-degrees {params.pixel_size_degrees} \
            --service {params.service:q} \
            --rendering-rule {params.rendering_rule:q} \
            --allow-download
        """


rule build_energy_nightlight_composite:
    """
    Combine the monthly VIIRS images into one, taking the median of each pixel.

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/nightlight/mauritius-rodrigues/viirs-composite.tif
    """
    input:
        months=NIGHTLIGHT_MONTHS,
        script=ancient("workflow/0-preprocess/energy_build_nightlight_composite.py"),
    output:
        composite=NIGHTLIGHT_BUILT_COMPOSITE,
    shell:
        """
        python {input.script:q} {input.months:q} --output {output.composite:q}
        """


rule build_energy_nightlight_targets:
    """
    Find the targets: lit pixels inside the island outlines that the estimated
    distribution network must reach.

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/nightlight/mauritius-rodrigues/targets.geoparquet
    """
    input:
        nightlights=NIGHTLIGHT_COMPOSITE,
        aoi=f"{{data}}/{OSM_AOI}",
        script=ancient("workflow/0-preprocess/energy_build_nightlight_targets.py"),
    output:
        targets_raster=f"{NIGHTLIGHT_DIR}/targets.tif",
        targets=NIGHTLIGHT_TARGETS,
        metadata=f"{NIGHTLIGHT_DIR}/metadata.json",
    params:
        output_dir=NIGHTLIGHT_DIR,
        region=REGION,
        nightlight_threshold=NIGHTLIGHT.get("nightlight_threshold", 0.1),
    shell:
        """
        python {input.script:q} \
            --nightlights {input.nightlights:q} \
            --aoi {input.aoi:q} \
            --output-dir {params.output_dir:q} \
            --region {params.region:q} \
            --nightlight-threshold {params.nightlight_threshold}
        """


def _inferred_inputs(wildcards):
    """Return the inputs of one inferred network: roads and targets, plus the OSM
    power features (osm) or the CEB asset tables (provided)."""
    inputs = {
        "roads": f"{wildcards.data}/{OSM_ROADS}",
        "nightlight_targets": NIGHTLIGHT_TARGETS.format(data=wildcards.data),
        "script": ancient("workflow/0-preprocess/energy_build_network.py"),
    }
    if wildcards.variant == "osm":
        inputs["power"] = f"{wildcards.data}/{OSM_POWER_CACHE}"
    if wildcards.variant != "osm":
        for name in ("snapped_substations.parquet", "transmission_routes.parquet", "generators.csv"):
            inputs[name.split(".")[0]] = f"{PROVIDED_DIR.format(data=wildcards.data)}/{name}"
    return inputs


rule build_inferred_energy_network:
    """
    Build an inferred network: roads near lit areas, connected to the OpenStreetMap
    power assets (osm) or to the CEB substations, plants and 66 kV lines (provided).

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc
    snakemake -c1 data/processed/energy/<model_data>/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc
    """
    wildcard_constraints:
        variant="osm|provided",
    input:
        unpack(_inferred_inputs),
    output:
        **network_outputs(f"inferred-{{variant}}-{REGION_SLUG}", inferred=True),
    params:
        input_dir=PROVIDED_DIR,
        output_dir=NETWORKS_DIR,
        export_root=TABLES_DIR,
        output_name=f"inferred-{{variant}}-{REGION_SLUG}",
        region=REGION,
        network_type=NETWORK_TYPE,
        power_arg=lambda wildcards, input: (
            f"--power-path {shlex.quote(str(input.power))}" if wildcards.variant == "osm" else ""
        ),
        max_anchor_distance_m=INFERRED.get("max_anchor_distance_m", 1000),
        inferred_voltage_kv=INFERRED.get("topology_voltage_kv", 11),
        inferred_transmission_voltage_kv=INFERRED.get("transmission_voltage_kv", 66),
        inferred_capacity_mva=INFERRED.get("topology_capacity_mva", 5),
        inferred_transmission_capacity_mva=INFERRED.get("transmission_capacity_mva", 50),
        inferred_anchor_capacity_mva=INFERRED.get("anchor_capacity_mva", 137),
        reference_line_length_km=INFERRED.get("ceb_total_line_length_km", 10492.2),
        line_length_tolerance_fraction=INFERRED.get("line_length_tolerance_fraction", 0.10),
        generation_capacity_tolerance_fraction=INFERRED.get("generation_capacity_tolerance_fraction", 0.10),
        nightlight_support_distance_m=NIGHTLIGHT.get("nightlight_support_distance_m", 1000),
        base_route_gap_tolerance_m=BASE_NETWORK.get("route_gap_tolerance_m", 75),
    shell:
        """
        python {input.script:q} \
            --source inferred-{wildcards.variant} \
            --input-dir {params.input_dir:q} \
            --output-dir {params.output_dir:q} \
            --export-root {params.export_root:q} \
            --output-name {params.output_name:q} \
            --overwrite \
            --region {params.region:q} \
            --network-type {params.network_type:q} \
            --roads-path {input.roads:q} \
            {params.power_arg} \
            --nightlight-targets {input.nightlight_targets:q} \
            --nightlight-support-distance-m {params.nightlight_support_distance_m} \
            --max-anchor-distance-m {params.max_anchor_distance_m} \
            --inferred-voltage-kv {params.inferred_voltage_kv} \
            --inferred-transmission-voltage-kv {params.inferred_transmission_voltage_kv} \
            --inferred-capacity-mva {params.inferred_capacity_mva} \
            --inferred-transmission-capacity-mva {params.inferred_transmission_capacity_mva} \
            --inferred-anchor-capacity-mva {params.inferred_anchor_capacity_mva} \
            --inferred-reference-line-length-km {params.reference_line_length_km} \
            --line-length-tolerance-fraction {params.line_length_tolerance_fraction} \
            --generation-capacity-tolerance-fraction {params.generation_capacity_tolerance_fraction} \
            --base-route-gap-tolerance-m {params.base_route_gap_tolerance_m}
        """


rule fetch_energy_population:
    """
    Download the WorldPop population grid. Needs internet. Runs only when the
    file is missing.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Energy/Population/mus_ppp_2020_UNadj_constrained.tif
    """
    output:
        raster=POPULATION_RASTER,
    params:
        url=POPULATION_URL,
    shell:
        """
        python workflow/0-preprocess/energy_fetch_population.py --url {params.url:q} --output {output.raster:q}
        """


rule build_energy_demand:
    """
    Estimate the demand share of each substation and node of inferred-provided,
    the area each substation supplies, and the peak and average demand.

    Test with:
    snakemake -c1 data/processed/energy/<model_data>/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv
    """
    input:
        nodes=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/geoparquet/{INFERRED_PROVIDED_NAME}-nodes.geoparquet",
        aoi=f"{{data}}/{OSM_AOI}",
        population=POPULATION_RASTER,
        nightlights=NIGHTLIGHT_COMPOSITE,
        demand_levels=f"{INCOMING_DIR}/{DEMAND_LEVELS_FILE}",
        script=ancient("workflow/0-preprocess/energy_build_demand.py"),
    output:
        service_areas=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_areas.geoparquet",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        node_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_nodes.csv",
        demand_levels=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/demand_levels.csv",
        metadata=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/metadata.json",
    params:
        output_dir=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}",
        weight_nightlights=DEMAND.get("weight_nightlights", 0.6),
        weight_population=DEMAND.get("weight_population", 0.4),
    shell:
        """
        python {input.script:q} \
            --nodes {input.nodes:q} \
            --aoi {input.aoi:q} \
            --population {input.population:q} \
            --nightlights {input.nightlights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            --weight-nightlights {params.weight_nightlights} \
            --weight-population {params.weight_population}
        """


rule build_energy_networks:
    """Build the three energy networks."""
    input:
        [
            f"{ENERGY_DATA_ROOT}/processed/energy/{MODEL_DATA}/networks/{name}/{name}.nc"
            for name in (BASE_NAME, INFERRED_OSM_NAME, INFERRED_PROVIDED_NAME)
        ],
