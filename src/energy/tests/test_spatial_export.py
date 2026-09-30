import hashlib
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.spatial_export import (
    EDGE_COLUMNS,
    NODE_COLUMNS,
    SPATIAL_SCHEMA_VERSION,
    write_network_geoparquet,
)


def _tables():
    buses = gpd.GeoDataFrame(
        {
            "bus_id": ["B", "A"],
            "name": ["Bus B", "Bus A"],
            "kind": ["junction", "substation"],
            "v_nom_kv": [66.0, 66.0],
            "source": ["derived_route_junction", "provided_substation"],
            "geometry": [Point(57.6, -20.2, 0), Point(57.5, -20.2, 0)],
        },
        crs="EPSG:4326",
    )
    lines = gpd.GeoDataFrame(
        {
            "line_id": ["LINE_B"],
            "bus0": ["A"],
            "bus1": ["B"],
            "v_nom_kv": [66.0],
            "length_km": [12.5],
            "s_nom_mva": [10_000.0],
            "source": ["provided_transmission_geometry"],
            "stage": ["topology_only"],
            "source_route_id": ["ROUTE_001"],
            "source_route_part_id": ["ROUTE_001_PART_001"],
            "circuit_id": ["ROUTE_001_PART_001"],
            "geometry": [
                LineString(
                    [
                        (57.5, -20.2, 0),
                        (57.55, -20.15, 0),
                        (57.6, -20.2, 0),
                    ]
                )
            ],
        },
        crs="EPSG:4326",
    )
    return buses, lines


def _network_file(tmp_path):
    path = tmp_path / "base-mauritius.nc"
    path.write_bytes(b"canonical-network")
    return path


def test_write_network_geoparquet_round_trips_stable_spatial_contract(tmp_path):
    buses, lines = _tables()
    source_network = _network_file(tmp_path)
    source_metadata = tmp_path / "base-mauritius_metadata.json"
    source_metadata.write_text('{"source": "base"}')

    outputs = write_network_geoparquet(
        buses,
        lines,
        tmp_path,
        network_id="base-mauritius",
        network_source="base",
        methodology="ceb-routed-topology-v3",
        source_network_path=source_network,
        default_region="mauritius",
        publish_voltage=True,
        publish_capacity=False,
        stage="topology_only",
        source_metadata_path=source_metadata,
    )

    nodes = gpd.read_parquet(outputs.nodes)
    edges = gpd.read_parquet(outputs.edges)
    manifest = json.loads(outputs.manifest.read_text())

    assert tuple(nodes.columns) == NODE_COLUMNS
    assert tuple(edges.columns) == EDGE_COLUMNS
    assert list(nodes["asset_id"]) == ["A", "B"]
    assert list(edges["asset_id"]) == ["LINE_B"]
    assert nodes.crs.to_epsg() == 4326
    assert edges.crs.to_epsg() == 4326
    assert not nodes.geometry.has_z.any()
    assert not edges.geometry.has_z.any()
    assert len(edges.geometry.iloc[0].coords) == 3
    assert set(nodes["region"]) == {"mauritius"}
    assert edges.loc[0, "region"] == "mauritius"
    assert edges["s_nom_mva"].isna().all()
    assert edges.loc[0, "model_s_nom_mva"] == 10_000.0
    assert manifest["schema_version"] == SPATIAL_SCHEMA_VERSION
    assert manifest["artifact_role"] == "visualisation_derivative"
    assert manifest["stage"] == "topology_only"
    assert manifest["inferred"] is False
    assert manifest["line_length_km"] == 12.5
    assert manifest["totals"] == {
        "nodes": 2,
        "edges": 1,
        "line_length_km": 12.5,
    }
    assert manifest["layers"]["nodes"]["row_count"] == 2
    assert manifest["layers"]["nodes"]["feature_count"] == 2
    assert manifest["layers"]["nodes"]["geometry_types"] == ["Point"]
    assert manifest["layers"]["edges"]["feature_count"] == 1
    assert manifest["source_network"]["sha256"] == hashlib.sha256(source_network.read_bytes()).hexdigest()
    assert manifest["source_metadata"]["sha256"] == hashlib.sha256(source_metadata.read_bytes()).hexdigest()


def test_inferred_export_hides_placeholder_electrical_values(tmp_path):
    buses, lines = _tables()
    buses["inferred"] = True
    buses["region"] = "rodrigues"
    lines["inferred"] = True
    lines["region"] = "rodrigues"
    lines["source"] = "osm"
    lines["v_nom_kv"] = 11.0
    lines["s_nom_mva"] = 5.0

    outputs = write_network_geoparquet(
        buses,
        lines,
        tmp_path,
        network_id="inferred-mauritius-rodrigues",
        network_source="inferred",
        methodology="osm-all-ways-connectivity-v3",
        source_network_path=_network_file(tmp_path),
        publish_voltage=False,
        publish_capacity=False,
    )
    nodes = gpd.read_parquet(outputs.nodes)
    edges = gpd.read_parquet(outputs.edges)

    assert nodes["v_nom_kv"].isna().all()
    assert edges["v_nom_kv"].isna().all()
    assert edges["s_nom_mva"].isna().all()
    assert edges.loc[0, "model_v_nom_kv"] == 11.0
    assert edges.loc[0, "model_s_nom_mva"] == 5.0
    assert set(edges["asset_type"]) == {"inferred_candidate"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda buses, lines: buses.__setitem__("bus_id", ["A", "A"]),
            "duplicate bus_id",
        ),
        (
            lambda buses, lines: lines.__setitem__("bus1", ["UNKNOWN"]),
            "unknown bus IDs",
        ),
        (
            lambda buses, lines: lines.__setitem__("length_km", [pd.NA]),
            "positive numbers",
        ),
    ],
)
def test_network_geoparquet_rejects_invalid_topology(tmp_path, mutation, message):
    buses, lines = _tables()
    mutation(buses, lines)

    with pytest.raises(ValueError, match=message):
        write_network_geoparquet(
            buses,
            lines,
            tmp_path,
            network_id="base-mauritius",
            network_source="base",
            methodology="test",
            source_network_path=_network_file(tmp_path),
            default_region="mauritius",
            publish_voltage=True,
            publish_capacity=False,
        )


def test_network_geoparquet_requires_expected_geometry_types(tmp_path):
    buses, lines = _tables()
    lines["geometry"] = [Point(57.5, -20.2)]

    with pytest.raises(ValueError, match="must all be LineString"):
        write_network_geoparquet(
            buses,
            lines,
            tmp_path,
            network_id="base-mauritius",
            network_source="base",
            methodology="test",
            source_network_path=_network_file(tmp_path),
            default_region="mauritius",
            publish_voltage=True,
            publish_capacity=False,
        )
