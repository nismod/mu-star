"""Pre-process an OSM extract into CSV node and edge tables."""

from economy import (
    cppRosm_edges_path, 
    cppRosm_nodes_path, 
    cppRosm_roads_output_dir, 
    osm_extract_path,
)

rule osm_roads_preprocess:
    """
    Create nodes.csv and edges.csv from the Mauritius OSM buffer extract.

    The inline R program receives the OSM extract path as its first trailing
    argument and the output directory as its second.
    """
    input:
        osm_buffer_extract = osm_extract_path,
    output: 
        nodes_path = cppRosm_nodes_path,
        edges_path = cppRosm_edges_path,
    params:
        cpprosm_dir = cppRosm_roads_output_dir,
    script:
        "osm_roads_preprocess.R"
