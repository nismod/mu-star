"""Clean the provided energy source data and write the analysis-ready tables.

Reads the provided shapefiles and demand workbook, applies the transforms in
energy.intake, and writes the substation, route, generator, demand and template
tables the network builds consume.
"""

from pathlib import Path

import click
import geopandas as gpd
import pandas as pd

from energy.distribution import build_service_weights
from energy.intake import (
    GEOGRAPHIC_CRS,
    METRIC_CRS,
    _clean_label,
    _extract_route_capacity_mw,
    _extract_route_voltage_kv,
    _read_gdf,
    _station_points_from_areas,
    apply_generator_capacity_reference,
    assign_generation_to_substations,
    classify_generation,
    extract_demand_workbook,
    snap_substations_to_routes,
    validate_provided_inputs,
)
from energy.network_tables import write_input_templates


@click.command()
@click.option(
    "--input-dir",
    "input_dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=str),
)
@click.option(
    "--output-dir",
    "output_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=str),
)
def main(input_dir, output_dir):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    validate_provided_inputs(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    substations = _read_gdf(input_dir / "substation" / "Substation.shp").reset_index(drop=True)
    substations["bus_id"] = [f"SUB_{index + 1:03d}" for index in substations.index]
    substations["name"] = substations["bus_id"]
    substations["asset_type"] = "substation"

    routes = _read_gdf(input_dir / "power_transmission" / "PowerGrid.shp").reset_index(drop=True)
    routes["route_id"] = [f"ROUTE_{index + 1:03d}" for index in routes.index]
    routes["name"] = routes["Name"].combine_first(routes["FolderPath"]).apply(_clean_label)
    routes["v_nom_kv"] = _extract_route_voltage_kv(routes)
    routes["capacity_mw"] = _extract_route_capacity_mw(routes)
    routes["capacity_unit"] = "MW"
    routes["length_km"] = routes.to_crs(METRIC_CRS).length / 1000
    snapped_substations = snap_substations_to_routes(substations, routes)

    points = _read_gdf(input_dir / "generation_source" / "GenSource1.shp").reset_index(drop=True)
    points["asset_id"] = [f"GEN_POINT_{index + 1:03d}" for index in points.index]
    points["name"] = points["Name"].apply(_clean_label)
    points["asset_type"] = points.apply(classify_generation, axis=1)

    areas = _read_gdf(input_dir / "generation_source" / "GenSource2.shp").reset_index(drop=True)
    areas["asset_id"] = [f"GEN_AREA_{index + 1:03d}" for index in areas.index]
    areas["label"] = areas["Name"].apply(_clean_label)
    areas["category"] = areas.apply(classify_generation, axis=1)
    areas["area_m2"] = areas.to_crs(METRIC_CRS).area
    areas["is_named"] = ~areas["label"].isin(["Placemark", "unnamed"])

    named_point_assets = points[points["name"].ne("Placemark")].rename(columns={"asset_type": "asset_type"})[
        ["asset_id", "name", "asset_type", "geometry"]
    ]
    named_area_assets = _station_points_from_areas(areas)
    generation_sites = gpd.GeoDataFrame(
        pd.concat([named_point_assets, named_area_assets], ignore_index=True),
        geometry="geometry",
        crs=GEOGRAPHIC_CRS,
    ).rename(columns={"asset_id": "generator_id"})
    generation_sites = apply_generator_capacity_reference(generation_sites)
    generation_sites["capacity_basis"] = "electrical_output"
    generation_sites["capacity_unit"] = "MW_e"
    generation_sites["carrier"] = generation_sites["asset_type"]
    generation_sites["fuel_energy_basis"] = pd.NA
    generation_sites["source"] = "provided_geometry"
    generation_sites = assign_generation_to_substations(
        generation_sites,
        snapped_substations,
    )
    generation_sites["lon"] = generation_sites.geometry.x
    generation_sites["lat"] = generation_sites.geometry.y

    monthly_peak, annual_demand = extract_demand_workbook(input_dir / "power_demand" / "Power Demand.xlsx")

    substation_path = output_dir / "substations.parquet"
    snapped_substation_path = output_dir / "snapped_substations.parquet"
    snap_distance_path = output_dir / "substation_snap_distances.csv"
    route_path = output_dir / "transmission_routes.parquet"
    point_path = output_dir / "generation_points.parquet"
    area_path = output_dir / "generation_areas.parquet"
    generators_path = output_dir / "generators.csv"
    peak_path = output_dir / "monthly_peak_demand_mw.csv"
    annual_path = output_dir / "annual_sector_demand_gwh.csv"
    service_weights_path = output_dir / "service_weights.csv"

    substations[["bus_id", "name", "asset_type", "geometry"]].to_parquet(substation_path)
    snapped_substations[
        [
            "bus_id",
            "name",
            "asset_type",
            "original_lon",
            "original_lat",
            "snap_distance_m",
            "snapped_route_id",
            "snapped_route_name",
            "snapped_route_part_id",
            "snapped_lon",
            "snapped_lat",
            "geometry",
        ]
    ].to_parquet(snapped_substation_path)
    snapped_substations[
        [
            "bus_id",
            "original_lon",
            "original_lat",
            "snapped_lon",
            "snapped_lat",
            "snap_distance_m",
            "snapped_route_id",
            "snapped_route_name",
            "snapped_route_part_id",
        ]
    ].sort_values("snap_distance_m", ascending=False).to_csv(
        snap_distance_path,
        index=False,
    )
    routes[
        [
            "route_id",
            "name",
            "v_nom_kv",
            "capacity_mw",
            "capacity_unit",
            "length_km",
            "geometry",
        ]
    ].to_parquet(route_path)
    points[["asset_id", "name", "asset_type", "PopupInfo", "geometry"]].to_parquet(point_path)
    areas[["asset_id", "label", "category", "area_m2", "is_named", "geometry"]].to_parquet(area_path)
    generation_sites.drop(columns="geometry").to_csv(generators_path, index=False)
    monthly_peak.to_csv(peak_path)
    annual_demand.to_csv(annual_path, index=False)
    build_service_weights(snapped_substations).to_csv(
        service_weights_path,
        index=False,
    )
    write_input_templates(output_dir.parent / "templates")


if __name__ == "__main__":
    main()
