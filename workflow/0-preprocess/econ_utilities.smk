"""Pre-process economic data for model constraints."""

from economy import (
    cppRosm_edges_path,
    cppRosm_nodes_path,
    cppRosm_roads,
    econ_utilities_folder,
    main_roads,
    roads_path,
    voronoids_fig_path,
    wtp_path,
    wwtp_path,
)

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
        voronoids_fig_path = voronoids_fig_path,
        figures=directory(econ_utilities_folder),
    params:
        main_roads = main_roads,
    log:
        "logs/econ_utilities.log",
    script:
        "econ_utilities.py"
