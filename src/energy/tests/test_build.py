import json

import geopandas as gpd
import pandas as pd
import pypsa
import pytest
from shapely import wkt
from shapely.geometry import LineString, Point

from energy.build import build_network


def _write_base_inputs(input_dir, extra_generator_rows=()):
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
    generators = pd.DataFrame(
        [
            {
                "generator_id": "plant",
                "bus_id": "A",
                "carrier": "thermal",
                "output_capacity_mw": 100.0,
                "capacity_basis": "electrical_output",
                "marginal_cost": 10.0,
                "lon": 57.5,
                "lat": -20.2,
            },
            *extra_generator_rows,
        ]
    )
    generators.to_csv(input_dir / "generators.csv", index=False)
    pd.DataFrame({"bus_id": ["A", "B"], "service_weight": [0.0, 1.0]}).to_csv(
        input_dir / "service_weights.csv", index=False
    )


def _roads(tmp_path, coords, region):
    frame = gpd.GeoDataFrame(
        {"source": ["osm_roads"], "region": [region], "highway": ["residential"], "geometry": [LineString(coords)]},
        crs="EPSG:4326",
    )
    path = tmp_path / "cache" / region / "roads.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    return frame, path


def _power(tmp_path, region, rows):
    frame = gpd.GeoDataFrame(
        {
            "source": ["osm_power"] * len(rows),
            "region": [region] * len(rows),
            "bus_id": [row[0] for row in rows],
            "power": [row[1] for row in rows],
            "geometry": [row[2] for row in rows],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / "cache" / region / "power.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    return path


def test_build_base_network_exports_network_files(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    output_dir = tmp_path / "processed" / "energy" / "networks"
    _write_base_inputs(input_dir)

    export_root = tmp_path / "results" / "energy"
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


def test_build_inferred_reports_missing_cache_with_the_rule_to_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_energy_osm"):
        build_network(
            "inferred-osm",
            region="rodrigues",
            input_dir=tmp_path / "inputs",
            output_dir=tmp_path / "networks",
            roads_path=tmp_path / "absent-roads.parquet",
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


def test_build_inferred_network_for_region_uses_cached_osm_files(tmp_path):
    roads, roads_path = _roads(tmp_path, [(63.42, -19.72), (63.421, -19.72)], "rodrigues")
    power_path = _power(tmp_path, "rodrigues", [("RODRIGUES_SUB_001", "substation", Point(63.42, -19.72))])
    targets_path = tmp_path / "nightlight" / "targets.geoparquet"
    targets_path.parent.mkdir(parents=True)
    roads.to_parquet(targets_path)
    (targets_path.parent / "metadata.json").write_text(json.dumps({"nightlight_threshold": 0.2, "region": "rodrigues"}))

    output_dir = tmp_path / "processed" / "energy" / "networks"
    outputs = build_network(
        "inferred-osm",
        region="rodrigues",
        input_dir=tmp_path / "inputs",
        output_dir=output_dir,
        roads_path=roads_path,
        power_path=power_path,
        nightlight_targets=targets_path,
        max_anchor_distance_m=100,
        export_root=tmp_path / "results" / "energy",
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
    assert metadata["osm_road_envelope_cache"] == str(roads_path)
    assert metadata["osm_power_cache"] == str(power_path)
    assert metadata["osm_power_features"] == 1
    assert metadata["substation_roots"] == 1
    assert metadata["generator_roots"] == 0
    assert metadata["anchored_power_assets"] == 1
    assert metadata["has_demand"] is False
    # The metadata records the targets file, its SHA-256 and its metadata.json.
    assert metadata["nightlight_targets"] == str(targets_path)
    assert len(metadata["nightlight_targets_sha256"]) == 64
    assert metadata["nightlight_targets_metadata"]["nightlight_threshold"] == 0.2
    assert "nightlights" not in metadata
    assert network.loads.empty
    assert len(network.lines) >= 1
    assert "geometry" in network.lines
    assert set(network.buses["region"]) == {"rodrigues"}
    assert set(network.lines["region"]) == {"rodrigues"}
    assert set(network.buses["v_nom"]) == {11.0}
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
    assert any(warning.startswith("Line length is") for warning in inferred_validation["warnings"])
    length_check = inferred_validation["checks"]["line_length_against_published_ceb_total"]
    assert length_check["status"] == "warning"
    assert length_check["reference_total_km"] == 10_492.2
    assert length_check["reference_scope"] == "CEB's circuit length at all voltages"


def test_build_inferred_places_a_provisional_root_when_a_region_has_no_power_assets(tmp_path):
    _, roads_path = _roads(tmp_path, [(63.42, -19.72), (63.421, -19.72)], "rodrigues")
    power_path = _power(tmp_path, "rodrigues", [])
    targets = gpd.GeoDataFrame(
        {
            "source": ["nightlight"],
            "region": ["rodrigues"],
            "geometry": [LineString([(63.422, -19.721), (63.423, -19.721)])],
        },
        crs="EPSG:4326",
    )

    outputs = build_network(
        "inferred-osm",
        region="rodrigues",
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        power_path=power_path,
        nightlight_targets=targets,
        max_anchor_distance_m=1000,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)

    assert metadata["road_envelope_edges"] == 1
    assert metadata["osm_power_features"] == 0
    assert metadata["provisional_roots"] == 1
    assert metadata["nightlight_targets"] is None
    assert any(str(line_id).startswith("osm_") for line_id in network.lines.index)
    assert metadata["nightlight_supported_roads"]["supported_road_features"] == 1
    assert "bus::RODRIGUES_PROVISIONAL_ROOT" in network.buses.index


def test_build_inferred_uses_osm_substations_and_generators_as_roots(tmp_path):
    roads, roads_path = _roads(tmp_path, [(57.5, -20.2), (57.501, -20.2)], "mauritius")
    power_path = _power(
        tmp_path,
        "mauritius",
        [
            ("GEN", "generator", Point(57.5002, -20.2)),
            ("SUB", "substation", Point(57.5, -20.2)),
            ("PLANT", "plant", Point(57.5004, -20.2)),
        ],
    )

    outputs = build_network(
        "inferred-osm",
        region="mauritius",
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        power_path=power_path,
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


def test_unanchored_asset_keeps_the_distribution_voltage(tmp_path):
    roads, roads_path = _roads(tmp_path, [(57.5, -20.2), (57.501, -20.2)], "mauritius")
    power_path = _power(
        tmp_path,
        "mauritius",
        [("NEAR", "substation", Point(57.5, -20.2)), ("FAR", "substation", Point(57.55, -20.25))],
    )

    outputs = build_network(
        "inferred-osm",
        region="mauritius",
        input_dir=tmp_path / "inputs",
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        power_path=power_path,
        nightlight_targets=roads,
        max_anchor_distance_m=500,
        inferred_voltage_kv=11,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)
    assert metadata["unanchored_power_assets"] == 1
    assert network.buses.loc["bus::FAR", "anchor_status"] == "unanchored"
    assert set(network.buses["v_nom"]) == {11.0}


def test_build_inferred_provided_uses_only_provided_power_assets(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    _write_base_inputs(input_dir)
    roads, roads_path = _roads(tmp_path, [(57.5, -20.2), (57.6, -20.2)], "mauritius")

    outputs = build_network(
        "inferred-provided",
        region="mauritius",
        input_dir=input_dir,
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        nightlight_targets=roads,
        max_anchor_distance_m=20_000,
        base_route_gap_tolerance_m=120,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)
    assert metadata["methodology"] == "nightlight-roads-provided-power-v1"
    assert metadata["power_asset_source"] == "provided_substations_and_generators"
    assert metadata["substation_roots"] == 2
    assert metadata["generator_roots"] == 1
    assert metadata["osm_power_features"] == 0
    assert metadata["osm_power_cache"] is None
    assert metadata["provided_backbone_edges"] >= 1
    assert metadata["provided_backbone_route_gap_tolerance_m"] == 120
    assert set(network.generators.index) == {"plant"}
    assert network.generators.loc["plant", "bus"] == "bus::A"
    assert {"bus::A", "bus::B", "asset::plant"} <= set(network.buses.index)
    # The CEB 66 kV lines keep 66 kV and meet the 11 kV roads through transformers.
    assert metadata["inferred_transmission_voltage_kv"] == 66
    assert metadata["transformers"] >= 1
    assert len(network.transformers) == metadata["transformers"]
    bus_voltages = set(network.buses["v_nom"].round().astype(int))
    assert 66 in bus_voltages
    assert 11 in bus_voltages


def test_build_inferred_provided_connects_an_island_plant_at_its_own_node(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    island_plant = {
        "generator_id": "island-plant",
        "bus_id": None,
        "region": "rodrigues",
        "carrier": "thermal",
        "output_capacity_mw": 6.0,
        "capacity_basis": "electrical_output",
        "marginal_cost": 0.0,
        "lon": 63.42,
        "lat": -19.68,
    }
    _write_base_inputs(input_dir, extra_generator_rows=[island_plant])
    generators = pd.read_csv(input_dir / "generators.csv")
    generators["region"] = generators["region"].fillna("mauritius")
    generators.to_csv(input_dir / "generators.csv", index=False)
    mauritius_roads, _ = _roads(tmp_path, [(57.5, -20.2), (57.6, -20.2)], "mauritius")
    rodrigues_roads, _ = _roads(tmp_path, [(63.41, -19.68), (63.43, -19.68)], "rodrigues")
    roads = gpd.GeoDataFrame(pd.concat([mauritius_roads, rodrigues_roads], ignore_index=True), crs="EPSG:4326")
    roads_path = tmp_path / "cache" / "mauritius-rodrigues" / "roads.parquet"
    roads_path.parent.mkdir(parents=True)
    roads.to_parquet(roads_path)

    outputs = build_network(
        "inferred-provided",
        region="mauritius-rodrigues",
        input_dir=input_dir,
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        nightlight_targets=roads,
        max_anchor_distance_m=20_000,
    )

    metadata = json.loads(outputs.metadata.read_text())
    network = pypsa.Network(outputs.network)
    # The Rodrigues plant is a power asset, so no placeholder substation is added;
    # the plant connects to the island's roads from its own node.
    assert metadata["provisional_roots"] == 0
    assert network.generators.loc["island-plant", "bus"] == "asset::island-plant"
    assert network.generators.loc["plant", "bus"] == "bus::A"
    assert "bus::RODRIGUES_PROVISIONAL_ROOT" not in network.buses.index
    assert (
        network.lines["bus0"].eq("asset::island-plant").any() or network.lines["bus1"].eq("asset::island-plant").any()
    )


def test_build_inferred_provided_keeps_a_generator_without_bus_for_review(tmp_path):
    input_dir = tmp_path / "processed" / "energy" / "provided"
    orphan = {
        "generator_id": "orphan",
        "bus_id": None,
        "carrier": "solar",
        "output_capacity_mw": 5.0,
        "capacity_basis": "electrical_output",
        "marginal_cost": 0.0,
        "lon": 57.55,
        "lat": -20.2,
    }
    _write_base_inputs(input_dir, extra_generator_rows=[orphan])
    roads, roads_path = _roads(tmp_path, [(57.5, -20.2), (57.6, -20.2)], "mauritius")

    outputs = build_network(
        "inferred-provided",
        region="mauritius",
        input_dir=input_dir,
        output_dir=tmp_path / "networks",
        roads_path=roads_path,
        nightlight_targets=roads,
        max_anchor_distance_m=20_000,
        export_root=tmp_path / "results",
    )

    network = pypsa.Network(outputs.network)
    validation = json.loads(outputs.validation.read_text())
    assert set(network.generators.index) == {"plant"}
    assert any("left out of the network because they have no bus_id" in warning for warning in validation["warnings"])
    exported = pd.read_csv(outputs.generators)
    assert exported.loc[exported["generator_id"].eq("orphan"), "bus_id"].isna().all()
