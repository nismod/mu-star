"""Economic data variables for MU-STAR."""

######################################################################
# MODEL NICKNAME
######################################################################

model_freeze: str = "hylas"

######################################################################
# VARIABLES
######################################################################

######################################################################
# VARIOUS PATHS
######################################################################

######################################################################
# VARIOUS PATHS
######################################################################

cppRosm_roads_output_dir = "data/processed/cppRosm_network/"
cppRosm_nodes_path: str = "data/processed/cppRosm_network/nodes.csv"
cppRosm_edges_path: str = "data/processed/cppRosm_network/road_segments.csv"

CATALOGUE_ROOT: str = "data/processed/Socio-Economic/CATALOGUE"  # "../catalogue"
CATALOGUE_PROCESSED_ROOT: str = f"{CATALOGUE_ROOT}/processed"
OUTPUT_ROOT: str = "data/processed/Socio-Economic/BHM/models"
CUSTOM_BOUNDS_PATH: str = f"{CATALOGUE_PROCESSED_ROOT}/custom_bounds"

# local_catalogue_root: str = CATALOGUE_ROOT
# processed_local_catalogue_root: str = "data/processed/Socio-Economic/CATALOGUE"
osm_extract_path: str = "data/incoming/Infrastructure/Road Network/osm-extract/mauritius-260708.osm.pbf"

raw_local_econ_data_path: str = "data/incoming/Socio-economic/GDP/Digest_NA_Yr2024_180625.xlsx"


def local_econ_data_path(country_code: str, year: int) -> str:
    """Return the catalogue path for the configured country's accounts table."""
    country = country_code.lower()
    return f"{CATALOGUE_PROCESSED_ROOT}/{country}_gdp{year}/{country}.parquet"


processed_local_econ_data_path: str = local_econ_data_path("MUS", 2024)

wtp_path: str = "data/incoming/Infrastructure/Water Treatment Plant/wtp.gpkg"
wwtp_path: str = "data/incoming/Infrastructure/Wastewater Treatment Plant/wwtp.gpkg"
roads_path: str = "data/incoming/Infrastructure/Road Network/overture/roads.parquet"
cppRosm_roads: str = "data/processed/cppRosm_network/"
voronoids_fig_path: str = "figures/economic/utilities/voronoids.png"
econ_utilities_folder: str = "figures/economic/utilities"
substations_path:str = "data/processed/Socio-Economic/support_data/substations_named.parquet"

######################################################################
# OSM ROAD NETWORK
######################################################################

main_roads = [
    "trunk",
    # "primary",
    # 'secondary',
]

# wide: (57.0808, -20.6867, 58.0698, -19.8)
# tight: (57.294,-20.535,57.832,-19.966)
utility_plot_limits = (57.294,-20.535,57.832,-19.966)
utility_full_plots: bool = False
