"""Convert provided source files into stable analysis-ready asset layers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import nearest_points

from energy.distribution import build_service_weights
from energy.network_tables import write_input_templates

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
METRIC_CRS = "EPSG:32740"
GEOGRAPHIC_CRS = "EPSG:4326"
GENERATOR_CAPACITY_REFERENCE = Path(__file__).parent / "resources" / "generator_capacity_reference.csv"
CEB_ANNUAL_REPORT_URL = "https://ceb.mu/files/files/publications/Annual%20Report/CEB%20AR%202023-2024.pdf"
REQUIRED_PROVIDED_FILES = (
    "power_demand/Power Demand.xlsx",
    "substation/Substation.shp",
    "substation/Substation.shx",
    "substation/Substation.dbf",
    "substation/Substation.prj",
    "power_transmission/PowerGrid.shp",
    "power_transmission/PowerGrid.shx",
    "power_transmission/PowerGrid.dbf",
    "power_transmission/PowerGrid.prj",
    "generation_source/GenSource1.shp",
    "generation_source/GenSource1.shx",
    "generation_source/GenSource1.dbf",
    "generation_source/GenSource1.prj",
    "generation_source/GenSource2.shp",
    "generation_source/GenSource2.shx",
    "generation_source/GenSource2.dbf",
    "generation_source/GenSource2.prj",
)


@dataclass(frozen=True)
class PreparedAssets:
    substations: Path
    snapped_substations: Path
    substation_snap_distances: Path
    transmission_routes: Path
    generation_points: Path
    generation_areas: Path
    generators: Path
    service_weights: Path
    generator_input_template: Path
    line_input_template: Path
    monthly_peak_demand: Path
    annual_sector_demand: Path


def validate_provided_inputs(input_dir: Path) -> None:
    """Give a clear error when a required source file is missing."""
    input_dir = Path(input_dir)
    missing = [relative_path for relative_path in REQUIRED_PROVIDED_FILES if not (input_dir / relative_path).is_file()]
    if not missing:
        return

    missing_list = "\n".join(f"  - {path}" for path in missing)
    message = (
        f"Provided input data are incomplete at:\n  {input_dir}\n\n"
        f"Missing files:\n{missing_list}\n\n"
        "Place the complete source folders under "
        "data/incoming/energy/provided, or set MU_STAR_DATA_ROOT to a "
        "data directory containing the same incoming/energy/provided "
        "structure."
    )
    raise FileNotFoundError(message)


def _read_gdf(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(GEOGRAPHIC_CRS)
    return gdf.to_crs(GEOGRAPHIC_CRS)


def _clean_label(value: object, fallback: str = "unnamed") -> str:
    if pd.isna(value):
        return fallback
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or fallback


def _combined_source_text(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    existing_columns = [column for column in columns if column in frame.columns]
    if not existing_columns:
        return pd.Series("", index=frame.index)
    return frame[existing_columns].fillna("").astype(str).agg(" ".join, axis=1)


def _first_numeric_source_column(
    frame: pd.DataFrame,
    candidates: tuple[str, ...],
) -> pd.Series:
    for column in candidates:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().any():
            return values
    return pd.Series(np.nan, index=frame.index, dtype="float64")


def _extract_route_voltage_kv(routes: pd.DataFrame) -> pd.Series:
    """Read route voltage from explicit fields, falling back to route labels."""
    values = _first_numeric_source_column(
        routes,
        (
            "v_nom_kv",
            "voltage_kv",
            "voltage",
            "Voltage",
            "V_NOM_KV",
            "KV",
            "kV",
        ),
    )
    missing = values.isna()
    if missing.any():
        text = _combined_source_text(
            routes,
            ("Name", "FolderPath", "PopupInfo", "Snippet"),
        )
        labelled = text.str.extract(
            r"(\d+(?:\.\d+)?)\s*kV\b",
            flags=re.IGNORECASE,
            expand=False,
        )
        values = values.combine_first(pd.to_numeric(labelled, errors="coerce"))
    return values


def _extract_route_capacity_mw(routes: pd.DataFrame) -> pd.Series:
    """Read route power rating from explicit MW fields or labels when present."""
    values = _first_numeric_source_column(
        routes,
        (
            "capacity_mw",
            "rating_mw",
            "power_mw",
            "CapacityMW",
            "RatingMW",
            "MW",
        ),
    )
    missing = values.isna()
    if missing.any():
        text = _combined_source_text(
            routes,
            ("Name", "FolderPath", "PopupInfo", "Snippet"),
        )
        labelled = text.str.extract(
            r"(\d+(?:\.\d+)?)\s*MW\b",
            flags=re.IGNORECASE,
            expand=False,
        )
        values = values.combine_first(pd.to_numeric(labelled, errors="coerce"))
    return values


def classify_generation(row: pd.Series) -> str:
    """Classify only explicit source labels; leave ambiguous assets unspecified."""
    text = " ".join(_clean_label(row.get(column), "") for column in ("Name", "PopupInfo", "FolderPath")).lower()
    if "gamesa" in text or "wind" in text:
        return "wind"
    if "hydro" in text or "ferney" in text:
        return "hydro"
    if "solar" in text or "sarako" in text or "landscope" in text:
        return "solar"
    if "substation" in text or "sub-station" in text or "sub station" in text:
        return "substation"
    thermal_tokens = (
        "power station",
        "power plant",
        "nicolay",
        "fort george",
        "saint louis",
    )
    if any(token in text for token in thermal_tokens):
        return "thermal"
    return "unspecified"


def _find_cell(frame: pd.DataFrame, pattern: str) -> tuple[int, int]:
    compiled = re.compile(pattern, flags=re.IGNORECASE)
    for row_i, row in frame.iterrows():
        for col_i, value in row.items():
            if isinstance(value, str) and compiled.search(value):
                return int(row_i), int(col_i)
    raise ValueError(f"Could not find workbook label matching {pattern!r}")


def extract_demand_workbook(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract monthly system peaks and annual customer-sector demand."""
    raw = pd.read_excel(path, sheet_name=0, header=None)

    year_row, year_col = _find_cell(raw, r"^\s*Year\s*$")
    header = raw.iloc[year_row]
    month_cols = [int(header[header.eq(month)].index[0]) for month in MONTHS]
    peak_rows: list[dict[str, object]] = []
    for row_i in range(year_row + 1, len(raw)):
        year = raw.iat[row_i, year_col]
        if pd.isna(year):
            if peak_rows:
                break
            continue
        if not isinstance(year, (int, float, np.integer, np.floating)):
            break
        values = [raw.iat[row_i, column] for column in month_cols]
        peak_rows.append({"year": int(year), **dict(zip(MONTHS, values, strict=True))})
    monthly_peak = pd.DataFrame(peak_rows).set_index("year").apply(pd.to_numeric, errors="coerce")

    unit_row, _ = _find_cell(raw, r"Unit\s*:\s*GWh")
    annual_year_row = unit_row + 1
    annual_year_cols = [
        int(column)
        for column, value in raw.iloc[annual_year_row].items()
        if pd.notna(value) and isinstance(value, (int, float, np.integer, np.floating))
    ]
    years = [int(raw.iat[annual_year_row, column]) for column in annual_year_cols]
    label_col = min(annual_year_cols) - 1
    annual_rows: list[dict[str, object]] = []
    for row_i in range(annual_year_row + 1, len(raw)):
        label = raw.iat[row_i, label_col]
        if pd.isna(label):
            continue
        label = _clean_label(str(label).replace("\n", " "))
        label = label.replace("Electricity demand - ", "").replace("Electricity demand ", "")
        for year, column in zip(years, annual_year_cols, strict=True):
            annual_rows.append({"year": year, "category": label, "demand_gwh": raw.iat[row_i, column]})
    annual = pd.DataFrame(annual_rows)
    annual["demand_gwh"] = pd.to_numeric(annual["demand_gwh"], errors="coerce")
    return monthly_peak, annual


def _station_points_from_areas(areas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    named = areas[areas["is_named"] & ~areas["category"].eq("substation")].copy()
    if named.empty:
        return gpd.GeoDataFrame(columns=["asset_id", "name", "asset_type", "geometry"], crs=GEOGRAPHIC_CRS)
    named["geometry"] = named.geometry.representative_point()
    return named.rename(columns={"label": "name", "category": "asset_type"})[
        ["asset_id", "name", "asset_type", "geometry"]
    ]


def snap_substations_to_routes(
    substations: gpd.GeoDataFrame,
    routes: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Align every substation with the nearest mapped transmission route.

    There is deliberately no distance cutoff because the source layers are
    coarse. Original coordinates and movement distances are retained so large
    adjustments remain visible and can be replaced when better data arrive.
    """
    required_substation_columns = {"bus_id", "geometry"}
    missing_substation_columns = required_substation_columns - set(substations.columns)
    if missing_substation_columns:
        raise ValueError(f"Substations missing columns: {sorted(missing_substation_columns)}")
    required_route_columns = {"route_id", "geometry"}
    missing_route_columns = required_route_columns - set(routes.columns)
    if missing_route_columns:
        raise ValueError(f"Routes missing columns: {sorted(missing_route_columns)}")
    if routes.empty:
        raise ValueError("Cannot snap substations because the route layer is empty")

    metric_substations = substations.to_crs(METRIC_CRS).copy()
    route_parts = routes.to_crs(METRIC_CRS).explode(index_parts=True).reset_index(drop=True)
    route_parts = route_parts[route_parts.geometry.geom_type.eq("LineString")].copy()
    if route_parts.empty:
        raise ValueError("Cannot snap substations because the route layer has no lines")
    route_parts["route_part_id"] = [
        f"{route_id}_PART_{part_number:03d}"
        for route_id, part_number in zip(
            route_parts["route_id"],
            route_parts.groupby("route_id").cumcount() + 1,
            strict=True,
        )
    ]

    rows: list[dict[str, object]] = []
    for _, substation in metric_substations.iterrows():
        # Work in metres so the nearest route and audit distance are meaningful.
        distances = route_parts.geometry.distance(substation.geometry)
        nearest_index = distances.idxmin()
        route = route_parts.loc[nearest_index]
        snapped_point = nearest_points(substation.geometry, route.geometry)[1]
        rows.append(
            {
                **substation.drop(labels="geometry").to_dict(),
                "snap_distance_m": float(distances.loc[nearest_index]),
                "snapped_route_id": str(route["route_id"]),
                "snapped_route_name": str(route.get("name", "unnamed")),
                "snapped_route_part_id": str(route["route_part_id"]),
                "geometry": snapped_point,
            }
        )

    snapped = gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC_CRS)
    original_points = metric_substations.set_index("bus_id").geometry
    original_geographic = original_points.to_crs(GEOGRAPHIC_CRS)
    snapped["original_lon"] = snapped["bus_id"].map(original_geographic.x)
    snapped["original_lat"] = snapped["bus_id"].map(original_geographic.y)
    snapped = snapped.to_crs(GEOGRAPHIC_CRS)
    snapped["snapped_lon"] = snapped.geometry.x
    snapped["snapped_lat"] = snapped.geometry.y
    return snapped


def assign_generation_to_substations(
    generation_sites: gpd.GeoDataFrame,
    substations: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Assign each mapped generation site to its nearest snapped substation."""
    if "generator_id" not in generation_sites or "geometry" not in generation_sites:
        raise ValueError("generation_sites must contain generator_id and geometry")
    if "bus_id" not in substations or "geometry" not in substations:
        raise ValueError("substations must contain bus_id and geometry")
    if substations.empty:
        raise ValueError("Cannot assign generation without substations")

    generators = generation_sites.to_crs(METRIC_CRS).copy()
    buses = substations.to_crs(METRIC_CRS)
    bus_ids = []
    distances_m = []
    for point in generators.geometry:
        distances = buses.geometry.distance(point)
        nearest_index = distances.idxmin()
        bus_ids.append(str(buses.loc[nearest_index, "bus_id"]))
        distances_m.append(float(distances.loc[nearest_index]))
    generators["bus_id"] = bus_ids
    generators["bus_assignment_distance_m"] = distances_m
    return generators.to_crs(GEOGRAPHIC_CRS)


def apply_generator_capacity_reference(
    generation_sites: gpd.GeoDataFrame,
    reference_path: Path = GENERATOR_CAPACITY_REFERENCE,
) -> gpd.GeoDataFrame:
    """Add report-backed installed capacity and a neutral VoLL dispatch cost."""
    reference = pd.read_csv(reference_path)
    _required = {
        "name",
        "output_capacity_mw",
        "effective_capacity_mw",
        "capacity_measure",
        "report_period",
        "capacity_source",
    }
    missing = _required - set(reference.columns)
    if missing:
        raise ValueError(f"generator capacity reference missing columns: {sorted(missing)}")

    result = generation_sites.merge(
        reference,
        on="name",
        how="left",
        validate="many_to_one",
    )
    duplicate_count = result.groupby("name")["generator_id"].transform("count")
    for column in ("output_capacity_mw", "effective_capacity_mw"):
        result[column] = result[column] / duplicate_count
    has_capacity = result["output_capacity_mw"].notna()
    result["marginal_cost"] = np.where(has_capacity, 0.0, np.nan)
    result["marginal_cost_basis"] = np.where(
        has_capacity,
        "equal_dispatch_proxy_for_voll",
        pd.NA,
    )
    result["capacity_source_url"] = np.where(
        has_capacity,
        CEB_ANNUAL_REPORT_URL,
        pd.NA,
    )
    return gpd.GeoDataFrame(result, geometry="geometry", crs=generation_sites.crs)


def prepare_provided_data(input_dir: Path, output_dir: Path) -> PreparedAssets:
    """Prepare source shapefiles and the CEB workbook for modelling."""
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
    templates = write_input_templates(output_dir.parent / "templates")

    return PreparedAssets(
        substations=substation_path,
        snapped_substations=snapped_substation_path,
        substation_snap_distances=snap_distance_path,
        transmission_routes=route_path,
        generation_points=point_path,
        generation_areas=area_path,
        generators=generators_path,
        service_weights=service_weights_path,
        generator_input_template=templates.generators,
        line_input_template=templates.lines,
        monthly_peak_demand=peak_path,
        annual_sector_demand=annual_path,
    )
