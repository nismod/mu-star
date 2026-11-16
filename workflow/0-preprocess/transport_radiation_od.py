"""Journey-to-work trips between zones with the radiation model.

Trips are persons per day, one way (home to work). Uses transport_flow_model:
employed residents produce trips, and jobs are the destination mass and the
intervening opportunities.

Trips within a cell: with include_self, each cell also competes as its own
destination, at the mean travel time within the cell (0.83 x the H3 cell
circumradius at connectors.car_speed_kph; or that distance for length_km). The
model decides how many trips stay in the cell. They are not assigned to the
network, but count towards the district comparison (CA27 includes them).

Calibration (islands with district_od: true and radiation.calibration.enabled):
every combination of impedance, max_impedance, origin mass, include_self and
alpha (the scale parameter of the extended radiation model) is
scored against census Table CA27 aggregated to districts
(transport_flow_model.calibrate_radiation), and the best is used.

Validation: the chosen run is aggregated to districts and compared with CA27
(od_fit). With radiation.district_od = "constrain" it is then scaled to the
CA27 totals (scale_to_aggregate) as a fallback.

Outputs
    od: origin_zone, destination_zone, trips (between cells, trips > 0)
    summary (json): settings used, totals, district fit
    comparison (csv): district pairs, observed, modelled (and constrained)
    calibration (csv): one row per candidate setting with fit metrics, best first
"""

import json

import click
import h3
import numpy as np
import pandas as pd
from transport_flow_model import (
    aggregate_od,
    calibrate_radiation,
    od_fit,
    radiation_flows,
    scale_to_aggregate,
)

from transport.land import h3_resolution, read_config

# Mean distance between two random points in a regular hexagon, as a multiple
# of its circumradius (= edge length).
HEXAGON_MEAN_DISTANCE = 0.8255


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", required=True)
@click.option("--zoning", required=True)
@click.option("--zones", required=True)
@click.option("--skim", required=True)
@click.option("--district-od", default="", help="census CA27 district OD (empty: none)")
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
@click.option("--output-comparison", required=True)
@click.option("--output-calibration", required=True)
def main(
    config_path,
    island,
    zoning,
    zones,
    skim,
    district_od,
    output,
    output_summary,
    output_comparison,
    output_calibration,
):
    full_cfg = read_config(config_path, island=island)
    cfg = full_cfg["radiation"]
    zones = pd.read_parquet(zones, columns=["zone_id", "district", "employed_residents", "jobs"])
    skim = pd.read_parquet(skim)

    ids = pd.Index(zones["zone_id"])
    z = len(ids)
    o_idx, d_idx = ids.get_indexer(skim.origin_zone), ids.get_indexer(skim.destination_zone)
    within_km = HEXAGON_MEAN_DISTANCE * h3.average_hexagon_edge_length(h3_resolution(zoning), unit="km")
    within = {"length_km": within_km, "time_min": within_km / full_cfg["connectors"]["car_speed_kph"] * 60.0}
    impedances = {}
    for name in ("time_min", "length_km"):
        matrix = np.full((z, z), np.inf)
        matrix[o_idx, d_idx] = skim[name]
        np.fill_diagonal(matrix, within[name])
        impedances[name] = matrix

    workers = zones["employed_residents"].to_numpy(dtype=float)
    jobs = zones["jobs"].to_numpy(dtype=float)
    origin_masses = {"employed_residents": workers, "jobs": jobs}
    districts = zones["district"].astype(str).to_numpy()
    labels = np.array(sorted(pd.unique(districts)))

    observed = None
    if district_od:
        census = pd.read_parquet(district_od)
        census[["origin_district", "destination_district"]] = census[
            ["origin_district", "destination_district"]
        ].astype(str)
        observed = (
            census.pivot_table(
                index="origin_district",
                columns="destination_district",
                values="workers",
                aggfunc="sum",
                fill_value=0.0,
            )
            .reindex(index=labels, columns=labels)
            .fillna(0.0)
            .to_numpy()
        )

    # Settings: calibrated against CA27 where possible, else from config.
    settings = {
        "impedance": cfg["impedance"],
        "max_impedance": float(cfg["max_impedance"]),
        "origin_mass": cfg["origin_mass"],
        "include_self": bool(cfg["include_self"]),
        "alpha": cfg.get("alpha"),
        "source": "config",
    }
    calibration = pd.DataFrame()
    grid = cfg.get("calibration") or {}
    if observed is not None and grid.get("enabled"):
        results = [
            calibrate_radiation(
                {name: impedances[name]},
                workers,
                jobs,
                districts,
                observed,
                labels,
                origin_masses={m: origin_masses[m] for m in grid["origin_mass"]},
                max_impedances=grid["max_impedance"][name],
                include_self=grid["include_self"],
                alphas=grid.get("alpha") or [None],
                normalisation=cfg["normalisation"],
                metric=grid["metric"],
            )
            for name in grid["impedance"]
        ]
        calibration = (
            pd.concat(results, ignore_index=True)
            .sort_values(grid["metric"], ascending=False, kind="stable")
            .reset_index(drop=True)
        )
        best = calibration.iloc[0]
        settings = {
            "impedance": best["impedance"],
            "max_impedance": float(best["max_impedance"]),
            "origin_mass": best["origin_mass"],
            "include_self": bool(best["include_self"]),
            "alpha": None if pd.isna(best["alpha"]) else float(best["alpha"]),
            "source": f"calibrated ({grid['metric']} = {best[grid['metric']]:.4f})",
        }
    calibration.to_csv(output_calibration, index=False)

    # Full matrix: within-cell trips on the diagonal.
    full = radiation_flows(
        impedances[settings["impedance"]],
        workers,
        origin_masses[settings["origin_mass"]],
        jobs,
        max_impedance=settings["max_impedance"],
        normalisation=cfg["normalisation"],
        min_flow=cfg["min_trips"],
        include_self=settings["include_self"],
        alpha=settings["alpha"],
    )

    fit_before = fit_after = None
    comparison = pd.DataFrame()
    if observed is not None:
        modelled, _ = aggregate_od(full, districts, labels=labels)
        fit_before, comparison = od_fit(modelled, observed, labels)
        fit_before["cpc_shares"] = od_fit(modelled, observed * modelled.sum() / observed.sum(), labels)[0]["cpc"]
        if cfg["district_od"] == "constrain":
            full = scale_to_aggregate(
                full, districts, observed, labels, method=cfg["district_od_method"], production=workers
            )
            constrained, _ = aggregate_od(full, districts, labels=labels)
            fit_after, _ = od_fit(constrained, observed, labels)
            comparison["constrained"] = constrained.ravel()
        elif cfg["district_od"] != "validate":
            raise ValueError(f"radiation.district_od must be 'constrain' or 'validate', got {cfg['district_od']!r}")
    comparison.to_csv(output_comparison, index=False)

    within_cell = np.diag(full).copy()
    trips = full.copy()
    np.fill_diagonal(trips, 0.0)
    o, d = np.nonzero(trips)
    od = pd.DataFrame({"origin_zone": ids[o], "destination_zone": ids[d], "trips": trips[o, d]})
    od.to_parquet(output, index=False)

    with_skim = od.merge(skim, on=["origin_zone", "destination_zone"], how="left")
    summary = {
        "island": island,
        "zones": z,
        "settings": settings,
        "within_cell_impedance": within,
        "employed_residents": float(workers.sum()),
        "jobs": float(jobs.sum()),
        "within_cell_trips": float(within_cell.sum()),
        "between_cell_trips": float(od.trips.sum()),
        # Origins with no reachable job, or district pairs that could not be matched.
        "unplaced_trips": float(workers.sum() - within_cell.sum() - od.trips.sum()),
        "mean_time_min": float(np.average(with_skim.time_min, weights=with_skim.trips)) if len(od) else None,
        "mean_length_km": float(np.average(with_skim.length_km, weights=with_skim.trips)) if len(od) else None,
        "census_district_od_workers": float(observed.sum()) if observed is not None else None,
        "district_fit": fit_before,
        "district_fit_constrained": fit_after,
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2, default=float)


if __name__ == "__main__":
    main()
