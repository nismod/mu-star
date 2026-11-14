"""Split commuting trips by mode and turn them into assignment demand.

For each OD pair, take the walk / car / pt shares of its road-distance band.
Walk trips are dropped from assignment (counted in the summary). Daily trips
are scaled to the modelled peak period.

PT trips go between PT centroids (zone/<id>/pt). Whether they use bus, metro
or both is decided by the least-cost path within capacity.

Output (transport_flow_model Demand plus labels), one row per node pair:
    origin_id, destination_id (network node ids), value (persons in period),
    mode (car|pt), origin_zone, destination_zone
"""

import json

import click
import numpy as np
import pandas as pd

from transport.land import read_config, zone_node

MODES = ("walk", "car", "pt")


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--od", "od_path", required=True)
@click.option("--skim", required=True)
@click.option("--nodes", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, od_path, skim, nodes, output, output_summary):
    cfg = read_config(config_path)
    period_share = cfg["period"]["peak_period_share"]

    od = pd.read_parquet(od_path).merge(pd.read_parquet(skim), on=["origin_zone", "destination_zone"], how="left")
    bands = pd.DataFrame(cfg["mode_split"]["bands"]).sort_values("max_km")
    shares = bands[list(MODES)].to_numpy()
    if not np.allclose(shares.sum(axis=1), 1.0):
        raise ValueError("mode_split.bands shares must sum to 1 in each band")
    band = np.minimum(
        np.searchsorted(bands["max_km"].to_numpy(), od["length_km"].to_numpy(), side="left"), len(bands) - 1
    )
    s = shares[band]
    trips = od["trips"].to_numpy() * period_share

    # Every OD node pair must exist in the network.
    node_ids = set(pd.read_parquet(nodes, columns=["node_id"])["node_id"])

    rows = []
    for column, mode in ((1, "car"), (2, "pt")):
        rows.append(
            pd.DataFrame(
                {
                    "origin_id": [zone_node(z, mode) for z in od.origin_zone],
                    "destination_id": [zone_node(z, mode) for z in od.destination_zone],
                    "value": trips * s[:, column],
                    "mode": mode,
                    "origin_zone": od.origin_zone,
                    "destination_zone": od.destination_zone,
                }
            )
        )
    demand = pd.concat(rows, ignore_index=True)
    demand = demand[demand["value"] > 0]
    missing = (set(demand.origin_id) | set(demand.destination_id)) - node_ids
    if missing:
        raise ValueError(f"{len(missing)} demand nodes are not in the network, e.g. {sorted(missing)[:5]}")
    demand.to_parquet(output, index=False)

    summary = {
        "daily_trips": float(od.trips.sum()),
        "period_trips": float(trips.sum()),
        "period_trips_by_mode": {mode: float((trips * s[:, i]).sum()) for i, mode in enumerate(MODES)},
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
