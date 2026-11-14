"""Split commuting trips by mode and turn them into assignment demand.

Each OD pair takes the walk / car / bus / metro shares of its road-distance
band. Metro applies only where both ends' metro centroids reach a station on
foot (connectors.metro_walk_max_km). Otherwise the metro share goes to
mode_split.metro_fallback. Bus applies only where both ends reach a bus stop.
Otherwise the bus share goes to car. Walk trips are dropped from assignment
and counted in the summary. Daily trips are scaled to the modelled peak
period.

Output (transport_flow_model Demand plus labels), one row per node pair:
    origin_id, destination_id (network node ids zone/<id>/<mode>), value
    (persons in period), mode (car|bus|metro), origin_zone, destination_zone
"""

import json

import click
import numpy as np
import pandas as pd

from transport.land import read_config, zone_node

MODES = ("walk", "car", "bus", "metro")


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--od", "od_path", required=True)
@click.option("--skim", required=True)
@click.option("--nodes", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, od_path, skim, nodes, output, output_summary):
    cfg = read_config(config_path)
    split = cfg["mode_split"]
    period_share = cfg["period"]["peak_period_share"]

    od = pd.read_parquet(od_path).merge(pd.read_parquet(skim), on=["origin_zone", "destination_zone"], how="left")
    bands = pd.DataFrame(split["bands"]).sort_values("max_km")
    shares = bands[list(MODES)].to_numpy()
    if not np.allclose(shares.sum(axis=1), 1.0):
        raise ValueError("mode_split.bands shares must sum to 1 in each band")
    band = np.minimum(
        np.searchsorted(bands["max_km"].to_numpy(), od["length_km"].to_numpy(), side="left"), len(bands) - 1
    )
    s = pd.DataFrame(shares[band], columns=list(MODES), index=od.index)

    node_table = pd.read_parquet(nodes, columns=["node_id", "kind", "zone_id", "has_access"])

    def both_ends(kind):
        ok = node_table[(node_table.kind == f"zone_{kind}") & node_table.has_access]["zone_id"]
        return od.origin_zone.isin(ok) & od.destination_zone.isin(ok)

    no_metro = ~both_ends("metro")
    s.loc[no_metro, split["metro_fallback"]] += s.loc[no_metro, "metro"]
    s.loc[no_metro, "metro"] = 0.0
    no_bus = ~both_ends("bus")
    s.loc[no_bus, "car"] += s.loc[no_bus, "bus"]
    s.loc[no_bus, "bus"] = 0.0
    # TODO: zones with no car connector (no road within connectors.car_max_km) are not in the skim at all.

    trips = od["trips"].to_numpy() * period_share
    rows = [
        pd.DataFrame(
            {
                "origin_id": [zone_node(z, mode) for z in od.origin_zone],
                "destination_id": [zone_node(z, mode) for z in od.destination_zone],
                "value": trips * s[mode].to_numpy(),
                "mode": mode,
                "origin_zone": od.origin_zone,
                "destination_zone": od.destination_zone,
            }
        )
        for mode in ("car", "bus", "metro")
    ]
    demand = pd.concat(rows, ignore_index=True)
    demand = demand[demand["value"] > 0]
    demand.to_parquet(output, index=False)

    summary = {
        "daily_trips": float(od.trips.sum()),
        "period_trips": float(trips.sum()),
        "period_trips_by_mode": {mode: float((trips * s[mode]).sum()) for mode in MODES},
        "od_pairs_without_metro_access": int(no_metro.sum()),
        "od_pairs_without_bus_access": int(no_bus.sum()),
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
