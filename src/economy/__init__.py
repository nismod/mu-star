"""Economic data variables for MU-STAR."""

cppRosm_roads_output_dir = "data/processed/cppRosm_network/"
cppRosm_nodes_path: str = "data/processed/cppRosm_network/nodes.csv"
cppRosm_edges_path: str = "data/processed/cppRosm_network/road_segments.csv"

local_catalogue_root: str = "../catalogue"
processed_local_catalogue_root: str = "data/processed/CATALOGUE"
osm_extract_path: str = "data/incoming/Infrastructure/Road Network/osm-extract/mauritius-260708.osm.pbf"

raw_local_econ_data_path: str = "data/incoming/Socio-economic/GDP/Digest_NA_Yr2024_180625.xlsx"

processed_local_econ_data_path: str = "data/processed/mus_gdp2024/mus.parquet"

wtp_path: str = "data/incoming/Infrastructure/Water Treatment Plant/wtp.gpkg"
wwtp_path: str = "data/incoming/Infrastructure/Wastewater Treatment Plant/wwtp.gpkg"
roads_path: str = "data/incoming/Infrastructure/Road Network/overture/roads.parquet"
cppRosm_roads: str = "data/processed/cppRosm_network/"
voronoids_fig_path: str = "catalogue/economic/utilities/voronoids.png"
econ_utilities_folder: str = "figures/economic/utilities"
