"""Inter-island air and sea flows and single-asset criticality (draft).

Islands: Mauritius, Rodrigues, Agalega (census island codes 1-3) and St
Brandon (outer islands).

Inputs (config: transport_interisland):
    links.csv    link_id, mode (air|sea), from_node, to_node, operator,
                 services_per_week, passenger_capacity, freight_capacity_t
                 (per service, one direction), service_type
                 (scheduled|charter|irregular)
    nodes.gpkg   node_id, name, island, kind (airport|port), geometry
    traffic.csv  optional: link_id, from_node, to_node, year, passengers,
                 freight_t (annual), commodity, source

flows: one row per link and direction, with annual and daily passengers and
freight, annual capacity and utilisation. Observed traffic (latest year) is
used where there is any; otherwise passengers come from baseline_passengers
(two-way annual trips per island pair and mode; the midpoint of low-high,
split evenly over the pair's links of that mode and the two directions).
Unknown baselines stay empty (NaN), not zero.

criticality: for each asset (a link, or an airport / port, which takes all
its links with it), per direction of travel between islands:
    affected   annual traffic on the failed links
    shifted    the part that the other *scheduled* links in the same
               direction between the two islands (e.g. sea instead of air) can absorb in their spare
               capacity, if that quantity is switchable (passengers: yes;
               freight: no, it is limited to specific modes)
    stranded   affected - shifted
"""

import click
import geopandas
import numpy as np
import pandas as pd
import yaml

QUANTITIES = (("passengers", "passenger_capacity"), ("freight_t", "freight_capacity_t"))


def pair_key(a, b) -> str:
    return " - ".join(sorted([str(a), str(b)]))


@click.group()
def cli():
    pass


@cli.command()
@click.option("--config", "config_path", required=True)
@click.option("--links", required=True)
@click.option("--nodes", required=True)
@click.option("--traffic", default="")
@click.option("--output", required=True)
def flows(config_path, links, nodes, traffic, output):
    with open(config_path) as fh:
        cfg = yaml.safe_load(fh)["transport_interisland"]
    days = cfg["days_per_year"]
    links = pd.read_csv(links, dtype={"link_id": str})
    nodes = geopandas.read_file(nodes)[["node_id", "island", "kind"]]
    island = nodes.set_index("node_id")["island"]
    links["from_island"] = links["from_node"].map(island)
    links["to_island"] = links["to_node"].map(island)
    links["pair"] = [pair_key(a, b) for a, b in zip(links.from_island, links.to_island)]

    out = links.copy()
    for quantity, _ in QUANTITIES:
        out[quantity] = np.nan
    out["traffic_source"] = None
    if traffic:
        observed = pd.read_csv(traffic, dtype={"link_id": str})
        totals = (
            observed.sort_values("year")
            .groupby(["link_id", "from_node", "to_node", "year"], as_index=False)[["passengers", "freight_t"]]
            .sum(min_count=1)
            .groupby(["link_id", "from_node", "to_node"], as_index=False)
            .last()
        )
        out = out.drop(columns=["passengers", "freight_t"]).merge(
            totals, on=["link_id", "from_node", "to_node"], how="left"
        )
        out.loc[out["passengers"].notna() | out["freight_t"].notna(), "traffic_source"] = "observed"

    # Baseline passengers where nothing is observed.
    for row in cfg.get("baseline_passengers") or []:
        if row.get("low") is None or row.get("high") is None:
            continue
        mask = (out["pair"] == pair_key(*row["pair"])) & (out["mode"] == row["mode"]) & out["passengers"].isna()
        if mask.any():
            out.loc[mask, "passengers"] = (row["low"] + row["high"]) / 2 / mask.sum()
            out.loc[mask, "traffic_source"] = f"baseline ({row.get('note', '')})"

    for quantity, capacity in QUANTITIES:
        out[f"{quantity}_per_day"] = out[quantity] / days
        out[f"{capacity}_annual"] = out[capacity] * out["services_per_week"] * 52
        out[f"{quantity}_utilisation"] = out[quantity] / out[f"{capacity}_annual"]
    out.to_csv(output, index=False)


@cli.command()
@click.option("--config", "config_path", required=True)
@click.option("--flows", "flows_path", required=True)
@click.option("--output", required=True)
def criticality(config_path, flows_path, output):
    with open(config_path) as fh:
        switchable = yaml.safe_load(fh)["transport_interisland"]["switchable"]
    links = pd.read_csv(flows_path, dtype={"link_id": str})
    # Spare capacity only helps in the same direction of travel.
    links["direction"] = links["from_island"].astype(str) + " -> " + links["to_island"].astype(str)
    scheduled = links.get("service_type", pd.Series("scheduled", index=links.index)).fillna("scheduled") == "scheduled"
    assets = [("link", lid, links.link_id == lid) for lid in links.link_id.unique()]
    for node in pd.unique(links[["from_node", "to_node"]].to_numpy().ravel()):
        assets.append(("node", node, (links.from_node == node) | (links.to_node == node)))

    rows = []
    for kind, asset, failed in assets:
        for direction, group in links.groupby("direction"):
            down = group[failed[group.index]]
            up = group[~failed[group.index] & scheduled[group.index]]
            if down.empty:
                continue
            row = {
                "asset_type": kind,
                "asset_id": asset,
                "direction": direction,
                "modes_failed": ",".join(sorted(down["mode"].unique())),
            }
            for quantity, capacity in QUANTITIES:
                affected = down[quantity].sum(min_count=1)
                if pd.isna(affected):
                    row.update(
                        {f"{quantity}_affected": np.nan, f"{quantity}_shifted": np.nan, f"{quantity}_stranded": np.nan}
                    )
                    continue
                spare = (up[f"{capacity}_annual"] - up[quantity].fillna(0.0)).clip(lower=0).sum()
                shifted = min(affected, spare) if switchable.get(quantity.split("_")[0], False) else 0.0
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
