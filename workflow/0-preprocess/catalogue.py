"""Subset the global data catalogue to Mauritius."""

import logging
from pathlib import Path

import scalenav.oop as snoo
from globdata.catalogue import load_catalogue
from ibis import _


def preprocess_local_catalogue(catalogue_root, output_root, country_code):
    """Write local subsets of catalogue layers that contain point coordinates."""

    # catalogue_root = str(catalogue_root)
    # if not catalogue_root.endswith("/"):
    #     catalogue_root = f"{catalogue_root}/"

    catalogue_ignore = [
        "kummu_2025_total_gdp_2015",
        "jrc_c_msz_2018_10",
        "skegs_c_msz_2018_10",
        "mus_gdp2021",
        "overture_roads",
        "gloria_cde_2015",
        "overture_places_landuses",
        "overture_places_landuses_fill",
        "copernicus_cropland_100m_2018",
        "kummu_gdp_ppp_30arcsec",
        "eurostat_nuts1",
        "eurostat_nuts0",
        "eurostat_nuts2",
        "eurostat_nuts3",
        "gadm_gid_2",
        "gadm_gid_0",
        "gadm_gid_1",
        "frsq_places",
        "aggdp_fish",
        "aggdp_livestock",
        "aggdp_crop",
        "aggdp_forest",
        "aggdp_total",
        "bea_county_sector",
        "tang_c_dvnl_2017",
        "tang_c_dvnl_2019",
        "tang_c_dvnl_2021",
        "tang_c_dvnl_2020",
        "tang_c_dvnl_2018",
        "tang_c_dvnl_2016",
        "tang_c_dvnl_2013",
        "tang_c_dvnl_2014",
        "tang_c_dvnl_2022",
        "tang_c_dvnl_2015",
        "tang_agg_dvnl_2015-2",
        "gva_thai_agri",
        "gva_thai_man",
        "gva_thai_serv",
        "dose_wdi_v6",
        "dose_wdi_v7",
        "sfi_paper_pulp",
        "sfi_iron_steel",
        "sfi_cement",
        "sfi_petrochem",
        "gem_chemicals",
        "gem_coal_terminals",
        "gem_power_tracker",
        "gem_iron_steel",
        "gem_coal_plants",
        "gem_coal_mines",
        "gem_cement",
        "glw_sheep_2015",
        "glw_pigs_2015",
        "glw_goat_2015",
        "glw_horse_2015",
        "glw_cattle_2015",
        "glw_duck_2015",
        "glw_chicken_2015",
        "glw_livestock",
    ]

    catalogue = load_catalogue(root=catalogue_root, ignore_=catalogue_ignore)

    conn = snoo.connect()

    region = conn.read_parquet(catalogue["custom_bounds"]).filter(_.gid_0.isin([country_code]))
    region_bound = region.geometry.unary_union().execute()  # .set_crs("epsg:4326")[0]

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    missed_layers = []

    for name, path in catalogue.items():
        print("Processing:", name)
        layer = conn.read_parquet(path)

        try:
            col_x, col_y = snoo.coords_columns(layer)
            output_folder = output_root / name
            output_folder.mkdir(parents=True, exist_ok=True)
            # output_path = output_folder / f"{name}.parquet"
            layer.filter(_[col_x].point(_[col_y]).intersects(region_bound)).to_parquet_dir(
                # str(output_path),
                # overwrite=True,
                str(output_folder),
                existing_data_behavior="overwrite_or_ignore",
            )
        except Exception as e:
            print(e)
            missed_layers.append(name)

    print("Missed layers:", missed_layers)

    return missed_layers


def run_from_snakemake(snakemake):
    """Pass workflow settings to the catalogue preprocessing function."""
    # log_path = Path(str(snakemake.log[0]))
    # log_path.parent.mkdir(parents=True, exist_ok=True)
    # logging.basicConfig(
    #     filename=log_path,
    #     level=logging.INFO,
    #     format="%(asctime)s %(levelname)s %(message)s",
    # )

    preprocess_local_catalogue(
        catalogue_root=snakemake.params.catalogue_root,
        output_root=snakemake.params.output_root,
        country_code=snakemake.params.country_code,
    )
    Path(str(snakemake.output.complete)).touch()


if __name__ == "__main__":
    run_from_snakemake(globals()["snakemake"])
