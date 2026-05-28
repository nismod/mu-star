
rule download_osm:
    output:
        osm = "{data}/incoming/osm/osm.pbf",

rule prepare_multimodal_network:
    input:
        osm = "{data}/incoming/osm/osm.pbf",
    output:
        edges = "{data}/processed/networks/transport/multi-modal-edges.geoparquet"),
        nodes = "{data}/processed/networks/transport/multi-modal-nodes.geoparquet"),
