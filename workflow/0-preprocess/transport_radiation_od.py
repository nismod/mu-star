"""Journey-to-work trips between zones with the radiation model.

Trips are persons per day, one way (home to work). See transport.radiation.

Output: origin_zone, destination_zone, trips (pairs with trips > 0).
Summary JSON: totals and mean trip time / distance, for sense checks.
"""

import json

import click
import numpy as np
import pandas as pd

from transport.land import read_config
from transport.radiation import prune_small_flows, radiation_trips


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--zones", required=True)
@click.option("--skim", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, zones, skim, output, output_summary):
    cfg = read_config(config_path)["radiation"]
    zones = pd.read_parquet(zones, columns=["zone_id", "employed_residents", "jobs"])
    skim = pd.read_parquet(skim)

    ids = pd.Index(zones["zone_id"])
    z = len(ids)
    impedance = np.full((z, z), np.inf)
    impedance[ids.get_indexer(skim.origin_zone), ids.get_indexer(skim.destination_zone)] = skim[cfg["impedance"]]

    workers = zones["employed_residents"].to_numpy(dtype=float)
    jobs = zones["jobs"].to_numpy(dtype=float)
    production = workers * (1.0 - cfg["intrazonal_share"])
    origin_mass = {"employed_residents": workers, "jobs": jobs}[cfg["origin_mass"]]

    trips = radiation_trips(
        impedance,
        production,
        origin_mass,
        jobs,
        max_impedance=cfg["max_impedance"],
        normalisation=cfg["normalisation"],
    )
    trips = prune_small_flows(trips, cfg["min_trips"])

    o, d = np.nonzero(trips)
    od = pd.DataFrame({"origin_zone": ids[o], "destination_zone": ids[d], "trips": trips[o, d]})
    od.to_parquet(output, index=False)

    with_skim = od.merge(skim, on=["origin_zone", "destination_zone"], how="left")
    summary = {
        "zones": z,
        "employed_residents": float(workers.sum()),
        "jobs": float(jobs.sum()),
        "intrazonal_trips": float((workers * cfg["intrazonal_share"]).sum()),
        "network_trips": float(od.trips.sum()),
        "unplaced_trips": float(production.sum() - od.trips.sum()),  # origins with no reachable job
        "od_pairs": len(od),
        "mean_time_min": float(np.average(with_skim.time_min, weights=with_skim.trips)),
        "mean_length_km": float(np.average(with_skim.length_km, weights=with_skim.trips)),
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
