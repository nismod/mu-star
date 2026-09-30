"""Energy network build: prepare the provided CEB data, cache the OpenStreetMap
inputs, find night-light targets and build three network products.

Products, each written under {data}/processed/energy/networks/<name>/:

- base-mauritius: the provided CEB transmission network (substations, routes,
  generation sites).
- inferred-osm-<region>: OpenStreetMap substations, plants and generators joined
  by the OSM roads that lie near night-light targets (a distribution proxy).
- inferred-provided-<region>: the provided substations, generators and CEB
  backbone joined by the same road network.

Every build rule runs workflow/0-preprocess/energy_build_network.py, a thin
command-line wrapper around energy.build.build_network. Settings live
in config/energy/energy.yaml. Downloads (OpenStreetMap, VIIRS) are opt-in there and run by name:
snakemake -c1 fetch_energy_osm / fetch_energy_nightlights.

Run everything:      snakemake -c1 build_energy_networks
One product, e.g.:   snakemake -c1 data/processed/energy/networks/base-mauritius/base-mauritius.nc

Disruption analysis (what is lost when assets fail) is not part of this
workflow yet; see docs/src/infrastructure-energy.md.
"""

import shlex
from pathlib import Path

from snakemake.exceptions import WorkflowError

from energy import osm as energy_osm
from energy.nightlights import tile_name


configfile: "config/energy/energy.yaml"


ENERGY = config["energy"]
# Where the {data} wildcard resolves when a target is named by rule rather than by path.
ENERGY_DATA_ROOT = config.get("data_root", "data")

REGION = str(ENERGY.get("region", "mauritius-rodrigues")).strip()
if not REGION:
    raise ValueError("energy.region must not be empty (config/energy/energy.yaml)")
REGION_SLUG = energy_osm.region_slug(REGION)

OSM_SETTINGS = ENERGY.get("osm", {})
NETWORK_TYPE = str(OSM_SETTINGS.get("network_type", "drive")).strip() or "drive"
OSM_ALLOW_DOWNLOAD = bool(OSM_SETTINGS.get("allow_download", False))

VECTOR_SUFFIXES = {".parquet", ".geoparquet", ".gpkg", ".geojson"}


def _relative_data_path(value, key, suffixes):
    """Validate an optional user-supplied path from config: relative to the data root, expected extension."""
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute():
        raise ValueError(f"{key} must be relative to the data root, got {value!r}")
    if path.suffix.lower() not in suffixes:
        raise ValueError(f"{key} must end with one of {sorted(suffixes)}, got {value!r}")
    return path.as_posix()


# --- OpenStreetMap cache (relative to the data root) --------------------------
# The paths come from the same helpers the Python code uses, so the rules and
# the library can never disagree about where a cached file lives.
OSM_ROADS_CACHE = energy_osm.roads_cache_relative(REGION, NETWORK_TYPE).as_posix()
OSM_POWER_CACHE = energy_osm.power_cache_relative(REGION).as_posix()
OSM_AOI_CACHE = energy_osm.aoi_cache_relative(REGION).as_posix()
# A user-supplied roads or AOI file (energy.osm.roads / energy.osm.aoi) replaces the cache.
OSM_ROADS = _relative_data_path(OSM_SETTINGS.get("roads"), "energy.osm.roads", VECTOR_SUFFIXES) or OSM_ROADS_CACHE
OSM_AOI = _relative_data_path(OSM_SETTINGS.get("aoi"), "energy.osm.aoi", VECTOR_SUFFIXES) or OSM_AOI_CACHE

# --- Night lights -------------------------------------------------------------
NIGHTLIGHT = ENERGY.get("nightlight", {})
NIGHTLIGHT_SOURCE = NIGHTLIGHT.get("source", {})
NIGHTLIGHT_DIR = f"{{data}}/processed/energy/nightlight/{REGION_SLUG}"
NIGHTLIGHT_TARGETS = f"{NIGHTLIGHT_DIR}/targets.geoparquet"
NIGHTLIGHT_MONTHLY_DIR = NIGHTLIGHT_SOURCE.get("monthly_dir", "incoming/energy/nightlights/viirs-2024-monthly")
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

# --- Products -----------------------------------------------------------------
BASE_NETWORK = ENERGY.get("base_network", {})
INFERRED = ENERGY.get("inferred", {})
PROVIDED_DIR = "{data}/processed/energy/provided"
NETWORKS_DIR = "{data}/processed/energy/networks"
TABLES_DIR = "{data}/out/energy"
BASE_NAME = "base-mauritius"
INFERRED_OSM_NAME = f"inferred-osm-{REGION_SLUG}"
INFERRED_PROVIDED_NAME = f"inferred-provided-{REGION_SLUG}"


def network_outputs(name, *, inferred):
    """The files every build writes for product ``name`` (see energy.build.build_network)."""
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


# Sidecars needed to read a provided ESRI shapefile (the optional .cpg is not required).
PROVIDED_SHAPEFILE_EXTENSIONS = ("shp", "shx", "dbf", "prj")


def require_cached(paths, how):
    """Explain, instead of a bare MissingInputException, when a cached download is absent."""
    missing = [path for path in paths if not Path(path).is_file()]
    if missing:
        listed = "\n".join(f"  - {path}" for path in missing)
        raise WorkflowError(f"Cached input file(s) missing:\n{listed}\n{how}")
    return paths


HOW_TO_FETCH_OSM = (
    "Fetch them once (needs internet): set energy.osm.allow_download: true in "
    "config/energy/energy.yaml, run `snakemake -c1 fetch_energy_osm`, then set it back to false."
)
HOW_TO_FETCH_NIGHTLIGHTS = (
    "Fetch them once (needs internet): set energy.nightlight.source.allow_download: true in "
    "config/energy/energy.yaml, run `snakemake -c1 fetch_energy_nightlights`, then set it back to false."
)


rule fetch_energy_osm:
    """
    Cache the OpenStreetMap inputs for the configured region: roads, power
    features and the area-of-interest outline. Run it once, by name:

        snakemake -c1 fetch_energy_osm

    It contacts OpenStreetMap only when a file is missing AND
    energy.osm.allow_download is true; otherwise it explains how to enable the
    fetch. The cache is deliberately not a Snakemake output: outputs are deleted
    before a rule re-runs, and a download must never be thrown away by accident.
    """
    input:
        script="workflow/0-preprocess/energy_fetch_osm.py",
    params:
        region=REGION,
        network_type=NETWORK_TYPE,
        data_root=ENERGY_DATA_ROOT,
        allow_download="--allow-download" if OSM_ALLOW_DOWNLOAD else "",
    shell:
        """
        python {input.script:q} \
            --region {params.region:q} \
            --network-type {params.network_type:q} \
            --data-root {params.data_root:q} \
            {params.allow_download}
        """


rule prepare_energy_assets:
    """
    Clean the provided CEB source data and write reviewable asset tables.

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
        substations=f"{PROVIDED_DIR}/substations.parquet",
        snapped_substations=f"{PROVIDED_DIR}/snapped_substations.parquet",
        snap_distances=f"{PROVIDED_DIR}/substation_snap_distances.csv",
        routes=f"{PROVIDED_DIR}/transmission_routes.parquet",
        generation_points=f"{PROVIDED_DIR}/generation_points.parquet",
        generation_areas=f"{PROVIDED_DIR}/generation_areas.parquet",
        generators=f"{PROVIDED_DIR}/generators.csv",
        service_weights=f"{PROVIDED_DIR}/service_weights.csv",
        monthly_peak=f"{PROVIDED_DIR}/monthly_peak_demand_mw.csv",
        annual_demand=f"{PROVIDED_DIR}/annual_sector_demand_gwh.csv",
        generator_template="{data}/processed/energy/templates/generators.csv",
        line_template="{data}/processed/energy/templates/lines.csv",
    params:
        input_dir="{data}/incoming/energy/provided",
        output_dir=PROVIDED_DIR,
    shell:
        """
        python {input.script:q} \
            --input-dir {params.input_dir:q} \
            --output-dir {params.output_dir:q} \
            --capacity-reference {input.capacity_reference:q}
        """


rule build_base_energy_network:
    """
    Build the provided CEB transmission network (product base-mauritius).

    Test with:
    snakemake -c1 data/processed/energy/networks/base-mauritius/base-mauritius.nc
    """
    input:
        buses=f"{PROVIDED_DIR}/snapped_substations.parquet",
        routes=f"{PROVIDED_DIR}/transmission_routes.parquet",
        generators=f"{PROVIDED_DIR}/generators.csv",
        script="workflow/0-preprocess/energy_build_network.py",
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
    Cache the monthly VIIRS radiance tiles. Run it once, by name:

        snakemake -c1 fetch_energy_nightlights

    It reaches the image service only when a tile is missing AND
    energy.nightlight.source.allow_download is true; otherwise it explains how
    to enable the fetch. Like the OSM cache, the tiles are not Snakemake outputs
    so a script change can never delete them.
    """
    input:
        script="workflow/0-preprocess/energy_fetch_nightlights.py",
    params:
        out_dir=f"{ENERGY_DATA_ROOT}/{NIGHTLIGHT_MONTHLY_DIR}",
        object_ids=",".join(str(value) for value in NIGHTLIGHT_OBJECT_IDS),
        bbox=",".join(str(value) for value in NIGHTLIGHT_SOURCE.get("bbox", [57, -21, 64, -19])),
        pixel_size_degrees=NIGHTLIGHT_SOURCE.get("pixel_size_degrees", 0.004166666666666667),
        service=NIGHTLIGHT_SOURCE.get("service") or "",
        rendering_rule=NIGHTLIGHT_SOURCE.get("rendering_rule") or "",
        allow_download="--allow-download" if bool(NIGHTLIGHT_SOURCE.get("allow_download", False)) else "",
    shell:
        """
        python {input.script:q} \
            --out-dir {params.out_dir:q} \
            --object-ids {params.object_ids} \
            --bbox {params.bbox} \
            --pixel-size-degrees {params.pixel_size_degrees} \
            --service {params.service:q} \
            --rendering-rule {params.rendering_rule:q} \
            {params.allow_download}
        """


rule build_energy_nightlight_composite:
    """
    Reduce the cached monthly VIIRS tiles to one radiance composite (pixelwise median).

    Test with:
    snakemake -c1 data/processed/energy/nightlight/mauritius-rodrigues/viirs-composite.tif
    """
    input:
        months=lambda wildcards: require_cached(
            [month.format(data=wildcards.data) for month in NIGHTLIGHT_MONTHS], HOW_TO_FETCH_NIGHTLIGHTS
        ),
        script="workflow/0-preprocess/energy_build_nightlight_composite.py",
    output:
        composite=NIGHTLIGHT_BUILT_COMPOSITE,
    shell:
        """
        python {input.script:q} {input.months:q} --output {output.composite:q}
        """


rule build_energy_nightlight_targets:
    """
    Find the night-light targets: bright pixels of the radiance composite inside
    the area of interest, the places the inferred distribution network has to reach.

    Test with:
    snakemake -c1 data/processed/energy/nightlight/mauritius-rodrigues/targets.geoparquet
    """
    input:
        nightlights=NIGHTLIGHT_COMPOSITE,
        aoi=lambda wildcards: require_cached([f"{wildcards.data}/{OSM_AOI}"], HOW_TO_FETCH_OSM)[0],
        script="workflow/0-preprocess/energy_build_nightlight_targets.py",
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
    """Inputs of one inferred product: the road cache and night-light targets for both
    variants, plus the OSM power cache (osm) or the prepared CEB tables (provided)."""
    inputs = {
        "roads": f"{wildcards.data}/{OSM_ROADS}",
        "nightlight_targets": NIGHTLIGHT_TARGETS.format(data=wildcards.data),
        "script": "workflow/0-preprocess/energy_build_network.py",
    }
    if wildcards.variant == "osm":
        inputs["power"] = f"{wildcards.data}/{OSM_POWER_CACHE}"
    require_cached([inputs["roads"], *([inputs["power"]] if "power" in inputs else [])], HOW_TO_FETCH_OSM)
    if wildcards.variant != "osm":
        for name in ("snapped_substations.parquet", "transmission_routes.parquet", "generators.csv"):
            inputs[name.split(".")[0]] = f"{PROVIDED_DIR.format(data=wildcards.data)}/{name}"
    return inputs


rule build_inferred_energy_network:
    """
    Build one inferred product: "osm" joins OpenStreetMap power features, "provided"
    joins the provided substations, generators and CEB backbone, both across the
    night-light-supported OSM road network.

    Test with:
    snakemake -c1 data/processed/energy/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc
    snakemake -c1 data/processed/energy/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc
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
            --inferred-reference-line-length-km {params.reference_line_length_km} \
            --line-length-tolerance-fraction {params.line_length_tolerance_fraction} \
            --generation-capacity-tolerance-fraction {params.generation_capacity_tolerance_fraction} \
            --base-route-gap-tolerance-m {params.base_route_gap_tolerance_m}
        """


rule build_energy_networks:
    """Build all three energy network products."""
    input:
        [
            f"{ENERGY_DATA_ROOT}/processed/energy/networks/{name}/{name}.nc"
            for name in (BASE_NAME, INFERRED_OSM_NAME, INFERRED_PROVIDED_NAME)
        ],
