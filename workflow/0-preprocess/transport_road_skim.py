"""Free-flow road skim between zone car centroids.

Uses road and car_connector links only, directed. The shortest-path searches
are transport_flow_model's RadiationModel.impedance_matrix (igraph Dijkstra).
Time is the shortest free-flow time. Distance is the shortest distance, from a
separate search, so it is not always the length of the fastest route. That is
close enough for distance-banded mode shares.

Output (long, pairs reachable within radiation.max_impedance):
    origin_zone, destination_zone, time_min, length_km
"""

import click
import numpy as np
import pandas as pd
from transport_flow_model import Network, RadiationModel

from transport.land import read_config, zone_node


def skim(links, nodes, weight):
    network = Network(links[["edge_id", "edge_from", "edge_to", weight]].rename(columns={weight: "cost"}))
    return RadiationModel(network).impedance_matrix(nodes, directed=True)


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--links", required=True)
@click.option("--zones", required=True)
@click.option("--output", required=True)
def main(config_path, links, zones, output):
    cfg = read_config(config_path)["radiation"]
    links = pd.read_parquet(links, columns=["edge_id", "edge_from", "edge_to", "layer", "time_min", "length_km"])
    links = links[links.layer.isin(["road", "car_connector"])]
    linked = set(links.edge_from)
    zone_ids = pd.read_parquet(zones, columns=["zone_id"])["zone_id"]
    # Zones with no road node within connectors.car_max_km have no car centroid links.
    # TODO: report them.
    zone_ids = zone_ids[[zone_node(z, "car") in linked for z in zone_ids]].to_numpy()
    nodes = [zone_node(z, "car") for z in zone_ids]

    time = skim(links, nodes, "time_min")
    length = skim(links, nodes, "length_km")
    impedance = {"time_min": time, "length_km": length}[cfg["impedance"]]
    o, d = np.nonzero(np.isfinite(impedance) & (impedance <= cfg["max_impedance"]))
    keep = o != d
    o, d = o[keep], d[keep]
    pd.DataFrame(
        {"origin_zone": zone_ids[o], "destination_zone": zone_ids[d], "time_min": time[o, d], "length_km": length[o, d]}
    ).to_parquet(output, index=False)


if __name__ == "__main__":
    main()
