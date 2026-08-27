import json

import geopandas as gpd
import pandas as pd
import pypsa
import pytest
from shapely import wkt
from shapely.geometry import LineString, Point

from energy.network_source import build_network
from energy.osm import OSMDownloadRequired


def _write_base_inputs(input_dir):
    input_dir.mkdir(parents=True)
    buses = gpd.GeoDataFrame(
        {
            "bus_id": ["A", "B"],
            "geometry": [Point(57.5, -20.2), Point(57.6, -20.2)],
        },
        crs="EPSG:4326",
    )
    buses.to_parquet(input_dir / "snapped_substations.parquet")
    gpd.GeoDataFrame(
        {
            "route_id": ["R1"],
            "v_nom_kv": [66],
            "geometry": [LineString([(57.5, -20.2), (57.55, -20.15), (57.6, -20.2)])],
        },
        crs="EPSG:4326",
    ).to_parquet(input_dir / "transmission_routes.parquet")
    pd.DataFrame(
        {
            "generator_id": ["plant"],
            "bus_id": ["A"],
            "carrier": ["thermal"],
            "output_capacity_mw": [100.0],
            "capacity_basis": ["electrical_output"],
            "marginal_cost": [10.0],
            "lon": [57.5],
            "lat": [-20.2],
        }
    ).to_csv(input_dir / "generators.csv", index=False)
    pd.DataFrame({"bus_id": ["A", "B"], "service_weight": [0.0, 1.0]}).to_csv(
        input_dir / "service_weights.csv", index=False
    )


def test_build_base_network_exports_network_files(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    output_dir = tmp_path / "processed" / "energy" / "networks"
    _write_base_inputs(input_dir)

    export_root = tmp_path / "out" / "energy"
    outputs = build_network(
        "base",
        input_dir=input_dir,
        output_dir=output_dir,
        export_root=export_root,
    )

    metadata = json.loads(outputs.metadata.read_text())
    assert outputs.network.is_file()
    assert outputs.network == output_dir / "base-mauritius" / "base-mauritius.nc"
    assert outputs.metadata.parent == output_dir / "base-mauritius"
    assert outputs.spatial_nodes.parent == output_dir / "base-mauritius" / "geoparquet"
    assert outputs.spatial_nodes.name == "base-mauritius-nodes.geoparquet"
    assert outputs.spatial_edges.name == "base-mauritius-edges.geoparquet"
    assert outputs.spatial_manifest.name == "base-mauritius-spatial-manifest.json"
    assert metadata["source"] == "base"
    assert metadata["methodology"] == "ceb-routed-topology-v3"
    assert metadata["line_geometry"] == "routed_wkt"
    assert metadata["buses"] == 2
    assert metadata["lines"] == 1
    assert metadata["generators"] == 1
    assert metadata["has_demand"] is False
    assert metadata["loads"] == 0
    assert metadata["spatial_nodes"] == str(outputs.spatial_nodes)
    assert metadata["spatial_edges"] == str(outputs.spatial_edges)
    assert metadata["spatial_manifest"] == str(outputs.spatial_manifest)
    assert metadata["inferred"] is False
    assert metadata["derived"] is True
    assert metadata["substations"] == 2
    assert metadata["cycle_rank"] == 0
    assert metadata["meaningful_cycle_count"] == 0
    assert metadata["ceb_topology_validation"]["status"] == "not_applicable"
    assert outputs.generators == export_root / "base-mauritius" / "generators.csv"
    assert outputs.lines == export_root / "base-mauritius" / "lines.csv"
    assert outputs.validation == export_root / "base-mauritius" / "validation.json"
    assert pd.read_csv(outputs.generators).loc[0, "output_capacity_mw"] == 100.0
    validation = json.loads(outputs.validation.read_text())
    assert validation["status"] == "valid_with_warnings"
    assert validation["totals"]["line_length_km"] > 10.0
    network = pypsa.Network(outputs.network)
    assert network.loads.empty
    assert "geometry" in network.lines
    assert "source_route_part_id" in network.lines
    assert "circuit_id" in network.lines
    assert len(wkt.loads(network.lines.loc["BASE_LINE_001", "geometry"]).coords) == 3
    spatial_nodes = gpd.read_parquet(outputs.spatial_nodes)
    spatial_edges = gpd.read_parquet(outputs.spatial_edges)
    spatial_manifest = json.loads(outputs.spatial_manifest.read_text())
    assert set(spatial_nodes["asset_id"]) == set(network.buses.index)
    assert set(spatial_edges["asset_id"]) == set(network.lines.index)
    assert spatial_edges["s_nom_mva"].isna().all()
    assert len(spatial_edges.geometry.iloc[0].coords) == 3
    assert spatial_manifest["stage"] == "topology_only"
    assert spatial_manifest["inferred"] is False
    assert spatial_manifest["source_metadata"]["path"] == outputs.metadata.name
    assert spatial_manifest["totals"]["nodes"] == len(network.buses)
    assert spatial_manifest["totals"]["edges"] == len(network.lines)


def test_build_base_network_requires_prepared_inputs(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"transmission_routes\.parquet"):
        build_network(
            "base",
            input_dir=tmp_path / "missing",
            output_dir=tmp_path / "networks",
        )


def test_build_inferred_without_region_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="requires a region"):
        build_network(
            "inferred-osm",
            input_dir=tmp_path / "inputs",
            output_dir=tmp_path / "networks",
        )


def test_build_network_rejects_unknown_source(tmp_path):
    with pytest.raises(ValueError, match="source must be"):
        build_network(
            "augmented",
            input_dir=tmp_path / "inputs",
            output_dir=tmp_path / "networks",
        )


def test_build_base_rejects_region(tmp_path):
    with pytest.raises(ValueError, match="region can only be used"):
        build_network(
            "base",
            region="mauritius",
            input_dir=tmp_path / "inputs",
            output_dir=tmp_path / "networks",
        )


def test_build_network_refuses_to_overwrite(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    output_dir = tmp_path / "processed" / "energy" / "networks"
    _write_base_inputs(input_dir)
    build_network("base", input_dir=input_dir, output_dir=output_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        build_network("base", input_dir=input_dir, output_dir=output_dir)
    outputs = build_network("base", input_dir=input_dir, output_dir=output_dir, overwrite=True)
    assert outputs.network.is_file()


def test_build_inferred_network_for_region_uses_osm_fixtures(tmp_path, monkeypatch):
    roads = gpd.GeoDataFrame(
        {
            "source": ["osm_roads"],
            "region": ["rodrigues"],
            "geometry": [LineString([(63.42, -19.72), (63.421, -19.72)])],
        },
        crs="EPSG:4326",
    )
    power = gpd.GeoDataFrame(
        {
            "source": ["osm_power"],
            "region": ["rodrigues"],
            "bus_id": ["RODRIGUES_SUB_001"],
            "power": ["substation"],
            "geometry": [Point(63.42, -19.72)],
        },
        crs="EPSG:4326",
    )

    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_roads",
        lambda region, **kwargs: roads,
    )
    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_power_features",
        lambda region, **kwargs: power,
    )

    output_dir = tmp_path / "processed" / "energy" / "networks"
    outputs = build_network(
        "inferred-osm",
        region="rodrigues",
        input_dir=tmp_path / "inputs",
        output_dir=output_dir,
        nightlight_targets=roads,
        max_anchor_distance_m=100,
        export_root=tmp_path / "out" / "energy",
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)

    assert outputs.network.name == "inferred-osm-rodrigues.nc"
    assert outputs.network.parent == output_dir / "inferred-osm-rodrigues"
    assert outputs.metadata.name == "inferred-osm-rodrigues_metadata.json"
    assert outputs.spatial_nodes.parent == (output_dir / "inferred-osm-rodrigues" / "geoparquet")
    assert outputs.spatial_nodes.name == "inferred-osm-rodrigues-nodes.geoparquet"
    assert outputs.spatial_edges.name == "inferred-osm-rodrigues-edges.geoparquet"
    assert outputs.inferred_nodes.parent.name == "inferred_distribution"
    assert metadata["region"] == "rodrigues"
    assert metadata["methodology"] == "nightlight-roads-osm-power-v1"
    assert metadata["distance_method"] == "WGS84 geodesic"
    assert metadata["nightlight_policy"].startswith("viirs_targets_filter")
    assert metadata["road_envelope_edges"] == 1
    assert metadata["osm_power_features"] == 1
    assert metadata["substation_roots"] == 1
    assert metadata["generator_roots"] == 0
    assert metadata["anchored_power_assets"] == 1
    assert metadata["has_demand"] is False
    assert network.loads.empty
    assert len(network.lines) >= 1
    assert "geometry" in network.lines
    assert set(network.buses["region"]) == {"rodrigues"}
    assert set(network.lines["region"]) == {"rodrigues"}
    assert len(wkt.loads(network.lines.iloc[0]["geometry"]).coords) == 2
    spatial_nodes = gpd.read_parquet(outputs.spatial_nodes)
    spatial_edges = gpd.read_parquet(outputs.spatial_edges)
    spatial_manifest = json.loads(outputs.spatial_manifest.read_text())
    assert set(spatial_nodes["asset_id"]) == set(network.buses.index)
    assert set(spatial_edges["asset_id"]) == set(network.lines.index)
    assert spatial_edges["v_nom_kv"].isna().all()
    assert spatial_edges["s_nom_mva"].isna().all()
    assert set(spatial_edges["region"]) == {"rodrigues"}
    assert spatial_manifest["stage"] == "connectivity_only"
    assert spatial_manifest["inferred"] is True
    assert spatial_manifest["source_metadata"]["path"] == outputs.metadata.name
    assert outputs.generators.parent.name == "inferred-osm-rodrigues"
    assert pd.read_csv(outputs.generators).empty
    inferred_validation = json.loads(outputs.validation.read_text())
    assert inferred_validation["status"] == "valid_with_warnings"
    assert any("cannot supply demand" in warning for warning in inferred_validation["warnings"])
    assert any("Model line length differs" in warning for warning in inferred_validation["warnings"])
    length_check = inferred_validation["checks"]["line_length_against_published_ceb_total"]
    assert length_check["status"] == "warning"
    assert length_check["reference_total_km"] == 10_492.2
    assert length_check["reference_scope"].startswith("CEB total transmission")


def test_build_inferred_uses_nightlight_targets_and_cached_roads_without_power(
    tmp_path,
    monkeypatch,
):
    roads = gpd.GeoDataFrame(
        {
            "source": ["osm_roads"],
            "region": ["rodrigues"],
            "geometry": [LineString([(63.42, -19.72), (63.421, -19.72)])],
        },
        crs="EPSG:4326",
    )
    targets = gpd.GeoDataFrame(
        {
            "source": ["nightlight"],
            "region": ["rodrigues"],
            "geometry": [LineString([(63.422, -19.721), (63.423, -19.721)])],
        },
        crs="EPSG:4326",
    )

    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_roads",
        lambda region, **kwargs: roads,
    )

    def missing_power(region, **kwargs):
        raise OSMDownloadRequired("power cache missing")

    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_power_features",
        missing_power,
    )

    outputs = build_network(
        "inferred-osm",
        region="rodrigues",
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "networks",
        nightlight_targets=targets,
        max_anchor_distance_m=1000,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)

    assert metadata["road_envelope_edges"] == 1
    assert metadata["provisional_roots"] == 1
    assert any(str(line_id).startswith("osm_") for line_id in network.lines.index)
    assert metadata["nightlight_supported_roads"]["supported_road_features"] == 1


def test_build_inferred_uses_osm_substations_and_generators_as_roots(tmp_path, monkeypatch):
    roads = gpd.GeoDataFrame(
        {
            "source": ["osm_roads"],
            "region": ["mauritius"],
            "geometry": [LineString([(57.5, -20.2), (57.501, -20.2)])],
        },
        crs="EPSG:4326",
    )
    power = gpd.GeoDataFrame(
        {
            "source": ["osm_power"] * 3,
            "region": ["mauritius"] * 3,
            "bus_id": ["GEN", "SUB", "PLANT"],
            "power": ["generator", "substation", "plant"],
            "geometry": [
                Point(57.5002, -20.2),
                Point(57.5, -20.2),
                Point(57.5004, -20.2),
            ],
        },
        crs="EPSG:4326",
    )
    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_roads",
        lambda region, **kwargs: roads,
    )
    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_power_features",
        lambda region, **kwargs: power,
    )

    outputs = build_network(
        "inferred-osm",
        region="mauritius",
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "networks",
        nightlight_targets=roads,
    )
    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)
    roots = network.buses[network.buses["is_root"].astype(bool)]

    assert metadata["osm_power_features"] == 3
    assert metadata["substation_roots"] == 1
    assert metadata["generator_roots"] == 2
    assert metadata["provisional_roots"] == 0
    assert metadata["anchored_power_assets"] == 3
    assert set(roots.index) == {"asset::GEN", "bus::SUB", "asset::PLANT"}
    assert roots.loc["bus::SUB", "source"] == "osm_power"
    assert not bool(roots.loc["bus::SUB", "provisional_root"])


def test_build_inferred_provided_uses_only_provided_power_assets(tmp_path, monkeypatch):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    _write_base_inputs(input_dir)
    roads = gpd.GeoDataFrame(
        {
            "source": ["osm_roads"],
            "region": ["mauritius"],
            "geometry": [LineString([(57.5, -20.2), (57.6, -20.2)])],
        },
        crs="EPSG:4326",
    )
    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_roads",
        lambda region, **kwargs: roads,
    )

    def unexpected_osm_power(*args, **kwargs):
        raise AssertionError("inferred-provided must not read OSM power features")

    monkeypatch.setattr(
        "energy.network_source.osm.fetch_osm_power_features",
        unexpected_osm_power,
    )

    outputs = build_network(
        "inferred-provided",
        region="mauritius",
        input_dir=input_dir,
        output_dir=tmp_path / "networks",
        nightlight_targets=roads,
        max_anchor_distance_m=20_000,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)
    assert metadata["methodology"] == "nightlight-roads-provided-power-v1"
    assert metadata["power_asset_source"] == "provided_substations_and_generators"
    assert metadata["substation_roots"] == 2
    assert metadata["generator_roots"] == 1
    assert metadata["osm_power_features"] == 0
    assert metadata["provided_backbone_edges"] >= 1
    assert set(network.generators.index) == {"plant"}
    assert network.generators.loc["plant", "bus"] == "bus::A"
    assert {"bus::A", "bus::B", "asset::plant"} <= set(network.buses.index)
