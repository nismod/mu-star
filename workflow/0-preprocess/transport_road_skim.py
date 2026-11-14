"""Free-flow road skim between zone car centroids.

Uses road and car_connector links only. Time is the shortest free-flow time.
Distance is the shortest distance, computed as a separate shortest-path search,
so it is not always the length of the fastest route. That is close enough for
distance-banded mode shares. TODO: trace path lengths if it matters.

Output (long, only pairs within radiation.max_impedance):
    origin_zone, destination_zone, time_min, length_km
"""

import click
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from transport.land import read_config, zone_node


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--links", required=True)
@click.option("--zones", required=True)
@click.option("--output", required=True)
def main(config_path, links, zones, output):
    cfg = read_config(config_path)["radiation"]
    links = pd.read_parquet(links, columns=["edge_from", "edge_to", "layer", "time_min", "length_km"])
    links = links[links.layer.isin(["road", "car_connector"])]
    zone_ids = pd.read_parquet(zones, columns=["zone_id"])["zone_id"].to_numpy()

    nodes = pd.Index(pd.unique(np.r_[links.edge_from.to_numpy(), links.edge_to.to_numpy()]))
    a, b = nodes.get_indexer(links.edge_from), nodes.get_indexer(links.edge_to)
    n = len(nodes)
    zone_idx = nodes.get_indexer([zone_node(z, "car") for z in zone_ids])
    connected = zone_idx >= 0
    zone_ids, zone_idx = zone_ids[connected], zone_idx[connected]

    def matrix(weight, limit, chunk=200):
        # dijkstra returns sources x all-nodes; chunk sources to bound memory.
        graph = csr_matrix((links[weight].to_numpy() + 1e-9, (a, b)), shape=(n, n))
        return np.vstack(
            [
                dijkstra(graph, indices=zone_idx[i : i + chunk], limit=limit)[:, zone_idx]
                for i in range(0, len(zone_idx), chunk)
            ]
        )

    max_imp = cfg["max_impedance"]
    time = matrix("time_min", max_imp if cfg["impedance"] == "time_min" else np.inf)
    length = matrix("length_km", max_imp if cfg["impedance"] == "length_km" else np.inf)

    o, d = np.nonzero(np.isfinite(time if cfg["impedance"] == "time_min" else length))
    pd.DataFrame(
        {"origin_zone": zone_ids[o], "destination_zone": zone_ids[d], "time_min": time[o, d], "length_km": length[o, d]}
    ).to_parquet(output, index=False)


if __name__ == "__main__":
    main()
