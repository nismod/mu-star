"""Build and save one energy network product: the provided base network or an inferred one.

``build_network(source, ...)`` is the single entry point used by the workflow
rules and the notebooks. It reads prepared inputs, assembles buses and lines,
validates them, writes the PyPSA network plus GeoParquet layers and metadata,
and logs every advisory warning so a run is never silently "fine".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import geopandas as gpd
import networkx as nx
import pandas as pd
import pypsa
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union

import energy.osm as osm
from energy.base_topology import (
    CEB_SUBSTATION_NAMES,
    DerivedBaseTopology,
    derive_base_topology,
)
from energy.distribution_network import (
    DEFAULT_MAX_ANCHOR_DISTANCE_M,
    build_inferred_distribution_graph,
    write_inferred_distribution_tables,
)
from energy.network import assert_fixed_capacity, build_topology_network
from energy.network_tables import (
    CEB_REPORTED_INSTALLED_GENERATION_MW,
    CEB_TOTAL_NETWORK_LENGTH_KM,
    CEB_TOTAL_NETWORK_LENGTH_SOURCE,
    CEB_TRANSMISSION_LENGTH_KM,
    CEB_TRANSMISSION_LENGTH_SOURCE,
    GENERATOR_REQUIRED_COLUMNS,
    validate_model_tables,
    write_model_tables,
)
from energy.paths import network_output_dir, processed_energy_dir
from energy.spatial_export import (
    spatial_export_paths,
    write_network_geoparquet,
)

logger = logging.getLogger(__name__)

BASE_REQUIRED_FILES = (
    "snapped_substations.parquet",
    "transmission_routes.parquet",
    "generators.csv",
)
PROVIDED_POWER_REQUIRED_FILES = (
    "snapped_substations.parquet",
    "generators.csv",
)
BASE_METHODOLOGY = "ceb-routed-topology-v3"
INFERRED_OSM_METHODOLOGY = "nightlight-roads-osm-power-v1"
INFERRED_PROVIDED_METHODOLOGY = "nightlight-roads-provided-power-v1"
DEFAULT_NIGHTLIGHT_SUPPORT_DISTANCE_M = 1_000.0

BASE_LINE_LENGTH_SCOPE = "CEB 66 kV transmission circuit length"
BASE_LINE_LENGTH_NOTE = (
    "CEB reports 442 km overhead plus 36.9 km underground at 66 kV. "
    "The routed base model is a geographic corridor model and may not retain "
    "every parallel circuit represented by the published circuit-km total."
)
INFERRED_LINE_LENGTH_SCOPE = "CEB total transmission, medium-voltage and low-voltage circuit length"
INFERRED_LINE_LENGTH_NOTE = (
    "CEB reports 10,492.2 km across overhead and underground transmission, "
    "medium-voltage distribution and low-voltage distribution. This check "
    "compares that total directly with the nightlight-supported OSM road "
    "subnetwork plus the provided CEB backbone where available. Geographic "
    "road length and electrical circuit-km remain different quantities."
)


@dataclass(frozen=True)
class NetworkBuildOutputs:
    network: Path
    metadata: Path
    spatial_nodes: Path | None = None
    spatial_edges: Path | None = None
    spatial_manifest: Path | None = None
    inferred_nodes: Path | None = None
    inferred_edges: Path | None = None
    inferred_metadata: Path | None = None
    generators: Path | None = None
    lines: Path | None = None
    validation: Path | None = None


def _file_sha256(path: Path) -> str:
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _log_validation(product: str, validation: dict[str, object]) -> None:
    """Log advisory warnings so they show up in the Snakemake log, not only in validation.json."""
    for warning in validation.get("warnings", []):
        logger.warning("%s: %s", product, warning)


def _missing_files(input_dir: Path, names: tuple[str, ...]) -> list[Path]:
    return [input_dir / name for name in names if not (input_dir / name).exists()]


def _load_provided_inputs(
    input_dir: Path,
) -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoDataFrame,
    pd.DataFrame,
]:
    missing = _missing_files(input_dir, BASE_REQUIRED_FILES)
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"Cannot build the base network until these prepared files exist:\n{formatted}")

    buses = gpd.read_parquet(input_dir / "snapped_substations.parquet")
    routes = gpd.read_parquet(input_dir / "transmission_routes.parquet")
    generators = pd.read_csv(input_dir / "generators.csv")
    return buses, routes, generators


def _complete_generators(generators: pd.DataFrame) -> pd.DataFrame:
    complete = generators[list(GENERATOR_REQUIRED_COLUMNS)].notna().all(axis=1)
    return generators.loc[complete].copy()


def _write_network(path: Path, network: pypsa.Network) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert_fixed_capacity(network)
    network.export_to_netcdf(path)
    return path


def _write_metadata(path: Path, metadata: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _ceb_topology_validation(topology: DerivedBaseTopology) -> dict[str, object]:
    """Check the CEB-map closures that can be resolved in the supplied vectors."""
    required_ids = set(CEB_SUBSTATION_NAMES)
    available_ids = set(topology.buses["bus_id"].astype(str))
    if not required_ids <= available_ids:
        return {
            "status": "not_applicable",
            "reason": "The input is not the complete 18-substation CEB Mauritius dataset.",
        }

    graph = nx.MultiGraph(topology.lines[["bus0", "bus1"]].itertuples(index=False, name=None))
    retained_route_parts = set(topology.lines["source_route_part_id"].dropna().astype(str))
    checks = {
        "six_meaningful_cycles": topology.meaningful_cycle_count >= 6,
        "amaury_three_way_junction": len(graph["SUB_004"]) >= 3,
        "la_chaumiere_through_connection": len(graph["SUB_010"]) >= 2,
        "ebene_through_connection": len(graph["SUB_011"]) >= 2,
        "ebene_wooton_route_retained": "ROUTE_001_PART_008" in retained_route_parts,
        "la_chaumiere_henrietta_route_retained": ("ROUTE_001_PART_012" in retained_route_parts),
        "parallel_source_route_retained": topology.parallel_edge_count >= 1,
        "operating_voltage_is_66_kv": topology.lines["v_nom_kv"].eq(66).all(),
    }
    checks = {name: bool(passed) for name, passed in checks.items()}
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "status": "pass" if not failed else "warning",
        "reference": "provided CEB 2025 network map",
        "checks": checks,
        "failed_checks": failed,
        "voltage_note": (
            "The CEB map's blue 132 kV construction class operates at 66 kV. "
            "The vectors do not retain enough style data to assign that design "
            "class per circuit, so PyPSA v_nom remains the operating 66 kV."
        ),
    }


def _build_base_network(
    *,
    input_dir: Path,
    network_path: Path,
    metadata_path: Path,
    table_output_dir: Path | None,
    reference_line_length_km: float,
    line_length_tolerance_fraction: float,
    reference_generation_capacity_mw: float,
    generation_capacity_tolerance_fraction: float,
    route_gap_tolerance_m: float,
    default_voltage_kv: float,
    topology_capacity_mva: float,
) -> NetworkBuildOutputs:
    snapped_substations, transmission_routes, generators = _load_provided_inputs(input_dir)
    topology = derive_base_topology(
        snapped_substations,
        transmission_routes,
        route_gap_tolerance_m=route_gap_tolerance_m,
        default_voltage_kv=default_voltage_kv,
        topology_capacity_mva=topology_capacity_mva,
    )
    buses = topology.buses
    lines = topology.lines
    ceb_topology_validation = _ceb_topology_validation(topology)
    table_outputs = None
    if table_output_dir is None:
        validation = validate_model_tables(
            buses,
            lines,
            generators,
            source="base",
            reference_line_length_km=reference_line_length_km,
            reference_line_length_scope=BASE_LINE_LENGTH_SCOPE,
            reference_line_length_source=CEB_TRANSMISSION_LENGTH_SOURCE,
            reference_line_length_note=BASE_LINE_LENGTH_NOTE,
            line_length_tolerance_fraction=line_length_tolerance_fraction,
            reference_generation_capacity_mw=reference_generation_capacity_mw,
            generation_capacity_tolerance_fraction=(generation_capacity_tolerance_fraction),
            allow_incomplete_generators=True,
        )
    else:
        table_outputs, validation = write_model_tables(
            buses,
            lines,
            generators,
            table_output_dir,
            source="base",
            reference_line_length_km=reference_line_length_km,
            reference_line_length_scope=BASE_LINE_LENGTH_SCOPE,
            reference_line_length_source=CEB_TRANSMISSION_LENGTH_SOURCE,
            reference_line_length_note=BASE_LINE_LENGTH_NOTE,
            line_length_tolerance_fraction=line_length_tolerance_fraction,
            reference_generation_capacity_mw=reference_generation_capacity_mw,
            generation_capacity_tolerance_fraction=(generation_capacity_tolerance_fraction),
            allow_incomplete_generators=True,
        )
    if validation["errors"]:
        raise ValueError("Invalid base network tables:\n- " + "\n- ".join(validation["errors"]))
    _log_validation(network_path.stem, validation)
    network_generators = _complete_generators(generators)
    network = build_topology_network(buses, lines, network_generators)
    _write_network(network_path, network)
    spatial_dir = network_path.parent / "geoparquet"
    spatial_outputs = spatial_export_paths(
        spatial_dir,
        network_id=network_path.stem,
    )
    _write_metadata(
        metadata_path,
        {
            "source": "base",
            "methodology": BASE_METHODOLOGY,
            "line_geometry": "routed_wkt",
            "input_dir": str(input_dir),
            "network": str(network_path),
            "spatial_nodes": str(spatial_outputs.nodes),
            "spatial_edges": str(spatial_outputs.edges),
            "spatial_manifest": str(spatial_outputs.manifest),
            "has_demand": False,
            "snapshots": 0,
            "buses": len(network.buses),
            "lines": len(network.lines),
            "generators": len(network.generators),
            "generator_records": len(generators),
            "generator_output_capacity_mw": float(network.generators.p_nom.sum()),
            "loads": 0,
            "inferred": False,
            "derived": True,
            "stage": "topology_only",
            "substations": topology.substation_count,
            "junctions": topology.junction_count,
            "connected_components": topology.connected_components,
            "route_gap_connectors": topology.route_gap_count,
            "route_gap_length_km": topology.route_gap_length_km,
            "route_gap_tolerance_m": route_gap_tolerance_m,
            "cycle_rank": topology.cycle_rank,
            "meaningful_cycle_count": topology.meaningful_cycle_count,
            "parallel_edge_count": topology.parallel_edge_count,
            "source_route_length_km": topology.source_route_length_km,
            "retained_source_length_km": topology.retained_source_length_km,
            "retained_source_fraction": (topology.retained_source_length_km / topology.source_route_length_km),
            "ceb_topology_validation": ceb_topology_validation,
            "default_voltage_kv": default_voltage_kv,
            "topology_capacity_mva": topology_capacity_mva,
            "electrical_values_note": (
                "Voltages are provided CEB 66 kV values; line capacities are non-binding topology placeholders."
            ),
            "model_line_length_km": validation["totals"]["line_length_km"],
            "line_length_validation": validation["checks"]["line_length_against_published_ceb_total"],
            "human_tables": str(table_output_dir) if table_output_dir else None,
            "validation_status": validation["status"],
            "validation_warnings": validation["warnings"],
        },
    )
    spatial_outputs = write_network_geoparquet(
        buses,
        lines,
        spatial_dir,
        network_id=network_path.stem,
        network_source="base",
        methodology=BASE_METHODOLOGY,
        source_network_path=network_path,
        source_metadata_path=metadata_path,
        default_region="mauritius",
        publish_voltage=True,
        publish_capacity=False,
        electrical_values_note=(
            "Voltages are provided CEB 66 kV values; line capacities are non-binding topology placeholders."
        ),
        stage="topology_only",
    )
    return NetworkBuildOutputs(
        network=network_path,
        metadata=metadata_path,
        spatial_nodes=spatial_outputs.nodes,
        spatial_edges=spatial_outputs.edges,
        spatial_manifest=spatial_outputs.manifest,
        generators=table_outputs.generators if table_outputs else None,
        lines=table_outputs.lines if table_outputs else None,
        validation=table_outputs.validation if table_outputs else None,
    )


def _empty_generators() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "generator_id",
            "bus_id",
            "carrier",
            "output_capacity_mw",
            "capacity_basis",
            "marginal_cost",
        ]
    )


def _node_bus_frame(graph: nx.Graph) -> gpd.GeoDataFrame:
    rows = [
        {
            "bus_id": node,
            "kind": attrs.get("kind"),
            "asset_id": attrs.get("asset_id"),
            "is_root": bool(attrs.get("is_root", False)),
            "inferred": bool(attrs.get("inferred", False)),
            "source": attrs.get("source", "provided_substation"),
            "region": attrs.get("region"),
            "provisional_root": bool(attrs.get("provisional_root", False)),
            "anchor_status": attrs.get("anchor_status"),
            "anchor_distance_m": attrs.get("anchor_distance_m"),
            "v_nom_kv": attrs.get("v_nom_kv"),
            "geometry": Point(float(attrs["x"]), float(attrs["y"])),
        }
        for node, attrs in graph.nodes(data=True)
        if "x" in attrs and "y" in attrs
    ]
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _graph_line_frame(
    graph: nx.Graph,
    *,
    default_voltage_kv: float,
    transmission_voltage_kv: float,
    default_capacity_mva: float,
    transmission_capacity_mva: float | None = None,
    anchor_capacity_mva: float | None = None,
) -> gpd.GeoDataFrame:
    """Turn graph edges into a PyPSA lines table.

    Voltage and rating depend on what the edge is: the provided transmission
    backbone gets ``transmission_voltage_kv`` / ``transmission_capacity_mva``,
    an asset-to-road connector gets ``anchor_capacity_mva`` (the substation's
    transformer rating), and every road segment gets the distribution
    placeholders ``default_voltage_kv`` / ``default_capacity_mva``.
    """
    transmission_capacity_mva = default_capacity_mva if transmission_capacity_mva is None else transmission_capacity_mva
    anchor_capacity_mva = default_capacity_mva if anchor_capacity_mva is None else anchor_capacity_mva
    columns = [
        "line_id",
        "bus0",
        "bus1",
        "v_nom_kv",
        "length_km",
        "s_nom_mva",
        "inferred",
        "source",
        "region",
        "stage",
        "geometry",
    ]
    rows = []
    for number, (bus0, bus1, attrs) in enumerate(graph.edges(data=True), start=1):
        if bus0 == bus1:
            continue
        node0 = graph.nodes[bus0]
        node1 = graph.nodes[bus1]
        geometry = attrs.get("geometry")
        if geometry is None or geometry.geom_type != "LineString":
            geometry = LineString(
                [
                    (float(node0["x"]), float(node0["y"])),
                    (float(node1["x"]), float(node1["y"])),
                ]
            )
        line_source = str(attrs.get("source") or "")
        is_transmission = line_source == "provided_transmission"
        is_anchor = line_source.endswith("_anchor")
        line_voltage_kv = transmission_voltage_kv if is_transmission else default_voltage_kv
        line_capacity_mva = (
            transmission_capacity_mva if is_transmission else anchor_capacity_mva if is_anchor else default_capacity_mva
        )
        rows.append(
            {
                "line_id": str(attrs.get("edge_id") or f"inferred_line_{number:06d}"),
                "bus0": bus0,
                "bus1": bus1,
                "v_nom_kv": line_voltage_kv,
                "length_km": max(float(attrs.get("length_km", 0.0)), 0.001),
                "s_nom_mva": line_capacity_mva,
                "inferred": True,
                "source": line_source,
                "region": attrs.get("region"),
                "stage": attrs.get("stage", "connectivity_only"),
                "geometry": geometry,
            }
        )
    return gpd.GeoDataFrame(
        rows,
        columns=columns,
        geometry="geometry",
        crs="EPSG:4326",
    )


def _equal_service_weights(bus_frame: gpd.GeoDataFrame) -> pd.DataFrame:
    bus_ids = bus_frame["bus_id"].astype(str)
    if bus_ids.empty:
        return pd.DataFrame(columns=["bus_id", "service_weight"])
    return pd.DataFrame(
        {
            "bus_id": bus_ids,
            "service_weight": [1 / len(bus_ids)] * len(bus_ids),
        }
    )


def _largest_road_component_centroid(roads: gpd.GeoDataFrame | None, *, region: str) -> Point:
    """Point on the largest connected road component, used as a stand-in root for a region with no power assets."""
    if roads is None or roads.empty:
        raise ValueError(
            f"No roads are available for {region!r}, so no provisional power root can be placed. "
            "Check the region name and the cached OSM roads for that region."
        )

    metric_crs = roads.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError(f"Could not choose a metric CRS for the roads of {region!r}")
    prepared = roads.to_crs(metric_crs).explode(index_parts=False).copy()
    prepared = prepared[prepared.geometry.geom_type.eq("LineString")]
    if prepared.empty:
        raise ValueError(f"The roads of {region!r} contain no LineStrings")

    road_graph = nx.Graph()
    for row in prepared.itertuples():
        coords = list(row.geometry.coords)
        start = (round(float(coords[0][0]), 1), round(float(coords[0][1]), 1))
        end = (round(float(coords[-1][0]), 1), round(float(coords[-1][1]), 1))
        if start == end:
            continue
        road_graph.add_edge(
            start,
            end,
            geometry=row.geometry,
            length=float(row.geometry.length),
        )
    if road_graph.number_of_edges() == 0:
        linework = unary_union(list(prepared.geometry))
    else:
        component = max(
            nx.connected_components(road_graph),
            key=lambda nodes: road_graph.subgraph(nodes).size(weight="length"),
        )
        component_lines = [
            attrs["geometry"]
            for start, end, attrs in road_graph.edges(data=True)
            if start in component and end in component
        ]
        linework = unary_union(component_lines)

    root = nearest_points(linework, linework.centroid)[0]
    return gpd.GeoSeries([root], crs=metric_crs).to_crs("EPSG:4326").iloc[0]


def _empty_power_assets() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "asset_id": [],
            "asset_kind": [],
            "source": [],
            "region": [],
            "provisional_root": [],
            "geometry": [],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def _normalise_osm_power_assets(
    power_features: gpd.GeoDataFrame | None,
    *,
    region: str,
) -> gpd.GeoDataFrame:
    """Keep OSM substations, plants and generators as candidate power roots."""
    if power_features is None or power_features.empty:
        return _empty_power_assets()
    power_features = power_features.copy()
    if "power" not in power_features:
        raise ValueError("OSM power features must contain a power column")
    power_features["power"] = power_features["power"].astype(str).str.lower()
    power_features = power_features[power_features["power"].isin({"substation", "plant", "generator"})].copy()
    power_features = power_features.reset_index(drop=True)
    if power_features.empty:
        return _empty_power_assets()

    assets = power_features.copy()
    if assets.crs is None:
        assets = assets.set_crs("EPSG:4326")
    if "bus_id" not in assets.columns:
        assets["bus_id"] = [f"{region.upper()}_POWER_{number:03d}" for number in range(1, len(assets) + 1)]
    geometry = assets.geometry
    non_points = ~geometry.geom_type.eq("Point")
    if non_points.any():
        geometry = geometry.copy()
        geometry.loc[non_points] = geometry.loc[non_points].representative_point()
    return gpd.GeoDataFrame(
        {
            "asset_id": assets["bus_id"].astype(str).to_numpy(),
            "asset_kind": assets["power"]
            .map({"substation": "substation", "plant": "generator", "generator": "generator"})
            .to_numpy(),
            "source": assets["source"].astype(str).to_numpy() if "source" in assets else ["osm_power"] * len(assets),
            "region": assets["region"].astype(str).to_numpy()
            if "region" in assets
            else [osm.region_slug(region)] * len(assets),
            "provisional_root": [False] * len(assets),
        },
        geometry=geometry,
        crs=assets.crs,
    ).to_crs("EPSG:4326")


def _provided_power_assets(
    input_dir: Path,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    missing = _missing_files(input_dir, PROVIDED_POWER_REQUIRED_FILES)
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(
            f"Cannot build the provided-data inferred network until these prepared files exist:\n{formatted}"
        )
    substations = gpd.read_parquet(input_dir / "snapped_substations.parquet").to_crs("EPSG:4326")
    substations = gpd.GeoDataFrame(
        {
            "asset_id": substations["bus_id"].astype(str),
            "asset_kind": "substation",
            "source": "provided_substation",
            "region": "mauritius",
            "provisional_root": False,
            "geometry": substations.geometry,
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    generators = pd.read_csv(input_dir / "generators.csv")
    if {"lon", "lat"} <= set(generators):
        has_coordinates = (
            pd.to_numeric(generators["lon"], errors="coerce").notna()
            & pd.to_numeric(generators["lat"], errors="coerce").notna()
        )
        coordinate_rows = generators.loc[has_coordinates].copy()
    else:
        coordinate_rows = generators.iloc[0:0].copy()
    generator_assets = gpd.GeoDataFrame(
        {
            "asset_id": coordinate_rows["generator_id"].astype(str),
            "asset_kind": "generator",
            "source": "provided_generator",
            "region": "mauritius",
            "provisional_root": False,
            "geometry": [
                Point(float(lon), float(lat))
                for lon, lat in coordinate_rows[["lon", "lat"]].itertuples(index=False, name=None)
            ],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    assets = gpd.GeoDataFrame(
        pd.concat([substations, generator_assets], ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326",
    )
    return assets, generators


def _provisional_power_root(region: str, roads: gpd.GeoDataFrame | None) -> gpd.GeoDataFrame:
    centroid = _largest_road_component_centroid(roads, region=region)
    return gpd.GeoDataFrame(
        {
            "asset_id": [f"{region.upper()}_PROVISIONAL_ROOT"],
            "asset_kind": ["substation"],
            "source": ["provisional_road_centroid"],
            "region": [osm.region_slug(region)],
            "provisional_root": [True],
            "geometry": [centroid],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def _inferred_table_dir(result_dir: Path) -> Path:
    return result_dir / "inferred_distribution"


def _power_asset_anchor_counts(graph: nx.Graph) -> tuple[int, int]:
    statuses = [attrs.get("anchor_status") for _, attrs in graph.nodes(data=True) if attrs.get("is_root")]
    return statuses.count("anchored"), statuses.count("unanchored")


def _osm_road_envelope_validation(
    roads: gpd.GeoDataFrame,
    *,
    reference_line_length_km: float,
    tolerance_fraction: float,
) -> dict[str, object]:
    """Compare the de-duplicated road envelope with CEB circuit-km."""
    graph = build_inferred_distribution_graph(
        _empty_power_assets(),
        osm_distribution_lines=roads,
    )
    model_total_km = sum(
        float(attrs.get("length_km", 0.0)) for _, _, attrs in graph.edges(data=True) if attrs.get("source") == "osm"
    )
    relative_difference = abs(model_total_km - reference_line_length_km) / float(reference_line_length_km)
    return {
        "status": "pass" if relative_difference <= tolerance_fraction else "warning",
        "model_total_km": model_total_km,
        "model_all_lines_total_km": model_total_km,
        "included_model_sources": ["osm_road_envelope"],
        "reference_total_km": float(reference_line_length_km),
        "relative_difference": relative_difference,
        "tolerance_fraction": tolerance_fraction,
        "reference_scope": INFERRED_LINE_LENGTH_SCOPE,
        "reference_source": CEB_TOTAL_NETWORK_LENGTH_SOURCE,
        "comparison_note": INFERRED_LINE_LENGTH_NOTE,
    }


def _road_component_count(roads: gpd.GeoDataFrame) -> int:
    """Count endpoint-connected road components per island.

    The final network graph joins lines only where they share an endpoint, so
    this mirrors the connectivity the build actually produces.
    """
    total = 0
    for _, block in roads.groupby("region", dropna=False):
        graph = nx.Graph()
        for line in block.geometry:
            coords = list(line.coords)
            graph.add_edge(
                tuple(round(value, 6) for value in coords[0]),
                tuple(round(value, 6) for value in coords[-1]),
            )
        total += nx.number_connected_components(graph)
    return total


def _road_endpoints(line: LineString) -> tuple[tuple[float, float], tuple[float, float]]:
    coords = list(line.coords)
    return (
        tuple(round(value, 6) for value in coords[0]),
        tuple(round(value, 6) for value in coords[-1]),
    )


def _reconnect_supported_roads(
    supported: gpd.GeoDataFrame,
    full_envelope: gpd.GeoDataFrame,
    metric_crs: object,
) -> gpd.GeoDataFrame:
    """Reconnect stranded supported components along real roads.

    The nightlight support filter can strand a lit cluster (e.g. Le Morne) by
    dropping an unlit stretch of connecting road. The full road envelope is
    connected, so for each stranded component restore the shortest real-road path
    (from the full envelope, weighted by length) back to its island's main
    network. Restored roads are real segments tagged ``source="road_link"``.
    """
    added_rows: list[dict[str, object]] = []
    for region_name, block in supported.groupby("region", dropna=False):
        support_graph = nx.Graph()
        for line in block.geometry:
            start, end = _road_endpoints(line)
            support_graph.add_edge(start, end)
        components = sorted(nx.connected_components(support_graph), key=len, reverse=True)
        if len(components) <= 1:
            continue

        if region_name is None or (isinstance(region_name, float) and pd.isna(region_name)):
            full_block = full_envelope[full_envelope["region"].isna()]
        else:
            full_block = full_envelope[full_envelope["region"] == region_name]
        full_lengths_m = full_block.to_crs(metric_crs).geometry.length.to_numpy()
        full_graph = nx.Graph()
        for line, length_m in zip(full_block.geometry, full_lengths_m):
            start, end = _road_endpoints(line)
            if start == end:
                continue
            if not full_graph.has_edge(start, end):
                full_graph.add_edge(start, end, length=float(length_m), geometry=line)

        sources = {node for node in components[0] if node in full_graph}
        if not sources:
            continue
        distances = nx.multi_source_dijkstra_path_length(full_graph, sources, weight="length")
        restored_edges = {frozenset(edge) for edge in support_graph.edges}
        for component in components[1:]:
            reachable = [node for node in component if node in distances]
            if not reachable:
                continue
            nearest = min(reachable, key=lambda node: distances[node])
            _, path = nx.multi_source_dijkstra(full_graph, sources, target=nearest, weight="length")
            for start, end in pairwise(path):
                edge_key = frozenset((start, end))
                if edge_key in restored_edges:
                    continue
                restored_edges.add(edge_key)
                edge_data = full_graph.get_edge_data(start, end)
                added_rows.append(
                    {
                        "region": region_name,
                        "source": "road_link",
                        "geometry": edge_data["geometry"],
                        "_link_km": edge_data["length"] / 1000,
                    }
                )
    if not added_rows:
        return gpd.GeoDataFrame(
            {"region": [], "source": [], "geometry": [], "_link_km": []},
            geometry="geometry",
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(added_rows, geometry="geometry", crs="EPSG:4326")


def _nightlight_supported_roads(
    roads: gpd.GeoDataFrame,
    targets: gpd.GeoDataFrame,
    *,
    support_distance_m: float,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    """Retain the road subnetwork supported by nightlight targets.

    Roads within the configured distance of a VIIRS-derived target are kept.
    Every supported road is retained, and stranded components (lit clusters the
    support filter cut off, e.g. Le Morne) are reconnected to the main network
    along the shortest real-road path from the full envelope, so the result is a
    connected, road-following network.
    """
    if support_distance_m < 0:
        raise ValueError("nightlight_support_distance_m must be non-negative")
    if roads.empty:
        raise ValueError("Cannot select a nightlight-supported network without roads")
    if targets.empty:
        raise ValueError("Cannot select a nightlight-supported network without targets")
    prepared = roads.to_crs("EPSG:4326").explode(index_parts=False).reset_index(drop=True)
    prepared = prepared[prepared.geometry.geom_type.eq("LineString")].copy()
    if prepared.empty:
        raise ValueError("The OSM road envelope contains no LineStrings")
    if "region" not in prepared:
        prepared["region"] = "region"

    metric_crs = prepared.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Could not select a metric CRS for the nightlight road filter")
    metric_roads = prepared.to_crs(metric_crs)
    support_area = targets.to_crs(metric_crs).geometry.buffer(support_distance_m).union_all()
    full_envelope = prepared
    prepared = prepared.loc[metric_roads.geometry.intersects(support_area)].copy()
    if prepared.empty:
        raise ValueError("No OSM roads fall within the nightlight support area")

    selected = prepared.copy()

    # The nightlight support filter can drop an unlit stretch of connecting road
    # and strand a lit cluster (e.g. Le Morne) as its own component. Reconnect
    # each stranded component to its island's main network along the shortest
    # real-road path from the full envelope, so the network stays road-following.
    links = _reconnect_supported_roads(selected, full_envelope, metric_crs)
    if not links.empty:
        selected = gpd.GeoDataFrame(
            pd.concat(
                [selected, links[["region", "source", "geometry"]]],
                ignore_index=True,
            ),
            geometry="geometry",
            crs="EPSG:4326",
        )

    selected_length_km = float(selected.to_crs(metric_crs).geometry.length.sum() / 1000)
    all_length_km = float(roads.to_crs(metric_crs).geometry.length.sum() / 1000)
    metadata = {
        "support_distance_m": float(support_distance_m),
        "support_point_count": len(targets),
        "input_road_features": len(roads),
        "supported_road_features": len(selected),
        "supported_road_feature_fraction": len(selected) / len(roads),
        "input_road_length_km": all_length_km,
        "supported_road_length_km": selected_length_km,
        "supported_road_length_fraction": (selected_length_km / all_length_km if all_length_km else 0.0),
        "candidate_component_count": _road_component_count(prepared),
        "retained_component_count": _road_component_count(selected),
        "reconnection_road_segments": len(links),
        "reconnection_length_km": (float(links["_link_km"].sum()) if not links.empty else 0.0),
    }
    return selected, metadata


def _provided_generators_for_inferred(
    generators: pd.DataFrame,
) -> pd.DataFrame:
    """Point provided generators at the graph's ``bus::<id>`` node names; a blank bus id stays blank."""
    prepared = generators.copy()
    bus_id = prepared["bus_id"]
    prepared["bus_id"] = bus_id.where(bus_id.isna(), "bus::" + bus_id.astype(str))
    return prepared


def _highway_class_breakdown(roads: gpd.GeoDataFrame | None) -> dict[str, int]:
    """Count OSM road features by their retained ``highway`` class.

    Lets a build report verify that footpaths and tracks are excluded. Caches
    written before the ``highway`` column was retained report every feature as
    ``"unlabelled"`` so the totals still add up.
    """
    if roads is None or len(roads) == 0:
        return {}
    if "highway" not in roads:
        return {"unlabelled": len(roads)}
    classes = roads["highway"].astype("string").str.strip().str.lower().replace("", pd.NA).fillna("unlabelled")
    counts = classes.value_counts()
    return {str(name): int(count) for name, count in counts.items()}


def _insert_transformer_junctions(
    buses: gpd.GeoDataFrame,
    lines: gpd.GeoDataFrame,
    *,
    capacity_mva: float,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split every bus carrying more than one line voltage into one bus per level.

    A node where the transmission backbone meets distribution is physically a
    transformer, and PyPSA needs one voltage per bus. Each such node becomes one
    bus per voltage (the lowest keeps the original id) joined by a ``Transformer``;
    lines reconnect to the split bus matching their own voltage.
    """
    transformer_columns = ["transformer_id", "bus0", "bus1", "v_nom0_kv", "v_nom1_kv", "s_nom_mva", "geometry"]
    incident: dict[str, set[float]] = {}
    for bus0, bus1, voltage in zip(lines["bus0"], lines["bus1"], lines["v_nom_kv"]):
        incident.setdefault(str(bus0), set()).add(float(voltage))
        incident.setdefault(str(bus1), set()).add(float(voltage))
    mixed = {bus: sorted(volts) for bus, volts in incident.items() if len(volts) > 1}
    if not mixed:
        empty = gpd.GeoDataFrame(columns=transformer_columns, geometry="geometry", crs="EPSG:4326")
        return buses, lines, empty

    def split_id(bus: str, voltage: float, levels: list[float]) -> str:
        return bus if voltage == levels[0] else f"{bus}::{round(voltage)}kv"

    bus_lookup = buses.copy()
    bus_lookup["bus_id"] = bus_lookup["bus_id"].astype(str)
    bus_lookup = bus_lookup.set_index("bus_id")
    extra_bus_rows: list[dict] = []
    transformer_rows: list[dict] = []
    for bus, levels in mixed.items():
        base_row = bus_lookup.loc[bus]
        geometry = base_row["geometry"]
        for voltage in levels[1:]:
            row = {column: base_row.get(column) for column in bus_lookup.columns}
            row["bus_id"] = split_id(bus, voltage, levels)
            row["kind"] = "transformer"
            row["v_nom_kv"] = None
            row["geometry"] = geometry
            extra_bus_rows.append(row)
        for low, high in pairwise(levels):
            transformer_rows.append(
                {
                    "transformer_id": f"transformer::{bus}::{round(low)}-{round(high)}kv",
                    "bus0": split_id(bus, high, levels),
                    "bus1": split_id(bus, low, levels),
                    "v_nom0_kv": float(high),
                    "v_nom1_kv": float(low),
                    "s_nom_mva": float(capacity_mva),
                    "geometry": geometry,
                }
            )

    def remap(bus: object, voltage: object) -> str:
        bus = str(bus)
        return split_id(bus, float(voltage), mixed[bus]) if bus in mixed else bus

    lines = lines.copy()
    lines["bus0"] = [remap(bus, voltage) for bus, voltage in zip(lines["bus0"], lines["v_nom_kv"])]
    lines["bus1"] = [remap(bus, voltage) for bus, voltage in zip(lines["bus1"], lines["v_nom_kv"])]

    extra_buses = gpd.GeoDataFrame(extra_bus_rows, geometry="geometry", crs=buses.crs)
    buses = gpd.GeoDataFrame(
        pd.concat([buses, extra_buses], ignore_index=True),
        geometry="geometry",
        crs=buses.crs,
    )
    transformers = gpd.GeoDataFrame(transformer_rows, columns=transformer_columns, geometry="geometry", crs="EPSG:4326")
    return buses, lines, transformers


def _build_inferred_network(
    *,
    source: str,
    input_dir: Path,
    output_dir: Path,
    network_path: Path,
    metadata_path: Path,
    region: str,
    network_type: str,
    roads_path: Path,
    power_path: Path | None,
    nightlight_targets: gpd.GeoDataFrame | Path,
    nightlight_support_distance_m: float,
    max_anchor_distance_m: float,
    inferred_voltage_kv: float,
    inferred_transmission_voltage_kv: float,
    inferred_capacity_mva: float,
    inferred_transmission_capacity_mva: float,
    inferred_anchor_capacity_mva: float,
    base_route_gap_tolerance_m: float,
    table_output_dir: Path | None,
    reference_line_length_km: float,
    line_length_tolerance_fraction: float,
    reference_generation_capacity_mw: float,
    generation_capacity_tolerance_fraction: float,
) -> NetworkBuildOutputs:
    if source not in {"inferred-osm", "inferred-provided"}:
        raise ValueError(f"Unsupported inferred source: {source}")

    # 1. Inputs: the cached OSM road envelope, the power assets and the night-light targets.
    osm_road_envelope = osm.read_vector(roads_path)
    if osm_road_envelope.empty:
        raise ValueError(f"The OSM road envelope at {roads_path} is empty")

    power_feature_count = 0
    provided_generator_records = 0
    provided_generators = _empty_generators()
    if source == "inferred-osm":
        if power_path is None:
            raise ValueError("inferred-osm needs power_path (the cached OSM power features)")
        power_features = osm.read_vector(power_path)
        power_feature_count = len(power_features)
        power_assets = _normalise_osm_power_assets(power_features, region=region)
        methodology = INFERRED_OSM_METHODOLOGY
        power_asset_source = "osm_power"
    else:
        power_assets, provided_generators = _provided_power_assets(input_dir)
        provided_generator_records = len(provided_generators)
        methodology = INFERRED_PROVIDED_METHODOLOGY
        power_asset_source = "provided_substations_and_generators"

    # A region member without any power asset gets one stand-in root on its road network.
    member_roots: list[gpd.GeoDataFrame] = []
    for member in osm.region_members(region):
        member_slug = osm.region_slug(member)
        has_root = not power_assets.empty and power_assets["region"].astype(str).eq(member_slug).any()
        if has_root:
            continue
        member_roads = osm_road_envelope
        if "region" in member_roads and len(osm.region_members(region)) > 1:
            member_roads = member_roads[member_roads["region"].astype(str).eq(member_slug)]
        logger.warning("%s: no power assets for %s; placing a provisional root on its road network", source, member)
        member_roots.append(_provisional_power_root(member, member_roads))
    if member_roots:
        power_assets = gpd.GeoDataFrame(
            pd.concat([power_assets, *member_roots], ignore_index=True),
            geometry="geometry",
            crs="EPSG:4326",
        )

    if isinstance(nightlight_targets, gpd.GeoDataFrame):
        targets_frame = nightlight_targets
        nightlight_targets_path = None
    else:
        nightlight_targets_path = Path(nightlight_targets)
        targets_frame = osm.read_vector(nightlight_targets_path)
    nightlight_target_points = gpd.GeoDataFrame(
        geometry=targets_frame.to_crs("EPSG:4326").geometry.representative_point(),
        crs="EPSG:4326",
    )
    targets_metadata = None
    if nightlight_targets_path is not None and (nightlight_targets_path.parent / "metadata.json").is_file():
        targets_metadata = json.loads((nightlight_targets_path.parent / "metadata.json").read_text())

    # 2. Keep the roads that lie near a lit target or a power asset.
    support_points = gpd.GeoDataFrame(
        pd.concat(
            [
                nightlight_target_points[["geometry"]].to_crs("EPSG:4326"),
                power_assets[["geometry"]].to_crs("EPSG:4326"),
            ],
            ignore_index=True,
        ),
        geometry="geometry",
        crs="EPSG:4326",
    )
    supported_roads, supported_road_metadata = _nightlight_supported_roads(
        osm_road_envelope,
        support_points,
        support_distance_m=nightlight_support_distance_m,
    )
    supported_road_metadata["nightlight_target_count"] = len(nightlight_target_points)
    supported_road_metadata["power_asset_support_count"] = len(power_assets)
    supported_road_metadata["highway_classes"] = _highway_class_breakdown(supported_roads)

    # 3. For the provided variant, keep the CEB transmission backbone (same rules as the base product).
    provided_backbone: gpd.GeoDataFrame | None = None
    provided_backbone_edges = 0
    if source == "inferred-provided":
        provided_substations, provided_routes, _ = _load_provided_inputs(input_dir)
        provided_topology = derive_base_topology(
            provided_substations,
            provided_routes,
            route_gap_tolerance_m=base_route_gap_tolerance_m,
        )
        provided_backbone = provided_topology.lines.copy()
        provided_backbone["region"] = "mauritius"
        provided_backbone_edges = len(provided_backbone)

    # 4. Graph: roads and backbone as edges, assets connected to the nearest road point.
    graph = build_inferred_distribution_graph(
        power_assets,
        osm_distribution_lines=supported_roads,
        provided_backbone_lines=provided_backbone,
        max_anchor_distance_m=max_anchor_distance_m,
        anchor_to_each_line_source=provided_backbone is not None,
    )
    table_dir = _inferred_table_dir(output_dir)
    inferred_tables = write_inferred_distribution_tables(graph, table_dir)

    # 5. PyPSA tables. Voltages and capacities are placeholders (see electrical_values_note).
    lines = _graph_line_frame(
        graph,
        default_voltage_kv=inferred_voltage_kv,
        transmission_voltage_kv=inferred_transmission_voltage_kv,
        default_capacity_mva=inferred_capacity_mva,
        transmission_capacity_mva=inferred_transmission_capacity_mva,
        anchor_capacity_mva=inferred_anchor_capacity_mva,
    )
    buses = _node_bus_frame(graph)
    # A junction that carries both the backbone and distribution voltage becomes
    # one bus per level joined by a transformer.
    buses, lines, transformers = _insert_transformer_junctions(buses, lines, capacity_mva=inferred_capacity_mva)
    # A bus with no line (an asset that could not be anchored) sits at the distribution voltage.
    connected_buses = set(lines["bus0"].astype(str)) | set(lines["bus1"].astype(str))
    isolated = ~buses["bus_id"].astype(str).isin(connected_buses) & buses["v_nom_kv"].isna()
    buses.loc[isolated, "v_nom_kv"] = float(inferred_voltage_kv)
    service_weights = _equal_service_weights(buses)
    service_weights_path_out = table_dir / "service_weights.csv"
    service_weights.to_csv(service_weights_path_out, index=False)

    generators = (
        _provided_generators_for_inferred(provided_generators) if source == "inferred-provided" else _empty_generators()
    )
    table_outputs = None
    validation_kwargs = {
        "source": source,
        "reference_line_length_km": reference_line_length_km,
        "reference_line_length_scope": INFERRED_LINE_LENGTH_SCOPE,
        "reference_line_length_source": CEB_TOTAL_NETWORK_LENGTH_SOURCE,
        "reference_line_length_note": INFERRED_LINE_LENGTH_NOTE,
        "reference_line_length_sources": ("osm", "provided_transmission"),
        "line_length_tolerance_fraction": line_length_tolerance_fraction,
        "reference_generation_capacity_mw": (
            reference_generation_capacity_mw if source == "inferred-provided" else None
        ),
        "generation_capacity_tolerance_fraction": generation_capacity_tolerance_fraction,
        "allow_incomplete_generators": source == "inferred-provided",
    }
    if table_output_dir is None:
        validation = validate_model_tables(buses, lines, generators, **validation_kwargs)
    else:
        table_outputs, validation = write_model_tables(buses, lines, generators, table_output_dir, **validation_kwargs)
    envelope_validation = _osm_road_envelope_validation(
        osm_road_envelope,
        reference_line_length_km=reference_line_length_km,
        tolerance_fraction=line_length_tolerance_fraction,
    )
    if validation["errors"]:
        raise ValueError("Invalid inferred network tables:\n- " + "\n- ".join(validation["errors"]))
    _log_validation(network_path.stem, validation)
    anchored, unanchored = _power_asset_anchor_counts(graph)
    if unanchored:
        logger.warning(
            "%s: %d power asset(s) are farther than %.0f m from any retained road and stay unconnected",
            network_path.stem,
            unanchored,
            max_anchor_distance_m,
        )

    # 6. Write the network, its metadata and the GeoParquet layers.
    network = build_topology_network(
        buses,
        lines,
        _complete_generators(generators),
        transformers=transformers,
        default_voltage_kv=inferred_voltage_kv,
    )
    _write_network(network_path, network)
    spatial_dir = network_path.parent / "geoparquet"
    spatial_outputs = spatial_export_paths(spatial_dir, network_id=network_path.stem)
    electrical_values_note = (
        "Distribution voltages/capacities are non-binding topology placeholders; "
        "the provided transmission backbone keeps its voltage and joins distribution "
        "through transformers (see model_v_nom_kv / model_s_nom_mva)."
    )
    _write_metadata(
        metadata_path,
        {
            "source": source,
            "methodology": methodology,
            "distance_method": "WGS84 geodesic",
            "nightlight_policy": "viirs_targets_filter_dense_osm_roads_and_preserve_cycles",
            "input_dir": str(input_dir),
            "network": str(network_path),
            "spatial_nodes": str(spatial_outputs.nodes),
            "spatial_edges": str(spatial_outputs.edges),
            "spatial_manifest": str(spatial_outputs.manifest),
            "region": region,
            "regions": list(osm.region_members(region)),
            "road_envelope_network_type": network_type,
            "road_envelope_highway_classes": _highway_class_breakdown(osm_road_envelope),
            "has_demand": False,
            "snapshots": 0,
            "buses": len(network.buses),
            "lines": len(network.lines),
            "generators": len(network.generators),
            "loads": 0,
            "inferred": True,
            "stage": "connectivity_only",
            "service_weights": str(service_weights_path_out),
            "road_envelope_edges": len(osm_road_envelope),
            "osm_road_envelope_cache": str(roads_path),
            "nightlight_targets": str(nightlight_targets_path) if nightlight_targets_path is not None else None,
            "nightlight_targets_sha256": (
                _file_sha256(nightlight_targets_path) if nightlight_targets_path is not None else None
            ),
            "nightlight_targets_metadata": targets_metadata,
            "nightlight_support_distance_m": nightlight_support_distance_m,
            "nightlight_supported_roads": supported_road_metadata,
            "osm_power_cache": str(power_path) if power_path is not None else None,
            "osm_power_features": power_feature_count,
            "provided_generator_records": provided_generator_records,
            "power_asset_source": power_asset_source,
            "power_assets": len(power_assets),
            "substation_roots": int(power_assets["asset_kind"].eq("substation").sum()),
            "generator_roots": int(power_assets["asset_kind"].eq("generator").sum()),
            "provisional_roots": len(member_roots),
            "provided_backbone_edges": provided_backbone_edges,
            "provided_backbone_route_gap_tolerance_m": (
                base_route_gap_tolerance_m if source == "inferred-provided" else None
            ),
            "anchored_power_assets": anchored,
            "unanchored_power_assets": unanchored,
            "inferred_voltage_kv": inferred_voltage_kv,
            "inferred_transmission_voltage_kv": inferred_transmission_voltage_kv,
            "inferred_capacity_mva": inferred_capacity_mva,
            "inferred_transmission_capacity_mva": inferred_transmission_capacity_mva,
            "inferred_anchor_capacity_mva": inferred_anchor_capacity_mva,
            "transformers": len(network.transformers),
            "electrical_values_note": electrical_values_note,
            "max_anchor_distance_m": max_anchor_distance_m,
            "connected_components": nx.number_connected_components(graph),
            "model_line_length_km": validation["totals"]["line_length_km"],
            "line_length_validation": validation["checks"]["line_length_against_published_ceb_total"],
            "road_envelope_line_length_validation": envelope_validation,
            "human_tables": str(table_output_dir) if table_output_dir else None,
            "validation_status": validation["status"],
            "validation_warnings": validation["warnings"],
        },
    )
    spatial_outputs = write_network_geoparquet(
        buses,
        lines,
        spatial_dir,
        network_id=network_path.stem,
        network_source=source,
        methodology=methodology,
        source_network_path=network_path,
        source_metadata_path=metadata_path,
        publish_voltage=False,
        publish_capacity=False,
        electrical_values_note=electrical_values_note,
        stage="connectivity_only",
    )
    return NetworkBuildOutputs(
        network=network_path,
        metadata=metadata_path,
        spatial_nodes=spatial_outputs.nodes,
        spatial_edges=spatial_outputs.edges,
        spatial_manifest=spatial_outputs.manifest,
        inferred_nodes=inferred_tables.nodes,
        inferred_edges=inferred_tables.edges,
        inferred_metadata=inferred_tables.metadata,
        generators=table_outputs.generators if table_outputs else None,
        lines=table_outputs.lines if table_outputs else None,
        validation=table_outputs.validation if table_outputs else None,
    )


def build_network(
    source: str,
    *,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    region: str | None = None,
    output_name: str | None = None,
    overwrite: bool = False,
    network_type: str = "drive",
    roads_path: Path | None = None,
    power_path: Path | None = None,
    nightlight_targets: gpd.GeoDataFrame | str | Path | None = None,
    nightlight_support_distance_m: float = DEFAULT_NIGHTLIGHT_SUPPORT_DISTANCE_M,
    max_anchor_distance_m: float = DEFAULT_MAX_ANCHOR_DISTANCE_M,
    inferred_voltage_kv: float = 11,
    inferred_transmission_voltage_kv: float = 66,
    inferred_capacity_mva: float = 5,
    inferred_transmission_capacity_mva: float = 50,
    inferred_anchor_capacity_mva: float = 137,
    export_root: Path | None = None,
    reference_line_length_km: float = CEB_TRANSMISSION_LENGTH_KM,
    inferred_reference_line_length_km: float = CEB_TOTAL_NETWORK_LENGTH_KM,
    line_length_tolerance_fraction: float = 0.35,
    reference_generation_capacity_mw: float = CEB_REPORTED_INSTALLED_GENERATION_MW,
    generation_capacity_tolerance_fraction: float = 0.10,
    base_route_gap_tolerance_m: float = 75,
    base_default_voltage_kv: float = 66,
    base_topology_capacity_mva: float = 10_000,
) -> NetworkBuildOutputs:
    """Build and save one named network product.

    ``source`` picks the product:

    - ``"base"``: the provided CEB transmission network (substations, routes, generators).
    - ``"inferred-osm"``: OSM substations, plants and generators joined by the
      night-light-supported OSM road network.
    - ``"inferred-provided"``: the provided substations and generators plus the
      CEB backbone, joined by the same road network.

    Nothing is downloaded here. The inferred sources read three files:
    ``roads_path`` and ``power_path`` (see :mod:`energy.osm`, downloaded by the
    ``fetch_energy_osm`` rule) and ``nightlight_targets`` (written by the
    ``build_energy_nightlight_targets`` rule). When a path is not given, the
    default location under the configured data root is used and a missing file
    is reported with the rule that creates it.

    Outputs go to ``<output_dir>/<output_name>/``: the PyPSA network, a metadata
    JSON and checksum-linked node and edge GeoParquet layers. With
    ``export_root`` the human-readable ``generators.csv``, ``lines.csv`` and
    ``validation.json`` are written below ``<export_root>/<output_name>/``.
    Existing outputs are only replaced when ``overwrite`` is set.
    """
    source = source.lower()
    valid_sources = {"base", "inferred-osm", "inferred-provided"}
    if source not in valid_sources:
        raise ValueError("source must be 'base', 'inferred-osm', or 'inferred-provided'")
    if region is not None and source == "base":
        raise ValueError("region can only be used with an inferred source")
    if source != "base" and not region:
        raise ValueError(f"source={source!r} requires a region, e.g. region='mauritius-rodrigues'.")

    input_dir = Path(input_dir or processed_energy_dir() / "provided")
    output_dir = Path(output_dir or network_output_dir())
    if output_name:
        output_stem = output_name
    elif source != "base":
        output_stem = f"{source}-{osm.region_slug(region)}"
    else:
        output_stem = "base-mauritius"
    result_dir = output_dir / output_stem
    network_path = result_dir / f"{output_stem}.nc"
    metadata_path = result_dir / f"{output_stem}_metadata.json"
    table_output_dir = Path(export_root) / output_stem if export_root else None

    if network_path.exists() and not overwrite:
        raise FileExistsError(
            f"{network_path} already exists; set overwrite=True (notebook: OVERWRITE = True) "
            "or pass a different output_name to rebuild it."
        )

    if source == "base":
        return _build_base_network(
            input_dir=input_dir,
            network_path=network_path,
            metadata_path=metadata_path,
            table_output_dir=table_output_dir,
            reference_line_length_km=reference_line_length_km,
            line_length_tolerance_fraction=line_length_tolerance_fraction,
            reference_generation_capacity_mw=reference_generation_capacity_mw,
            generation_capacity_tolerance_fraction=generation_capacity_tolerance_fraction,
            route_gap_tolerance_m=base_route_gap_tolerance_m,
            default_voltage_kv=base_default_voltage_kv,
            topology_capacity_mva=base_topology_capacity_mva,
        )

    roads_path = Path(roads_path) if roads_path is not None else osm.osm_roads_path(region, network_type)
    if not roads_path.is_file():
        raise FileNotFoundError(
            f"OSM roads are missing: {roads_path}. The fetch_energy_osm rule downloads them "
            "(needs internet), or pass roads_path."
        )
    if source == "inferred-osm":
        power_path = Path(power_path) if power_path is not None else osm.osm_power_path(region)
        if not power_path.is_file():
            raise FileNotFoundError(
                f"OSM power features are missing: {power_path}. The fetch_energy_osm rule downloads them "
                "(needs internet), or pass power_path."
            )
    else:
        power_path = None
    if nightlight_targets is None:
        nightlight_targets = processed_energy_dir() / "nightlight" / osm.region_slug(region) / "targets.geoparquet"
    if not isinstance(nightlight_targets, gpd.GeoDataFrame) and not Path(nightlight_targets).is_file():
        raise FileNotFoundError(
            f"Night-light targets are missing: {nightlight_targets}. Build them with "
            f"'snakemake -c1 {nightlight_targets}' or pass nightlight_targets."
        )

    return _build_inferred_network(
        source=source,
        input_dir=input_dir,
        output_dir=result_dir,
        network_path=network_path,
        metadata_path=metadata_path,
        region=region,
        network_type=network_type,
        roads_path=roads_path,
        power_path=power_path,
        nightlight_targets=nightlight_targets,
        nightlight_support_distance_m=nightlight_support_distance_m,
        max_anchor_distance_m=max_anchor_distance_m,
        inferred_voltage_kv=inferred_voltage_kv,
        inferred_transmission_voltage_kv=inferred_transmission_voltage_kv,
        inferred_capacity_mva=inferred_capacity_mva,
        inferred_transmission_capacity_mva=inferred_transmission_capacity_mva,
        inferred_anchor_capacity_mva=inferred_anchor_capacity_mva,
        base_route_gap_tolerance_m=base_route_gap_tolerance_m,
        table_output_dir=table_output_dir,
        reference_line_length_km=inferred_reference_line_length_km,
        line_length_tolerance_fraction=line_length_tolerance_fraction,
        reference_generation_capacity_mw=reference_generation_capacity_mw,
        generation_capacity_tolerance_fraction=generation_capacity_tolerance_fraction,
    )
