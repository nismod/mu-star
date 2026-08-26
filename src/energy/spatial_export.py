"""Deterministic GeoParquet views of validated PyPSA network topology."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pandas as pd
from shapely import force_2d

GEOGRAPHIC_CRS = "EPSG:4326"
SPATIAL_SCHEMA_VERSION = "energy-network-spatial-v1"

NODE_COLUMNS = (
    "asset_id",
    "bus_id",
    "network_id",
    "component_type",
    "asset_type",
    "name",
    "kind",
    "region",
    "source",
    "carrier",
    "v_nom_kv",
    "model_v_nom_kv",
    "electrical_values_basis",
    "is_inferred",
    "operational_ready",
    "provisional_root",
    "anchor_status",
    "anchor_distance_m",
    "geometry",
)

EDGE_COLUMNS = (
    "asset_id",
    "line_id",
    "network_id",
    "component_type",
    "asset_type",
    "region",
    "source",
    "carrier",
    "bus0",
    "bus1",
    "v_nom_kv",
    "s_nom_mva",
    "model_v_nom_kv",
    "model_s_nom_mva",
    "electrical_values_basis",
    "length_km",
    "is_inferred",
    "operational_ready",
    "stage",
    "derived",
    "rating_basis",
    "source_route_id",
    "source_route_part_id",
    "circuit_id",
    "geometry",
)


@dataclass(frozen=True)
class SpatialExportOutputs:
    """Paths comprising one versioned spatial view of a network."""

    nodes: Path
    edges: Path
    manifest: Path


def spatial_export_paths(
    output_dir: Path,
    *,
    network_id: str,
) -> SpatialExportOutputs:
    """Return the conventional sidecar paths for one network identifier."""
    output_dir = Path(output_dir)
    return SpatialExportOutputs(
        nodes=output_dir / f"{network_id}-nodes.geoparquet",
        edges=output_dir / f"{network_id}-edges.geoparquet",
        manifest=output_dir / f"{network_id}-spatial-manifest.json",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _column(
    frame: pd.DataFrame,
    name: str,
    *,
    default: object = pd.NA,
    dtype: str,
) -> pd.Series:
    if name in frame:
        values = frame[name].copy()
    else:
        values = pd.Series(default, index=frame.index)
    return values.astype(dtype)


def _required_ids(frame: pd.DataFrame, column: str, label: str) -> pd.Series:
    if column not in frame:
        raise ValueError(f"{label} must contain {column}")
    identifiers = frame[column].astype("string")
    if identifiers.isna().any() or identifiers.str.strip().eq("").any():
        raise ValueError(f"{label} {column} values must be non-blank")
    if identifiers.duplicated().any():
        duplicates = identifiers[identifiers.duplicated(keep=False)].unique().tolist()
        raise ValueError(f"{label} contains duplicate {column} values: {duplicates}")
    return identifiers


def _prepare_geometry(
    frame: gpd.GeoDataFrame,
    *,
    label: str,
    geometry_type: str,
) -> gpd.GeoSeries:
    if not isinstance(frame, gpd.GeoDataFrame):
        raise TypeError(f"{label} must be a GeoDataFrame")
    if frame.crs is None:
        raise ValueError(f"{label} must define a coordinate reference system")
    geometry = frame.to_crs(GEOGRAPHIC_CRS).geometry
    if geometry.isna().any() or geometry.is_empty.any():
        raise ValueError(f"{label} geometries must be non-null and non-empty")
    if not geometry.geom_type.eq(geometry_type).all():
        found = sorted(geometry.geom_type.unique().tolist())
        raise ValueError(f"{label} geometries must all be {geometry_type}; found {found}")
    invalid = ~geometry.is_valid
    if invalid.any():
        # A root exactly coincident with its OSM node is represented in the
        # connectivity model by a zero-length logical anchor. Retain that
        # topology edge; reject every other invalid map geometry.
        logical_anchor = pd.Series(False, index=frame.index)
        if label == "lines" and "source" in frame:
            logical_anchor = frame["source"].astype("string").str.endswith("_anchor", na=False)
            logical_anchor &= geometry.map(lambda value: len(set(value.coords)) == 1)
        if (invalid & ~logical_anchor).any():
            raise ValueError(f"{label} geometries must be valid")
    return geometry.map(force_2d)


def _prepare_nodes(
    buses: gpd.GeoDataFrame,
    *,
    network_id: str,
    network_source: str,
    default_region: str | None,
    operational_ready: bool,
    publish_voltage: bool,
    electrical_values_basis: str,
) -> gpd.GeoDataFrame:
    bus_ids = _required_ids(buses, "bus_id", "buses")
    geometry = _prepare_geometry(buses, label="buses", geometry_type="Point")
    model_voltage = pd.to_numeric(
        buses["v_nom_kv"] if "v_nom_kv" in buses else pd.Series(pd.NA, index=buses.index),
        errors="coerce",
    ).astype("Float64")
    region = _column(buses, "region", dtype="string")
    if default_region is not None:
        region = region.fillna(default_region)
    kind = _column(buses, "kind", default="bus", dtype="string").fillna("bus")
    inferred = _column(
        buses,
        "inferred",
        default=network_source != "base",
        dtype="boolean",
    ).fillna(network_source != "base")

    nodes = gpd.GeoDataFrame(
        {
            "asset_id": bus_ids,
            "bus_id": bus_ids,
            "network_id": pd.Series(network_id, index=buses.index, dtype="string"),
            "component_type": pd.Series("Bus", index=buses.index, dtype="string"),
            "asset_type": kind,
            "name": _column(buses, "name", dtype="string"),
            "kind": kind,
            "region": region,
            "source": _column(
                buses,
                "source",
                default=network_source,
                dtype="string",
            ).fillna(network_source),
            "carrier": _column(buses, "carrier", default="AC", dtype="string").fillna("AC"),
            "v_nom_kv": (model_voltage if publish_voltage else pd.Series(pd.NA, index=buses.index, dtype="Float64")),
            "model_v_nom_kv": model_voltage,
            "electrical_values_basis": pd.Series(
                electrical_values_basis,
                index=buses.index,
                dtype="string",
            ),
            "is_inferred": inferred,
            "operational_ready": pd.Series(
                operational_ready,
                index=buses.index,
                dtype="boolean",
            ),
            "provisional_root": _column(
                buses,
                "provisional_root",
                default=False,
                dtype="boolean",
            ).fillna(False),
            "anchor_status": _column(buses, "anchor_status", dtype="string"),
            "anchor_distance_m": pd.to_numeric(
                buses["anchor_distance_m"] if "anchor_distance_m" in buses else pd.Series(pd.NA, index=buses.index),
                errors="coerce",
            ).astype("Float64"),
        },
        geometry=geometry,
        crs=GEOGRAPHIC_CRS,
    )
    return nodes.loc[:, NODE_COLUMNS].sort_values("asset_id", kind="mergesort").reset_index(drop=True)


def _prepare_edges(
    lines: gpd.GeoDataFrame,
    *,
    valid_bus_ids: set[str],
    network_id: str,
    network_source: str,
    default_region: str | None,
    operational_ready: bool,
    publish_voltage: bool,
    publish_capacity: bool,
    electrical_values_basis: str,
) -> gpd.GeoDataFrame:
    line_ids = _required_ids(lines, "line_id", "lines")
    geometry = _prepare_geometry(lines, label="lines", geometry_type="LineString")
    for endpoint in ("bus0", "bus1"):
        if endpoint not in lines:
            raise ValueError(f"lines must contain {endpoint}")
        values = lines[endpoint].astype("string")
        if values.isna().any() or values.str.strip().eq("").any():
            raise ValueError(f"lines {endpoint} values must be non-blank")
        unknown = sorted(set(values.astype(str)) - valid_bus_ids)
        if unknown:
            raise ValueError(f"lines {endpoint} contains unknown bus IDs: {unknown}")

    lengths = pd.to_numeric(lines["length_km"], errors="coerce").astype("Float64")
    if lengths.isna().any() or lengths.le(0).any():
        raise ValueError("lines length_km values must be positive numbers")
    model_voltage = pd.to_numeric(
        lines["v_nom_kv"] if "v_nom_kv" in lines else pd.Series(pd.NA, index=lines.index),
        errors="coerce",
    ).astype("Float64")
    model_capacity = pd.to_numeric(
        lines["s_nom_mva"] if "s_nom_mva" in lines else pd.Series(pd.NA, index=lines.index),
        errors="coerce",
    ).astype("Float64")
    region = _column(lines, "region", dtype="string")
    if default_region is not None:
        region = region.fillna(default_region)
    inferred = _column(
        lines,
        "inferred",
        default=network_source != "base",
        dtype="boolean",
    ).fillna(network_source != "base")
    asset_type = pd.Series(
        ["inferred_candidate" if value else "transmission" for value in inferred],
        index=lines.index,
        dtype="string",
    )

    edges = gpd.GeoDataFrame(
        {
            "asset_id": line_ids,
            "line_id": line_ids,
            "network_id": pd.Series(network_id, index=lines.index, dtype="string"),
            "component_type": pd.Series("Line", index=lines.index, dtype="string"),
            "asset_type": asset_type,
            "region": region,
            "source": _column(
                lines,
                "source",
                default=network_source,
                dtype="string",
            ).fillna(network_source),
            "carrier": _column(lines, "carrier", default="AC", dtype="string").fillna("AC"),
            "bus0": lines["bus0"].astype("string"),
            "bus1": lines["bus1"].astype("string"),
            "v_nom_kv": (model_voltage if publish_voltage else pd.Series(pd.NA, index=lines.index, dtype="Float64")),
            "s_nom_mva": (model_capacity if publish_capacity else pd.Series(pd.NA, index=lines.index, dtype="Float64")),
            "model_v_nom_kv": model_voltage,
            "model_s_nom_mva": model_capacity,
            "electrical_values_basis": pd.Series(
                electrical_values_basis,
                index=lines.index,
                dtype="string",
            ),
            "length_km": lengths,
            "is_inferred": inferred,
            "operational_ready": pd.Series(
                operational_ready,
                index=lines.index,
                dtype="boolean",
            ),
            "stage": _column(lines, "stage", dtype="string"),
            "derived": _column(lines, "derived", default=False, dtype="boolean").fillna(False),
            "rating_basis": _column(lines, "rating_basis", dtype="string"),
            "source_route_id": _column(lines, "source_route_id", dtype="string"),
            "source_route_part_id": _column(lines, "source_route_part_id", dtype="string"),
            "circuit_id": _column(lines, "circuit_id", dtype="string"),
        },
        geometry=geometry,
        crs=GEOGRAPHIC_CRS,
    )
    return edges.loc[:, EDGE_COLUMNS].sort_values("asset_id", kind="mergesort").reset_index(drop=True)


def _layer_manifest(
    path: Path,
    frame: gpd.GeoDataFrame,
    *,
    geometry_type: str,
) -> dict[str, object]:
    bbox = None if frame.empty else [float(value) for value in frame.total_bounds]
    geometry_types = sorted(set(frame.geometry.geom_type))
    return {
        "path": path.name,
        "sha256": _file_sha256(path),
        "row_count": len(frame),
        "feature_count": len(frame),
        "id_column": "asset_id",
        "crs": GEOGRAPHIC_CRS,
        "geometry_type": geometry_type,
        "geometry_types": geometry_types,
        "bbox": bbox,
    }


def _round_trip_check(
    path: Path,
    expected: gpd.GeoDataFrame,
    *,
    geometry_type: str,
) -> None:
    actual = gpd.read_parquet(path)
    if actual.crs is None or actual.crs.to_epsg() != 4326:
        raise ValueError(f"GeoParquet round trip did not preserve EPSG:4326: {path}")
    if len(actual) != len(expected):
        raise ValueError(f"GeoParquet round trip changed feature count: {path}")
    if actual["asset_id"].astype(str).tolist() != expected["asset_id"].astype(str).tolist():
        raise ValueError(f"GeoParquet round trip changed asset IDs: {path}")
    if not actual.geometry.geom_type.eq(geometry_type).all():
        raise ValueError(f"GeoParquet round trip changed geometry type: {path}")
    if actual.geometry.has_z.any():
        raise ValueError(f"GeoParquet output must contain only 2D geometry: {path}")


def write_network_geoparquet(
    buses: gpd.GeoDataFrame,
    lines: gpd.GeoDataFrame,
    output_dir: Path,
    *,
    network_id: str,
    network_source: str,
    methodology: str,
    source_network_path: Path,
    default_region: str | None = None,
    operational_ready: bool,
    publish_voltage: bool,
    publish_capacity: bool,
    electrical_values_basis: str,
    stage: str | None = None,
    source_metadata_path: Path | None = None,
) -> SpatialExportOutputs:
    """Publish validated topology as a checksum-linked GeoParquet sidecar bundle."""
    if not network_id.strip():
        raise ValueError("network_id must be non-blank")
    source_network_path = Path(source_network_path)
    if not source_network_path.is_file():
        raise FileNotFoundError(f"Canonical NetCDF does not exist: {source_network_path}")
    if source_metadata_path is not None:
        source_metadata_path = Path(source_metadata_path)
        if not source_metadata_path.is_file():
            raise FileNotFoundError(f"Network metadata does not exist: {source_metadata_path}")

    prepared_nodes = _prepare_nodes(
        buses,
        network_id=network_id,
        network_source=network_source,
        default_region=default_region,
        operational_ready=operational_ready,
        publish_voltage=publish_voltage,
        electrical_values_basis=electrical_values_basis,
    )
    prepared_edges = _prepare_edges(
        lines,
        valid_bus_ids=set(prepared_nodes["bus_id"].astype(str)),
        network_id=network_id,
        network_source=network_source,
        default_region=default_region,
        operational_ready=operational_ready,
        publish_voltage=publish_voltage,
        publish_capacity=publish_capacity,
        electrical_values_basis=electrical_values_basis,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = spatial_export_paths(
        output_dir,
        network_id=network_id,
    )
    nonce = uuid4().hex
    temporary_nodes = output_dir / f".{outputs.nodes.name}.{nonce}.tmp.geoparquet"
    temporary_edges = output_dir / f".{outputs.edges.name}.{nonce}.tmp.geoparquet"
    temporary_manifest = output_dir / f".{outputs.manifest.name}.{nonce}.tmp"
    temporary_paths = (temporary_nodes, temporary_edges, temporary_manifest)

    try:
        prepared_nodes.to_parquet(
            temporary_nodes,
            index=False,
            compression="zstd",
        )
        prepared_edges.to_parquet(
            temporary_edges,
            index=False,
            compression="zstd",
        )
        _round_trip_check(
            temporary_nodes,
            prepared_nodes,
            geometry_type="Point",
        )
        _round_trip_check(
            temporary_edges,
            prepared_edges,
            geometry_type="LineString",
        )
        os.replace(temporary_nodes, outputs.nodes)
        os.replace(temporary_edges, outputs.edges)

        manifest = {
            "schema_version": SPATIAL_SCHEMA_VERSION,
            "artifact_role": "visualisation_derivative",
            "network_id": network_id,
            "network_source": network_source,
            "methodology": methodology,
            "stage": stage,
            "inferred": network_source != "base",
            "source_network": {
                "path": source_network_path.name,
                "sha256": _file_sha256(source_network_path),
            },
            "source_metadata": (
                {
                    "path": source_metadata_path.name,
                    "sha256": _file_sha256(source_metadata_path),
                }
                if source_metadata_path is not None
                else None
            ),
            "layers": {
                "nodes": _layer_manifest(
                    outputs.nodes,
                    prepared_nodes,
                    geometry_type="Point",
                ),
                "edges": _layer_manifest(
                    outputs.edges,
                    prepared_edges,
                    geometry_type="LineString",
                ),
            },
            "totals": {
                "nodes": len(prepared_nodes),
                "edges": len(prepared_edges),
                "line_length_km": float(prepared_edges["length_km"].sum()),
            },
            "line_length_km": float(prepared_edges["length_km"].sum()),
            "operational_ready": operational_ready,
            "electrical_values_basis": electrical_values_basis,
        }
        temporary_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_manifest, outputs.manifest)
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)

    return outputs
