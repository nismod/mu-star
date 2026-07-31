"""Pre-process economic data for model constraints."""

wtp_path : str = "data/incoming/Infrastructure/Water Treatment Plant/wtp.gpkg"
wwtp_path : str = "data/incoming/Infrastructure/Wastewater Treatment Plant/wwtp.gpkg"
roads_path : str = "data/incoming/Infrastructure/Road Network/overture/roads.parquet"
cppRosm_roads : str = "data/processed/cppRosm_network/"
cppRosm_nodes_path : str = "data/processed/cppRosm_network/nodes.csv"
cppRosm_edges_path : str = "data/processed/cppRosm_network/road_segments.csv"

voronoids_fig_path : str = f"../../catalogue/economic/utilities/voronoids.png"

rule econ_utilities:
    """Convert official national accounts into a geospatial economic constraint table."""
    input:
        wtp_path = wtp_path,
        wwtp_path = wwtp_path,
        roads_path = roads_path,
        cppRosm_roads = cppRosm_roads,
        nodes_path = cppRosm_nodes_path,
        edges_path = cppRosm_edges_path,
    output:
        # table = PROCESSED_LOCAL_ECON_DATA_PATH,
        # utilities_voronoids_fig = "../../figures/economic/utilities_voronois.png",
        voronoids_fig_path=voronoids_fig_path,
        figures=directory("../../figures/economic/utilities"),
    # params:
    #     catalogue_root = lambda wildcards: LOCAL_ECON_CATALOGUE_ROOT,
    #     country_code = config["local_econ"]["country_code"],
    #     year = config["local_econ"]["year"],
    #     rupees_per_usd = config["local_econ"]["rupees_per_usd"],
    log:
        "logs/econ_utilities.log",
    script:
        "econ_utilities.py"
