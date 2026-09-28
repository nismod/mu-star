import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.provided import (
    _extract_route_capacity_mw,
    _extract_route_voltage_kv,
    assign_generation_to_substations,
    classify_generation,
    extract_demand_workbook,
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


def test_demand_workbook_total_row_is_not_a_sector(tmp_path):
    rows = [
        ["Year", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        [2012, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400, 410],
        [None] * 13,
        ["Unit : GWh"] + [None] * 12,
        [None, 2012, 2013] + [None] * 10,
        ["Electricity demand - Domestic", 700, 710] + [None] * 10,
        ["Electricity demand - Commercial", 800, 810] + [None] * 10,
        ["Electricity demand \n(final)", 1500, 1520] + [None] * 10,
    ]
    workbook = tmp_path / "Power Demand.xlsx"
    pd.DataFrame(rows).to_excel(workbook, header=False, index=False)

    monthly_peak, annual = extract_demand_workbook(workbook)

    assert monthly_peak.loc[2012, "Dec"] == 410
    assert set(annual["category"]) == {"Domestic", "Commercial"}
    assert annual.groupby("year")["demand_gwh"].sum().loc[2012] == 1500


def test_report_generators_are_placed_and_assigned(tmp_path):
    from energy.provided import assemble_report_generators

    capacities = tmp_path / "capacities.csv"
    capacities.write_text(
        "report_name,group,technology,installed_capacity_mw,effective_capacity_mw,units_sent_out_kwh,report_page,site_match\n"
        "Big P/S,CEB,thermal,100,90,1,50,\n"
        "Wind farm,IPP,wind,9,9,1,51,\n"
        "Village PV,IPP,solar,2,2,1,51,\n"
        "Rooftops,distributed,solar,10,10,1,51,\n"
    )
    sites = tmp_path / "sites.csv"
    sites.write_text(
        "report_name,site_kind,site_name,lat,lon,location_basis\n"
        "Big P/S,provided,Big Power Station,,,\n"
        "Wind farm,provided,Turbine,,,\n"
        "Village PV,geocoded,,-20.30,57.60,Nominatim: Village\n"
        "Rooftops,distributed,,,,\n"
    )
    provided_sites = pd.DataFrame(
        {
            "name": ["Big Power Station", "Turbine 01", "Turbine 02"],
            "lon": [57.50, 57.70, 57.72],
            "lat": [-20.20, -20.10, -20.10],
        }
    )
    substations = gpd.GeoDataFrame(
        {"bus_id": ["A", "B"], "geometry": [Point(57.5, -20.2), Point(57.7, -20.1)]}, crs="EPSG:4326"
    )

    generators = assemble_report_generators(provided_sites, substations, capacities_path=capacities, sites_path=sites)

    by_name = generators.set_index("name")
    expected_ids = ["ceb-big-p-s", "ipp-wind-farm", "ipp-village-pv", "distributed-rooftops"]
    assert list(generators["generator_id"]) == expected_ids
    assert by_name.loc["Big P/S", "bus_id"] == "A"
    assert by_name.loc["Big P/S", "output_capacity_mw"] == 100 and by_name.loc["Big P/S", "effective_capacity_mw"] == 90
    assert by_name.loc["Wind farm", "lon"] == pytest.approx(57.71)  # centroid of the two turbine points
    assert by_name.loc["Wind farm", "bus_id"] == "B"
    assert by_name.loc["Village PV", "bus_id"] in {"A", "B"}
    assert pd.isna(by_name.loc["Rooftops", "bus_id"]) and pd.isna(by_name.loc["Rooftops", "lon"])
    assert generators["capacity_basis"].eq("electrical_output").all()
    assert generators["marginal_cost"].eq(0.0).all()


def test_report_generators_reject_an_unknown_site(tmp_path):
    from energy.provided import assemble_report_generators

    capacities = tmp_path / "capacities.csv"
    capacities.write_text(
        "report_name,group,technology,installed_capacity_mw,effective_capacity_mw,units_sent_out_kwh,report_page,site_match\n"
        "Big P/S,CEB,thermal,100,90,1,50,\n"
    )
    sites = tmp_path / "sites.csv"
    sites.write_text("report_name,site_kind,site_name,lat,lon,location_basis\nBig P/S,provided,Nowhere,,,\n")
    substations = gpd.GeoDataFrame({"bus_id": ["A"], "geometry": [Point(57.5, -20.2)]}, crs="EPSG:4326")

    provided_sites = pd.DataFrame({"name": ["Big Power Station"], "lon": [57.5], "lat": [-20.2]})
    with pytest.raises(ValueError, match="no provided site named"):
        assemble_report_generators(provided_sites, substations, capacities_path=capacities, sites_path=sites)
