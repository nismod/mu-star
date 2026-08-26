import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.intake import (
    _extract_route_capacity_mw,
    _extract_route_voltage_kv,
    apply_generator_capacity_reference,
    assign_generation_to_substations,
    classify_generation,
    snap_substations_to_routes,
    validate_provided_inputs,
)


def test_provided_input_check_lists_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError) as error:
        validate_provided_inputs(tmp_path)

    message = str(error.value)
    assert str(tmp_path) in message
    assert "power_demand/Power Demand.xlsx" in message
    assert "data/incoming/energy/provided" in message


def test_snap_substations_to_nearest_route_and_record_distance():
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["SUB_001", "SUB_002"],
            "name": ["A", "B"],
            "asset_type": ["substation", "substation"],
            "geometry": [Point(57.5, -20.2), Point(57.6, -20.21)],
        },
        crs="EPSG:4326",
    )
    routes = gpd.GeoDataFrame(
        {
            "route_id": ["ROUTE_001"],
            "geometry": [LineString([(57.4, -20.2), (57.7, -20.2)])],
        },
        crs="EPSG:4326",
    )

    snapped = snap_substations_to_routes(substations, routes)

    assert snapped["snapped_route_id"].eq("ROUTE_001").all()
    assert snapped["snapped_route_part_id"].eq("ROUTE_001_PART_001").all()
    assert snapped.loc[0, "snap_distance_m"] < 10
    assert snapped.loc[1, "snap_distance_m"] > 1_000
    assert snapped.loc[1, "geometry"].y == pytest.approx(-20.2, abs=1e-4)
    assert snapped.loc[1, "original_lat"] == pytest.approx(-20.21)


def test_route_voltage_and_capacity_are_read_from_explicit_source_fields_or_labels():
    routes = gpd.GeoDataFrame(
        {
            "Name": ["CEB 66 KV Line A / B", "unlabelled", "rated line"],
            "FolderPath": ["", "", ""],
            "voltage_kv": [None, 132, None],
            "capacity_mw": [None, None, 40],
            "geometry": [
                LineString([(57.4, -20.2), (57.5, -20.2)]),
                LineString([(57.5, -20.2), (57.6, -20.2)]),
                LineString([(57.6, -20.2), (57.7, -20.2)]),
            ],
        },
        crs="EPSG:4326",
    )

    voltage = _extract_route_voltage_kv(routes)
    capacity = _extract_route_capacity_mw(routes)

    assert voltage.iloc[0] == 66.0
    assert voltage.iloc[1] == 132.0
    assert pd.isna(voltage.iloc[2])
    assert capacity.iloc[:2].isna().all()
    assert capacity.iloc[2] == 40.0


def test_ferney_is_classified_as_hydro_from_the_provided_label():
    row = pd.Series({"Name": "Ferney Power Station"})

    assert classify_generation(row) == "hydro"


def test_generation_sites_are_assigned_to_nearest_substation():
    generators = gpd.GeoDataFrame(
        {"generator_id": ["G1"], "geometry": [Point(57.501, -20.2)]},
        crs="EPSG:4326",
    )
    substations = gpd.GeoDataFrame(
        {
            "bus_id": ["A", "B"],
            "geometry": [Point(57.5, -20.2), Point(57.6, -20.2)],
        },
        crs="EPSG:4326",
    )

    result = assign_generation_to_substations(generators, substations)

    assert result.loc[0, "bus_id"] == "A"
    assert result.loc[0, "bus_assignment_distance_m"] < 200


def test_report_capacity_is_split_across_duplicate_site_geometries():
    generators = gpd.GeoDataFrame(
        {
            "generator_id": ["G1", "G2"],
            "name": ["Sarako", "Sarako"],
            "geometry": [Point(57.42, -20.26), Point(57.43, -20.26)],
        },
        crs="EPSG:4326",
    )

    result = apply_generator_capacity_reference(generators)

    assert result["output_capacity_mw"].sum() == pytest.approx(15.19)
    assert result["marginal_cost"].eq(0.0).all()
    assert result["marginal_cost_basis"].eq("equal_dispatch_proxy_for_voll").all()
