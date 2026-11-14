"""Clean the provided road network.

Provided schema (layer "edges" of roads.gpkg, LineStrings, noded at junctions):
    edge_id     str, unique and stable (results are reported against it)
    from_node   str, node at the start of the geometry
    to_node     str, node at the end of the geometry
    road_class  motorway|trunk|primary|secondary|tertiary|unclassified|residential|service
    oneway      bool; true = traffic only from from_node to to_node
  optional:
    name, speed_kph, capacity_vph (per direction), lanes, length_m, bridge (bool)

Output edges: the above plus length_km, speed_kph, capacity_vph, time_min
(free flow), in crs_metric. Output nodes: node_id, geometry (from edge ends).
"""

import click
import geopandas
import pandas as pd
from shapely.geometry import Point

from transport.land import read_config, require_columns

REQUIRED = ["edge_id", "from_node", "to_node", "road_class", "oneway", "geometry"]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--roads", required=True)
@click.option("--output-edges", required=True)
@click.option("--output-nodes", required=True)
def main(config_path, roads, output_edges, output_nodes):
    cfg = read_config(config_path)
    defaults = pd.DataFrame(cfg["road"]["defaults"]).T

    edges = geopandas.read_file(roads, layer="edges").to_crs(cfg["crs_metric"])
    require_columns(edges, REQUIRED, "roads")
    for column in ("edge_id", "from_node", "to_node"):
        edges[column] = edges[column].astype(str)
    if edges["edge_id"].duplicated().any():
        raise ValueError("Duplicate road edge_id values")
    unknown = set(edges["road_class"]) - set(defaults.index)
    if unknown:
        raise ValueError(f"Road classes with no defaults in config: {sorted(unknown)}")
    edges = edges[edges["from_node"] != edges["to_node"]]  # drop self loops
    # TODO: drop edges not on the main island / not in the largest connected component.

    edges["length_km"] = edges.geometry.length / 1000.0
    for column in ("speed_kph", "capacity_vph"):
        default = edges["road_class"].map(defaults[column]).astype(float)
        edges[column] = pd.to_numeric(edges[column], errors="coerce").fillna(default) if column in edges else default
    edges["oneway"] = edges["oneway"].fillna(False).astype(bool)
    edges["time_min"] = edges["length_km"] / edges["speed_kph"] * 60.0
    edges.to_parquet(output_edges)

    starts = pd.DataFrame({"node_id": edges["from_node"], "geometry": [Point(g.coords[0]) for g in edges.geometry]})
    ends = pd.DataFrame({"node_id": edges["to_node"], "geometry": [Point(g.coords[-1]) for g in edges.geometry]})
    nodes = pd.concat([starts, ends]).drop_duplicates("node_id")
    geopandas.GeoDataFrame(nodes, crs=edges.crs).to_parquet(output_nodes)


if __name__ == "__main__":
    main()
