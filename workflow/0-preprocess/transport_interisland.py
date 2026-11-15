"""Inter-island air and sea flows and single-asset criticality (draft).

Inputs (config: transport_interisland.inputs):
    links.csv    link_id, mode (air|sea), from_node, to_node, operator,
                 services_per_week, passenger_capacity, freight_capacity_t
                 (per service, one direction)
    nodes.gpkg   node_id, name, island, kind (airport|port), geometry
    traffic.csv  link_id, from_node, to_node, year, passengers, freight_t (annual)

flows: one row per link and direction, with annual and daily traffic,
annual capacity and utilisation. Uses the latest year per link.

criticality: for each asset (a link, or a node, which takes all its links
with it), per island pair and quantity (passengers, freight_t):
    affected   annual traffic on the failed links
    shifted    the part that the other links between the same island pair
               (e.g. sea instead of air) can absorb in their spare capacity
    stranded   affected - shifted
TODO: freight cannot always switch to air (bulk, fuel); add a per-commodity
or per-mode switchability factor when the data allow.
"""

import click
import geopandas
import pandas as pd

QUANTITIES = (("passengers", "passenger_capacity"), ("freight_t", "freight_capacity_t"))


@click.group()
def cli():
    pass


@cli.command()
@click.option("--config", "config_path", required=True)
@click.option("--links", required=True)
@click.option("--nodes", required=True)
@click.option("--traffic", required=True)
@click.option("--output", required=True)
def flows(config_path, links, nodes, traffic, output):
    import yaml

    with open(config_path) as fh:
        days = yaml.safe_load(fh)["transport_interisland"]["days_per_year"]
    links = pd.read_csv(links, dtype={"link_id": str})
    nodes = geopandas.read_file(nodes)[["node_id", "island", "kind"]]
    traffic = pd.read_csv(traffic, dtype={"link_id": str})
    latest = traffic.sort_values("year").groupby(["link_id", "from_node", "to_node"], as_index=False).last()

    out = links.merge(latest, on=["link_id", "from_node", "to_node"], how="left")
    island = nodes.set_index("node_id")["island"]
    out["from_island"] = out["from_node"].map(island)
    out["to_island"] = out["to_node"].map(island)
    for quantity, capacity in QUANTITIES:
        out[quantity] = out[quantity].fillna(0.0)
        out[f"{quantity}_per_day"] = out[quantity] / days
        out[f"{capacity}_annual"] = out[capacity] * out["services_per_week"] * 52
        out[f"{quantity}_utilisation"] = out[quantity] / out[f"{capacity}_annual"]
    out.to_csv(output, index=False)


@cli.command()
@click.option("--flows", "flows_path", required=True)
@click.option("--output", required=True)
def criticality(flows_path, output):
    links = pd.read_csv(flows_path, dtype={"link_id": str})
    links["pair"] = [" - ".join(sorted(p)) for p in zip(links.from_island.astype(str), links.to_island.astype(str))]
    assets = [("link", lid, links.link_id == lid) for lid in links.link_id.unique()]
    for node in pd.unique(links[["from_node", "to_node"]].to_numpy().ravel()):
        assets.append(("node", node, (links.from_node == node) | (links.to_node == node)))

    rows = []
    for kind, asset, failed in assets:
        for pair, group in links.groupby("pair"):
            down, up = group[failed[group.index]], group[~failed[group.index]]
            if down.empty:
                continue
            row = {"asset_type": kind, "asset_id": asset, "island_pair": pair}
            for quantity, capacity in QUANTITIES:
                affected = down[quantity].sum()
                spare = (up[f"{capacity}_annual"] - up[quantity]).clip(lower=0).sum()
                shifted = min(affected, spare)
                row.update(
                    {
                        f"{quantity}_affected": affected,
                        f"{quantity}_shifted": shifted,
                        f"{quantity}_stranded": affected - shifted,
                    }
                )
            rows.append(row)
    pd.DataFrame(rows).to_csv(output, index=False)


if __name__ == "__main__":
    cli()
