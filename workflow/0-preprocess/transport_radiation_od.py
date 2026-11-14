"""Journey-to-work trips between zones with the radiation model.

Trips are persons per day, one way (home to work). Uses
transport_flow_model.radiation_flows on the road skim: employed residents
produce trips, and jobs are the destination mass and the intervening
opportunities.

District constraint and validation (census Table CA27): the census counts
every worker, including those who work in their own cell, so the comparison
and the constraint use the full matrix, with each cell's intrazonal trips
(employed residents x radiation.intrazonal_share) on the diagonal.

1. The unconstrained matrix is aggregated to districts and compared with
   CA27 (transport_flow_model.od_fit). The comparison table and fit metrics
   are written out. "cpc_shares" compares after scaling CA27 to the modelled
   total, i.e. it measures the pattern only.
2. With radiation.district_od = "constrain", the full matrix is scaled to the
   CA27 totals (transport_flow_model.scale_to_aggregate). With "ipf", each
   cell's production is kept and the CA27 rows are rescaled to the modelled
   production per district, so differences in census year or coverage do not
   change the totals.
3. The diagonal (intrazonal trips, not assigned) is split off again.

Output: origin_zone, destination_zone, trips (pairs with trips > 0, off-diagonal).
Summary JSON: totals, mean trip time / distance, and district fit before
and after the constraint.
Comparison CSV: origin, destination (districts), observed, unconstrained,
constrained.
"""

import json

import click
import numpy as np
import pandas as pd
from transport_flow_model import aggregate_od, od_fit, radiation_flows, scale_to_aggregate

from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--zones", required=True)
@click.option("--skim", required=True)
@click.option("--output", required=True)
@click.option("--district-od", required=True)
@click.option("--output-summary", required=True)
@click.option("--output-comparison", required=True)
def main(config_path, zones, skim, district_od, output, output_summary, output_comparison):
    cfg = read_config(config_path)["radiation"]
    zones = pd.read_parquet(zones, columns=["zone_id", "district", "employed_residents", "jobs"])
    skim = pd.read_parquet(skim)

    ids = pd.Index(zones["zone_id"])
    z = len(ids)
    impedance = np.full((z, z), np.inf)
    impedance[ids.get_indexer(skim.origin_zone), ids.get_indexer(skim.destination_zone)] = skim[cfg["impedance"]]

    workers = zones["employed_residents"].to_numpy(dtype=float)
    jobs = zones["jobs"].to_numpy(dtype=float)
    production = workers * (1.0 - cfg["intrazonal_share"])
    origin_mass = {"employed_residents": workers, "jobs": jobs}[cfg["origin_mass"]]

    trips = radiation_flows(
        impedance,
        production,
        origin_mass,
        jobs,
        max_impedance=cfg["max_impedance"],
        normalisation=cfg["normalisation"],
        min_flow=cfg["min_trips"],
    )
    # Full matrix, intrazonal trips on the diagonal.
    full = trips.copy()
    np.fill_diagonal(full, workers * cfg["intrazonal_share"])

    # District targets, for the districts in the study area.
    districts = zones["district"].to_numpy()
    labels = np.array(sorted(pd.unique(districts)))
    census = pd.read_parquet(district_od)
    observed = (
        census.pivot_table(
            index="origin_district", columns="destination_district", values="workers", aggfunc="sum", fill_value=0.0
        )
        .reindex(index=labels, columns=labels)
        .fillna(0.0)
        .to_numpy()
    )
    unconstrained, _ = aggregate_od(full, districts, labels=labels)
    fit_before, comparison = od_fit(unconstrained, observed, labels)
    scaled_observed = observed * unconstrained.sum() / observed.sum()
    fit_before["cpc_shares"] = od_fit(unconstrained, scaled_observed, labels)[0]["cpc"]

    fit_after = None
    if cfg["district_od"] == "constrain":
        full = scale_to_aggregate(
            full, districts, observed, labels, method=cfg["district_od_method"], production=workers
        )
        constrained, _ = aggregate_od(full, districts, labels=labels)
        fit_after, _ = od_fit(constrained, observed, labels)
        fit_after["cpc_shares"] = od_fit(constrained, observed * constrained.sum() / observed.sum(), labels)[0]["cpc"]
        comparison["constrained"] = constrained.ravel()
    elif cfg["district_od"] != "validate":
        raise ValueError(f"radiation.district_od must be 'constrain' or 'validate', got {cfg['district_od']!r}")
    comparison = comparison.rename(columns={"modelled": "unconstrained"})
    comparison.to_csv(output_comparison, index=False)

    intrazonal = np.diag(full).copy()
    trips = full.copy()
    np.fill_diagonal(trips, 0.0)

    o, d = np.nonzero(trips)
    od = pd.DataFrame({"origin_zone": ids[o], "destination_zone": ids[d], "trips": trips[o, d]})
    od.to_parquet(output, index=False)

    with_skim = od.merge(skim, on=["origin_zone", "destination_zone"], how="left")
    summary = {
        "zones": z,
        "employed_residents": float(workers.sum()),
        "jobs": float(jobs.sum()),
        "intrazonal_trips": float(intrazonal.sum()),
        "network_trips": float(od.trips.sum()),
        # Origins with no reachable job, or district pairs that could not be matched.
        "unplaced_trips": float(workers.sum() - intrazonal.sum() - od.trips.sum()),
        "census_district_od_workers": float(observed.sum()),
        "district_fit_unconstrained": fit_before,
        "district_fit_constrained": fit_after,
        "od_pairs": len(od),
        "mean_time_min": float(np.average(with_skim.time_min, weights=with_skim.trips)),
        "mean_length_km": float(np.average(with_skim.length_km, weights=with_skim.trips)),
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
