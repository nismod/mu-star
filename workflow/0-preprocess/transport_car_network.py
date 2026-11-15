"""Car base network: directed road links and car connectors, with no scenario settings.

The road skim (and so the OD estimation, which all scenarios share) runs on
this network. The multimodal network builds the same road and connector
links (transport.network) and adds costs and capacities per scenario.

Outputs
    links.parquet                   edge_id, edge_from, edge_to, layer (road |
                                    car_connector), mode, asset_id, length_km,
                                    time_min (free flow)
    zones-without-car-access.csv    zones with no road node within
                                    connectors.car_max_km (no car connector, so no car
                                    trips): zone_id, nearest_road_node, nearest_km,
                                    population, employed_residents, jobs
"""

import click
import geopandas
import pandas as pd

from transport.land import read_config
from transport.network import car_connectors, road_links, zones_without_car_access

COLUMNS = ["edge_id", "edge_from", "edge_to", "layer", "mode", "asset_id", "length_km", "time_min"]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", default=None)
@click.option("--road-edges", required=True)
@click.option("--road-nodes", required=True)
@click.option("--zones", required=True)
@click.option("--output-links", required=True)
@click.option("--output-no-access", required=True)
def main(config_path, island, road_edges, road_nodes, zones, output_links, output_no_access):
    cfg = read_config(config_path, island=island)
    conn = cfg["connectors"]
    roads = geopandas.read_parquet(road_edges)
    rnodes = geopandas.read_parquet(road_nodes)
    zones = geopandas.read_parquet(zones)

    links = pd.concat([road_links(roads), car_connectors(zones, rnodes, conn)], ignore_index=True)[COLUMNS]
    if links["edge_id"].duplicated().any():
        raise ValueError(f"Duplicate link ids, e.g. {links.loc[links.edge_id.duplicated(), 'edge_id'].head().tolist()}")
    links.to_parquet(output_links, index=False)

    missing = zones_without_car_access(zones, rnodes, conn)
    missing.to_csv(output_no_access, index=False)
    if len(missing):
        print(f"{len(missing)} of {len(zones)} zones have no road node within {conn['car_max_km']} km (no car trips)")


if __name__ == "__main__":
    main()
