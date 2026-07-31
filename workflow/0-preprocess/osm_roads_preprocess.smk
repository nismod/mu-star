"""Pre-process an OSM extract into CSV node and edge tables."""

OSM_BUFFER_EXTRACT_PATH = config['paths']['osm_extract']
OSM_NODES_PATH = "data/processed/cppRosm_network/nodes.csv"
OSM_EDGES_PATH = "data/processed/cppRosm_network/road_segments.csv"
OSM_ROADS_OUTPUT_DIR = "data/processed/cppRosm_network/"

rule osm_roads_preprocess:
    """
    Create nodes.csv and edges.csv from the Mauritius OSM buffer extract.

    The inline R program receives the OSM extract path as its first trailing
    argument and the output directory as its second.
    """
    input:
        osm_buffer_extract = OSM_BUFFER_EXTRACT_PATH,
    output: 
        nodes_path = OSM_NODES_PATH,
        edges_path = OSM_EDGES_PATH,
    params:
        cpprosm_dir = OSM_ROADS_OUTPUT_DIR,
    script:
        "osm_roads_preprocess.R"
