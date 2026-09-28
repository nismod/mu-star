"""Convert provided source files into stable analysis-ready asset layers."""

from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import nearest_points

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
METRIC_CRS = "EPSG:32740"
GEOGRAPHIC_CRS = "EPSG:4326"
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
        "data/incoming/energy/provided (or under incoming/energy/provided of the "
        "data_root set in config/config.yaml)."
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
        if "final" in label.lower() or "total" in label.lower():
            # The workbook ends the sector block with a grand total; it is not a sector.
            continue
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


# --- Generators from the CEB annual report ---------------------------------------

CEB_PLANT_CAPACITIES = Path(__file__).parent / "resources" / "ceb_plant_capacities_2023_24.csv"
CEB_PLANT_SITES = Path(__file__).parent / "resources" / "ceb_plant_sites.csv"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def assemble_report_generators(
    provided_sites: pd.DataFrame,
    substations: gpd.GeoDataFrame,
    *,
    osm_power: gpd.GeoDataFrame | None = None,
    capacities_path: Path = CEB_PLANT_CAPACITIES,
    sites_path: Path = CEB_PLANT_SITES,
) -> pd.DataFrame:
    """Build the generator table from the CEB annual report's plant list.

    Every plant in ``ceb_plant_capacities_2023_24.csv`` becomes one generator
    with its installed and effective capacity. Its location comes from
    ``ceb_plant_sites.csv``: a named site in ``provided_sites`` (the cleaned
    provided generation sites, matched by exact name or by name prefix, in
    which case the centroid of the matching points is used), an OpenStreetMap
    plant in ``osm_power`` matched by name, or geocoded coordinates. Located
    plants are assigned to their nearest substation on the same island (the
    ``region`` column of ``substations``, "mauritius" when absent); an island
    with no substation (Rodrigues) leaves ``bus_id`` empty and the inferred
    network connects the plant at its own node. Distributed or unmatched
    plants keep an empty ``bus_id`` and are spread by demand share later.
    """
    capacities = pd.read_csv(capacities_path, comment="#")
    if "region" not in capacities:
        capacities["region"] = "mauritius"
    sites = pd.read_csv(sites_path, comment="#")
    missing = set(capacities["report_name"]) - set(sites["report_name"])
    if missing:
        raise ValueError(f"ceb_plant_sites.csv has no row for: {sorted(missing)}")
    table = capacities.merge(sites, on="report_name", how="left", validate="one_to_one")

    provided = provided_sites.copy()
    provided["name"] = provided["name"].astype(str)
    osm = None
    if osm_power is not None and "name" in osm_power.columns:
        osm = osm_power[osm_power["name"].notna()].to_crs(GEOGRAPHIC_CRS)

    lons, lats, notes = [], [], []
    for row in table.itertuples():
        kind = str(row.site_kind)
        lon = lat = float("nan")
        note = ""
        if kind == "provided":
            exact = provided[provided["name"].eq(str(row.site_name))]
            matches = exact if not exact.empty else provided[provided["name"].str.startswith(str(row.site_name))]
            if matches.empty:
                raise ValueError(f"{row.report_name}: no provided site named {row.site_name!r}")
            lon, lat = float(matches["lon"].mean()), float(matches["lat"].mean())
            note = f"provided site {row.site_name!r}"
            if len(matches) > 1:
                note += f" ({len(matches)} points, centroid)"
        elif kind == "osm":
            if osm is None:
                raise ValueError(f"{row.report_name}: OSM power features with names are needed to place it")
            matches = osm[osm["name"].astype(str).eq(str(row.site_name))]
            if matches.empty:
                raise ValueError(f"{row.report_name}: no OpenStreetMap plant named {row.site_name!r}")
            point = matches.geometry.union_all().centroid
            lon, lat = float(point.x), float(point.y)
            note = f"OpenStreetMap plant {row.site_name!r}"
        elif kind == "geocoded":
            lon, lat = float(row.lon), float(row.lat)
            note = str(row.location_basis)
        elif kind in {"distributed", "unmatched"}:
            note = "no single site; spread over substations by demand share"
        else:
            raise ValueError(f"{row.report_name}: unknown site_kind {kind!r}")
        lons.append(lon)
        lats.append(lat)
        notes.append(note)

    generator_ids = [f"{_slug(group)}-{_slug(name)}" for group, name in zip(table["group"], table["report_name"])]
    generators = pd.DataFrame(
        {
            "generator_id": generator_ids,
            "name": table["report_name"],
            "group": table["group"],
            "region": table["region"].astype(str),
            "carrier": table["technology"],
            "output_capacity_mw": table["installed_capacity_mw"].astype(float),
            "effective_capacity_mw": table["effective_capacity_mw"].astype(float),
            "capacity_basis": "electrical_output",
            "capacity_measure": "installed_capacity",
            "capacity_source": [f"CEB Annual Report 2023-2024 p. {p}" for p in table["report_page"]],
            "capacity_source_url": CEB_ANNUAL_REPORT_URL,
            "marginal_cost": 0.0,
            "marginal_cost_basis": "equal_dispatch_proxy_for_voll",
            "site_kind": table["site_kind"],
            "site_note": notes,
            "lon": lons,
            "lat": lats,
        }
    )
    located = generators["lon"].notna() & generators["lat"].notna()
    generators["bus_id"] = pd.NA
    generators["bus_assignment_distance_m"] = np.nan
    substation_regions = (
        substations["region"].astype(str)
        if "region" in substations
        else pd.Series("mauritius", index=substations.index)
    )
    for region_name in sorted(generators.loc[located, "region"].unique()):
        in_region = located & generators["region"].eq(region_name)
        candidates = substations[substation_regions.eq(region_name).to_numpy()]
        if candidates.empty:
            # No substation on this island (Rodrigues): the plant is the network's
            # connection point itself, and the inferred network attaches it at its own node.
            generators.loc[in_region, "site_note"] += f"; no substation on {region_name}, connected at its own node"
            continue
        sited = gpd.GeoDataFrame(
            generators.loc[in_region, ["generator_id"]],
            geometry=gpd.points_from_xy(generators.loc[in_region, "lon"], generators.loc[in_region, "lat"]),
            crs=GEOGRAPHIC_CRS,
        )
        assigned = assign_generation_to_substations(sited, candidates).set_index("generator_id")
        ids = generators.loc[in_region, "generator_id"]
        generators.loc[in_region, "bus_id"] = assigned.loc[ids, "bus_id"].to_numpy()
        generators.loc[in_region, "bus_assignment_distance_m"] = assigned.loc[
            ids, "bus_assignment_distance_m"
        ].to_numpy()
    return generators
