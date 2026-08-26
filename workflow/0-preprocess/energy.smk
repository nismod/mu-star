"""Prepare provided energy assets and build the base and inferred networks.

Three explicit, provenance-preserving products are built through the single
``energy.network_source.build_network`` dispatch:

- ``base-mauritius`` (source ``base``): the reviewed CEB routed transmission
  topology. This is the canonical operational network.
- ``inferred-osm-<region>`` (source ``inferred-osm``): OSM substations, plants
  and generators as power terminals, with a VIIRS-nightlight-supported OSM road
  subnetwork.
- ``inferred-data-<region>`` (source ``inferred-data``): the same nightlight
  road method rooted on the reviewed substations and generators, preserving the
  CEB backbone.

Interruption analysis is intentionally out of scope for this migration, so the
"given disrupted assets, output disruption metrics" interface is not exposed
yet (see docs/src/infrastructure-energy.md).
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
ENERGY_INFERRED_DATA_NAME = f"inferred-data-{ENERGY_INFERRED_REGION_SLUG}"

# --- Reviewed and cached inputs --------------------------------------------
# The provided/reviewed asset tables and the offline OSM cache are inputs to
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
ENERGY_OSM_ROADS = f"{ENERGY_OSM_ROOT}/roads{_ENERGY_ROADS_SUFFIX}.parquet"
ENERGY_OSM_POWER = f"{ENERGY_OSM_ROOT}/power.parquet"

# --- Nightlight target settings --------------------------------------------
ENERGY_NIGHTLIGHT_REGION = str(
    ENERGY_NIGHTLIGHT.get("region", ENERGY_INFERRED_REGION)
).strip()
ENERGY_NIGHTLIGHT_REGION_SLUG = _energy_slug(ENERGY_NIGHTLIGHT_REGION)
ENERGY_NIGHTLIGHT_RELATIVE = ENERGY_NIGHTLIGHT.get(
    "nightlights",
    f"incoming/energy/nightlights/viirs-{ENERGY_NIGHTLIGHT_REGION_SLUG}-2024.tif",
)
ENERGY_NIGHTLIGHT_AOI_RELATIVE = (
    ENERGY_NIGHTLIGHT.get("aoi")
    or f"incoming/energy/osm/{ENERGY_NIGHTLIGHT_REGION_SLUG}/aoi.parquet"
)
ENERGY_NIGHTLIGHT_DIR = (
    f"{{data}}/processed/energy/nightlight/{ENERGY_NIGHTLIGHT_REGION_SLUG}"
)
ENERGY_NIGHTLIGHT_TARGETS = f"{ENERGY_NIGHTLIGHT_DIR}/targets.geoparquet"

for label, relative_path, suffixes in (
    ("energy.nightlight.nightlights", ENERGY_NIGHTLIGHT_RELATIVE, {".tif", ".tiff"}),
    (
        "energy.nightlight.aoi",
        ENERGY_NIGHTLIGHT_AOI_RELATIVE,
        {".geojson", ".gpkg", ".parquet", ".geoparquet"},
    ),
):
    if not relative_path:
        raise ValueError(f"{label} must not be empty")
    if Path(relative_path).is_absolute():
        raise ValueError(f"{label} must be relative to the selected data root")
    if Path(relative_path).suffix.lower() not in suffixes:
        expected = ", ".join(sorted(suffixes))
        raise ValueError(f"{label} must use one of: {expected}")


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
            for extension in ("shp", "shx", "dbf", "prj", "cpg")
        ],
        routes=[
            f"{{data}}/incoming/energy/provided/power_transmission/PowerGrid.{extension}"
            for extension in ("shp", "shx", "dbf", "prj", "cpg")
        ],
        generation_points=[
            f"{{data}}/incoming/energy/provided/generation_source/GenSource1.{extension}"
            for extension in ("shp", "shx", "dbf", "prj", "cpg")
        ],
        generation_areas=[
            f"{{data}}/incoming/energy/provided/generation_source/GenSource2.{extension}"
            for extension in ("shp", "shx", "dbf", "prj", "cpg")
        ],
        capacity_reference="src/energy/resources/generator_capacity_reference.csv",
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
    run:
        from pathlib import Path

        from energy.intake import prepare_provided_data

        prepare_provided_data(Path(params.input_dir), Path(params.output_dir))


rule build_base_energy_network:
    """
    Build the canonical reviewed CEB routed transmission topology.

    Methodology: ceb-routed-topology-v3. Test with:
    snakemake -c1 data/processed/energy/networks/base-mauritius/base-mauritius.nc
    """
    input:
        buses="{data}/processed/energy/provided/snapped_substations.parquet",
        routes="{data}/processed/energy/provided/transmission_routes.parquet",
        generators="{data}/processed/energy/provided/generators.csv",
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
    run:
        from pathlib import Path

        from energy.network_source import build_network

        build_network(
            "base",
            input_dir=Path(params.input_dir),
            output_dir=Path(params.output_dir),
            export_root=Path(params.export_root),
            output_name=params.output_name,
            overwrite=True,
            base_route_gap_tolerance_m=float(params.route_gap_tolerance_m),
            base_default_voltage_kv=float(params.default_voltage_kv),
            base_topology_capacity_mva=float(params.topology_capacity_mva),
        )


rule build_energy_nightlight_targets:
    """
    Extract VIIRS nightlight connection targets (replaces GridFinder rasters).

    Only target points, their raster mask and provenance metadata are written;
    the inferred builds consume these targets to retain the OSM road subnetwork
    they support. Test with:
    snakemake -c1 data/processed/energy/nightlight/mauritius-rodrigues/targets.geoparquet
    """
    input:
        nightlights=f"{{data}}/{ENERGY_NIGHTLIGHT_RELATIVE}",
        aoi=f"{{data}}/{ENERGY_NIGHTLIGHT_AOI_RELATIVE}",
    output:
        targets_raster=f"{ENERGY_NIGHTLIGHT_DIR}/targets.tif",
        targets=f"{ENERGY_NIGHTLIGHT_DIR}/targets.geoparquet",
        metadata=f"{ENERGY_NIGHTLIGHT_DIR}/metadata.json",
    params:
        output_dir=ENERGY_NIGHTLIGHT_DIR,
        region=ENERGY_NIGHTLIGHT_REGION,
        nightlight_threshold=ENERGY_NIGHTLIGHT.get("nightlight_threshold", 0.1),
    run:
        from pathlib import Path

        from energy.nightlight_targets import build_nightlight_targets

        build_nightlight_targets(
            Path(input.nightlights),
            Path(params.output_dir),
            aoi_path=Path(input.aoi),
            region=params.region,
            nightlight_threshold=float(params.nightlight_threshold),
        )


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
    run:
        import os
        from pathlib import Path

        from energy.network_source import build_network

        # Resolve the OSM cache that build_network reads internally against the
        # same data root Snakemake declares the roads/power inputs under.
        os.environ["MU_STAR_DATA_ROOT"] = str(Path(wildcards.data).resolve())

        build_network(
            "inferred-osm",
            region=params.region,
            input_dir=Path(params.input_dir),
            output_dir=Path(params.output_dir),
            export_root=Path(params.export_root),
            output_name=params.output_name,
            overwrite=True,
            network_type=params.network_type,
            nightlight_targets=Path(input.nightlight_targets),
            nightlight_support_distance_m=float(params.nightlight_support_distance_m),
            max_anchor_distance_m=float(params.max_anchor_distance_m),
            inferred_voltage_kv=float(params.inferred_voltage_kv),
            inferred_capacity_mva=float(params.inferred_capacity_mva),
            inferred_reference_line_length_km=float(params.reference_line_length_km),
            line_length_tolerance_fraction=float(params.line_length_tolerance_fraction),
        )


rule build_inferred_data_energy_network:
    """
    Build the reviewed-data inferred topology using the same nightlight road method.

    Reviewed substations and generators are the power targets and the CEB
    backbone is preserved. Methodology: nightlight-roads-reviewed-power-v1.
    Test with:
    snakemake -c1 data/processed/energy/networks/inferred-data-mauritius-rodrigues/inferred-data-mauritius-rodrigues.nc
    """
    input:
        buses="{data}/processed/energy/provided/snapped_substations.parquet",
        routes="{data}/processed/energy/provided/transmission_routes.parquet",
        generators="{data}/processed/energy/provided/generators.csv",
        roads=f"{{data}}/{ENERGY_OSM_ROADS}",
        nightlight_targets=ENERGY_NIGHTLIGHT_TARGETS,
    output:
        network=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/{ENERGY_INFERRED_DATA_NAME}.nc",
        metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/{ENERGY_INFERRED_DATA_NAME}_metadata.json",
        spatial_nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/geoparquet/{ENERGY_INFERRED_DATA_NAME}-nodes.geoparquet",
        spatial_edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/geoparquet/{ENERGY_INFERRED_DATA_NAME}-edges.geoparquet",
        spatial_manifest=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/geoparquet/{ENERGY_INFERRED_DATA_NAME}-spatial-manifest.json",
        nodes=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/inferred_distribution/inferred_distribution_nodes.csv",
        edges=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/inferred_distribution/inferred_distribution_edges.csv",
        graph_metadata=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/inferred_distribution/inferred_distribution_metadata.json",
        service_weights=f"{ENERGY_NETWORKS_DIR}/{ENERGY_INFERRED_DATA_NAME}/inferred_distribution/service_weights.csv",
        generators=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_DATA_NAME}/generators.csv",
        lines=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_DATA_NAME}/lines.csv",
        validation=f"{ENERGY_TABLES_DIR}/{ENERGY_INFERRED_DATA_NAME}/validation.json",
    params:
        input_dir=ENERGY_PROVIDED_DIR,
        output_dir=ENERGY_NETWORKS_DIR,
        export_root=ENERGY_TABLES_DIR,
        output_name=ENERGY_INFERRED_DATA_NAME,
        region=ENERGY_INFERRED_REGION,
        network_type=ENERGY_INFERRED_NETWORK_TYPE,
        max_anchor_distance_m=ENERGY_INFERRED.get("max_anchor_distance_m", 1000),
        inferred_voltage_kv=ENERGY_INFERRED.get("topology_voltage_kv", 11),
        inferred_capacity_mva=ENERGY_INFERRED.get("topology_capacity_mva", 5),
        reference_line_length_km=ENERGY_INFERRED.get("ceb_total_line_length_km", 10492.2),
        line_length_tolerance_fraction=ENERGY_INFERRED.get("line_length_tolerance_fraction", 0.10),
        generation_capacity_tolerance_fraction=ENERGY_INFERRED.get(
            "generation_capacity_tolerance_fraction", 0.10
        ),
        nightlight_support_distance_m=ENERGY_NIGHTLIGHT.get("nightlight_support_distance_m", 1000),
    run:
        import os
        from pathlib import Path

        from energy.network_source import build_network

        # Resolve the OSM cache that build_network reads internally against the
        # same data root Snakemake declares the roads input under.
        os.environ["MU_STAR_DATA_ROOT"] = str(Path(wildcards.data).resolve())

        build_network(
            "inferred-data",
            region=params.region,
            input_dir=Path(params.input_dir),
            output_dir=Path(params.output_dir),
            export_root=Path(params.export_root),
            output_name=params.output_name,
            overwrite=True,
            network_type=params.network_type,
            nightlight_targets=Path(input.nightlight_targets),
            nightlight_support_distance_m=float(params.nightlight_support_distance_m),
            max_anchor_distance_m=float(params.max_anchor_distance_m),
            inferred_voltage_kv=float(params.inferred_voltage_kv),
            inferred_capacity_mva=float(params.inferred_capacity_mva),
            inferred_reference_line_length_km=float(params.reference_line_length_km),
            line_length_tolerance_fraction=float(params.line_length_tolerance_fraction),
            generation_capacity_tolerance_fraction=float(
                params.generation_capacity_tolerance_fraction
            ),
        )


rule energy_base_network:
    """Build the canonical base-mauritius network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_BASE_NAME}/{ENERGY_BASE_NAME}.nc",


rule energy_inferred_osm_network:
    """Build the OSM-power inferred network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_OSM_NAME}/{ENERGY_INFERRED_OSM_NAME}.nc",


rule energy_inferred_data_network:
    """Build the reviewed-data inferred network."""
    input:
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_DATA_NAME}/{ENERGY_INFERRED_DATA_NAME}.nc",


rule energy_network:
    """Build all three energy networks (base and both inferred products)."""
    input:
        rules.energy_base_network.input,
        rules.energy_inferred_osm_network.input,
        rules.energy_inferred_data_network.input,


rule energy_exports:
    """Publish checksum-linked GeoParquet sidecars and review tables per product."""
    input:
        # base-mauritius
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_BASE_NAME}/geoparquet/{ENERGY_BASE_NAME}-spatial-manifest.json",
        f"{ENERGY_DATA_ROOT}/out/energy/{ENERGY_BASE_NAME}/validation.json",
        # inferred-osm
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_OSM_NAME}/geoparquet/{ENERGY_INFERRED_OSM_NAME}-spatial-manifest.json",
        f"{ENERGY_DATA_ROOT}/out/energy/{ENERGY_INFERRED_OSM_NAME}/validation.json",
        # inferred-data
        f"{ENERGY_DATA_ROOT}/processed/energy/networks/{ENERGY_INFERRED_DATA_NAME}/geoparquet/{ENERGY_INFERRED_DATA_NAME}-spatial-manifest.json",
        f"{ENERGY_DATA_ROOT}/out/energy/{ENERGY_INFERRED_DATA_NAME}/validation.json",
