"""Prepare the example data for the direct damages workflow.

Clips open data from the Infrastructure Climate Resilience Assessment Data
Starter Kit for Mauritius to a small area around Port Louis, and writes the
"processed" asset layers and hazard data cubes in the layout the workflow
expects. Also writes an example damage configuration workbook using damage
curves packaged with snail and illustrative (made up) costs.

The outputs are committed under ``example/``, so this only needs to be re-run
to change the example. Download and unzip the starter kit (MUS.zip) from
https://doi.org/10.5281/zenodo.23271097, then run from the repository root:

    python example/prepare_example_data.py --source-dir path/to/data/MUS --output-dir example
"""

import re
from pathlib import Path

import click
import geopandas as gpd
import pandas as pd
import rioxarray  # noqa: F401
import xarray as xr
from shapely.geometry import box
from snail import damage_library

from damages.geozarr import with_geozarr_attrs

# Port Louis and surroundings (lon/lat)
BBOX = (57.42, -20.22, 57.56, -20.10)
# Pad rasters so coarse grids fully cover the assets
RASTER_PAD = 0.15

ROAD_TYPES = {
    "trunk": "road_major",
    "trunk_link": "road_major",
    "primary": "road_major",
    "primary_link": "road_major",
    "secondary": "road_minor",
    "secondary_link": "road_minor",
    "tertiary": "road_minor",
    "tertiary_link": "road_minor",
}
POWER_TYPES = {
    "Oil": "power_plant_thermal",
    "Coal": "power_plant_thermal",
    "Biomass": "power_plant_thermal",
    "Solar": "power_plant_solar",
    "Hydro": "power_plant_hydro",
}

COASTAL_RCP = {"historical": "historical", "rcp4p5": "rcp4p5", "rcp8p5": "rcp8p5"}
COASTAL_EPOCH = {"hist": 2010, "2030": 2030, "2050": 2050, "2080": 2080}
STORM_GCMS = ["constant", "CMCC-CM2-VHR4", "CNRM-CM6-1-HR", "EC-Earth3P-HR", "HadGEM3-GC31-HM"]
STORM_RPS = [10, 20, 50, 100, 200, 500, 1000, 10000]


def prepare_roads(source: Path) -> gpd.GeoDataFrame:
    roads = gpd.read_file(source / "openstreetmap" / "openstreetmap_roads-tertiary__MUS.gpkg", layer="lines")
    roads = gpd.clip(roads, box(*BBOX)).explode(ignore_index=True)
    roads = roads[roads.geometry.geom_type == "LineString"]
    roads["asset_type"] = roads.highway.map(ROAD_TYPES)
    roads = roads.dropna(subset=["asset_type"])
    roads["asset_id"] = "road_" + roads.osm_id.astype(str) + "_" + roads.groupby("osm_id").cumcount().astype(str)
    columns = ["asset_id", "asset_type", "osm_id", "name", "highway", "surface", "bridge", "geometry"]
    return roads[columns].sort_values("asset_id").reset_index(drop=True)


def prepare_power(source: Path) -> gpd.GeoDataFrame:
    plants = gpd.read_file(source / "wri_powerplants" / "wri-powerplants__MUS.gpkg")
    plants = plants[plants.intersects(box(*BBOX))].copy()
    plants["asset_type"] = plants.primary_fuel.map(POWER_TYPES)
    plants["asset_id"] = plants.gppd_idnr
    columns = ["asset_id", "asset_type", "name", "primary_fuel", "capacity_mw", "geometry"]
    return plants[columns].sort_values("asset_id").reset_index(drop=True)


def _read_clip(path: Path) -> xr.DataArray:
    da = xr.open_dataarray(path, engine="rasterio").squeeze("band", drop=True)
    xmin, ymin, xmax, ymax = BBOX
    da = da.rio.clip_box(xmin - RASTER_PAD, ymin - RASTER_PAD, xmax + RASTER_PAD, ymax + RASTER_PAD)
    nodata = da.rio.nodata
    da = da.where(da != nodata) if nodata is not None else da
    return da.load()


def prepare_coastal(source: Path) -> xr.Dataset:
    pattern = re.compile(r"inuncoast_(\w+?)_wtsub_(\w+?)_rp(\d+)_0__MUS\.tif")
    arrays = []
    for path in sorted((source / "aqueduct_flood").glob("inuncoast_*.tif")):
        rcp, epoch, rp = pattern.match(path.name).groups()
        da = _read_clip(path).expand_dims(rcp=[COASTAL_RCP[rcp]], epoch=[COASTAL_EPOCH[epoch]], return_period=[int(rp)])
        arrays.append(da)
    da = xr.combine_by_coords(arrays, combine_attrs="drop_conflicts")
    if isinstance(da, xr.Dataset):
        da = da[next(iter(da.data_vars))]
    da = da.transpose("rcp", "epoch", "return_period", "y", "x").astype("float32")
    da = da.rename("inundation_depth")
    da.attrs = {"long_name": "Coastal flood inundation depth", "units": "m"}
    ds = da.to_dataset()
    ds["return_period"].attrs["units"] = "years"
    ds["epoch"].attrs["long_name"] = "Epoch (year); 2010 is the historical baseline"
    ds.attrs = {
        "title": "Coastal flood depth, WRI Aqueduct Floods (with subsidence), clipped to Port Louis",
        "source": "Ward et al. (2020) Aqueduct Floods Methodology, CC-BY-4.0, via https://doi.org/10.5281/zenodo.23271097",
    }
    return with_geozarr_attrs(ds.rio.write_crs("EPSG:4326"))


def prepare_wind(source: Path) -> xr.Dataset:
    arrays = []
    for gcm in STORM_GCMS:
        for rp in STORM_RPS:
            path = source / "storm" / f"STORM_FIXED_RETURN_PERIODS_{gcm}_{rp}_YR_RP__MUS.tif"
            arrays.append(_read_clip(path).expand_dims(gcm=[gcm], return_period=[rp]))
    da = xr.combine_by_coords(arrays, combine_attrs="drop_conflicts")
    if isinstance(da, xr.Dataset):
        da = da[next(iter(da.data_vars))]
    da = da.transpose("gcm", "return_period", "y", "x").astype("float32").rename("wind_speed")
    da.attrs = {"long_name": "Maximum 10-minute sustained wind speed", "units": "m/s"}
    ds = da.to_dataset()
    ds["return_period"].attrs["units"] = "years"
    ds["gcm"].attrs["long_name"] = "Climate model; 'constant' is present climate, others are 2015-2050 SSP5-8.5"
    ds.attrs = {
        "title": "Tropical cyclone wind speed return periods, STORM, clipped to Port Louis",
        "source": "Bloemendaal et al. (2022) STORM climate change tropical cyclone wind speed return periods, "
        "CC0, via https://doi.org/10.5281/zenodo.23271097",
    }
    return with_geozarr_attrs(ds.rio.write_crs("EPSG:4326"))


def damage_config_tables() -> dict[str, pd.DataFrame]:
    """Example damage configuration. Costs are illustrative, not estimates."""
    asset_types = pd.DataFrame(
        [
            ("road_major", "transport", "Trunk and primary roads"),
            ("road_minor", "transport", "Secondary and tertiary roads"),
            ("power_plant_thermal", "energy", "Oil, coal and biomass power plants"),
        ],
        columns=["asset_type", "sector", "description"],
    )
    hazard_metrics = pd.DataFrame(
        [("depth_m", "Flood depth in metres"), ("ws_10min", "10-minute average surface wind speed, metres per second")],
        columns=["intensity_metric", "description"],
    )
    hazards = pd.DataFrame(
        [
            ("coastal_aqueduct", "depth_m", "WRI Aqueduct Floods (2020) coastal flooding", "Coastal flood depth"),
            ("wind_storm", "ws_10min", "Bloemendaal et al. (2022) STORM", "Tropical cyclone 10-minute wind speed"),
        ],
        columns=["hazard", "intensity_metric", "source", "description"],
    )
    vulnerability = pd.DataFrame(
        [
            ("road_major", "depth_m", 1),
            ("road_major", "ws_10min", 0),
            ("road_minor", "depth_m", 1),
            ("road_minor", "ws_10min", 0),
            ("power_plant_thermal", "depth_m", 1),
            ("power_plant_thermal", "ws_10min", 1),
        ],
        columns=["asset_type", "hazard_intensity_metric", "vulnerable"],
    )
    costs = pd.DataFrame(
        [
            ("road_major", "mur_per_km", 30_000_000, 0.2, 0.3, "Illustrative value for testing only"),
            ("road_minor", "mur_per_km", 12_000_000, 0.2, 0.3, "Illustrative value for testing only"),
            ("power_plant_thermal", "mur_per_capacity_mw", 40_000_000, 0.1, 0.2, "Illustrative value for testing only"),
        ],
        columns=["asset_type", "cost_unit", "cost_mean", "cost_systemic_cv", "cost_asset_cv", "source"],
    )
    asset_curves = pd.DataFrame(
        [
            ("road_major", "depth_m", "nirandjan_F7.4", 0.5),
            ("road_major", "depth_m", "nirandjan_F7.5", 0.5),
            ("road_minor", "depth_m", "nirandjan_F7.8", 0.5),
            ("road_minor", "depth_m", "nirandjan_F7.9", 0.5),
            ("power_plant_thermal", "depth_m", "nirandjan_F1.4", 1.0),
            ("power_plant_thermal", "ws_10min", "nirandjan_W3.5", 0.5),
            ("power_plant_thermal", "ws_10min", "nirandjan_W3.7", 0.5),
        ],
        columns=["asset_type", "hazard_intensity_metric", "curve_id", "curve_weight"],
    )
    curve_rows, data_rows = [], []
    for curve_id in asset_curves.curve_id.unique():
        library_id = curve_id.removeprefix("nirandjan_")
        meta = damage_library.get_metadata(library_id)
        metric = "depth_m" if meta.hazard_type == "F" else "ws_10min"
        notes = f"{meta.exposed_element}; {meta.additional_characteristics or ''}; {meta.geographical_application}"
        source = f"Nirandjan et al. physical vulnerability database, curve {library_id}, CC-BY-4.0, via snail"
        curve_rows.append((curve_id, metric, source, notes))
        curve = damage_library.load_curve(library_id)
        for intensity, damage in zip(curve.intensity, curve.damage):
            data_rows.append((curve_id, float(intensity), float(damage)))
    curves = pd.DataFrame(curve_rows, columns=["curve_id", "hazard_intensity_metric", "source", "notes"])
    curve_data = pd.DataFrame(data_rows, columns=["curve_id", "hazard_intensity", "damage_fraction"])
    readme = pd.DataFrame(
        {
            "README": [
                "Example damage configuration for the mu-star direct damages workflow.",
                "Same structure as the project workbook. Costs are illustrative and must not be used for analysis.",
                "Damage curves are copied from the snail damage curve library (Nirandjan et al., CC-BY-4.0).",
                "Written by example/prepare_example_data.py",
            ]
        }
    )
    impairment = pd.DataFrame(columns=["curve_id", "hazard_intensity_metric", "source", "notes"])
    impairment_data = pd.DataFrame(columns=["curve_id", "hazard_intensity", "damage_fraction"])
    asset_impairment = pd.DataFrame(columns=["asset_type", "hazard_intensity_metric", "curve_id", "curve_weight"])
    return {
        "README": readme,
        "asset-type": asset_types,
        "hazard-metric": hazard_metrics,
        "hazard": hazards,
        "asset-type-hazard-vulnerability": vulnerability,
        "asset-type-cost": costs,
        "damage-curves": curves,
        "damage-curve-data": curve_data,
        "asset-type-damage-curve": asset_curves,
        "impairment-curves": impairment,
        "impairment-curve-data": impairment_data,
        "asset-type-impairment-curve": asset_impairment,
    }


@click.command()
@click.option("--source-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output-dir", default="example", type=click.Path(file_okay=False, path_type=Path))
def main(source_dir: Path, output_dir: Path):
    asset_dir = output_dir / "processed" / "asset"
    asset_dir.mkdir(parents=True, exist_ok=True)
    roads = prepare_roads(source_dir)
    roads.to_parquet(asset_dir / "layer-road.gpq")
    power = prepare_power(source_dir)
    power.to_parquet(asset_dir / "layer-power.gpq")
    click.echo(f"roads: {len(roads)} {roads.asset_type.value_counts().to_dict()}")
    click.echo(f"power: {len(power)} {power.asset_type.value_counts().to_dict()}")

    hazard_dir = output_dir / "processed" / "hazard" / "rp"
    for ds, path in [
        (prepare_coastal(source_dir), hazard_dir / "peril-flood" / "subperil-coastal.zarr"),
        (prepare_wind(source_dir), hazard_dir / "peril-cyclone" / "subperil-wind.zarr"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        ds.to_zarr(path, mode="w", consolidated=False)
        click.echo(f"{path}: {dict(ds.sizes)}")

    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(config_dir / "asset-type-cost-curves.xlsx") as writer:
        for sheet, table in damage_config_tables().items():
            table.to_excel(writer, sheet_name=sheet, index=False)


if __name__ == "__main__":
    main()
