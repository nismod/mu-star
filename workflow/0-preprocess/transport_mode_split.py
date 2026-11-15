"""Split trips by mode and purpose, and turn them into assignment demand.

Purposes:
    work               journeys to work (radiation OD between cells)
    school-<level>     journeys to school, per level (primary, secondary), if
                       a school OD is given (transport_school_od)

Mode shares (walk, cycle, car, bus, metro) for journeys to work, by
mode_split.method (per island; see config):

    "ridership"  bus and metro from reported daily boardings:
                 commuters = boardings x commute_fraction / boardings_per_journey
                             / journeys_per_commuter
                 share = commuters / between-cell commuters on the island;
                 walk = walk_share, cycle = 0, car takes the rest (Mauritius).
    "shares"     mode_split.shares for every OD pair.
    "bands"      mode_split.bands by road distance (e.g. Rodrigues: much
                 walking and cycling on short trips).

School trips use school.mode_split per level.

Metro applies only where both ends' metro centroids reach a station on foot;
elsewhere the metro share goes to mode_split.metro_fallback. Bus applies only
where both ends reach a stop; elsewhere the bus share goes to car. Walk and
cycle trips are counted in the summary but not assigned. Daily trips are
scaled to the modelled peak period (period.peak_period_share for work,
school.peak_period_share for school).

Output (transport_flow_model Demand plus labels), one row per node pair,
mode and purpose:
    origin_id, destination_id (zone/<id>/<mode>), value (persons in period),
    mode (car|bus|metro), purpose, origin_zone, destination_zone
"""

import json

import click
import numpy as np
import pandas as pd

from transport.land import read_config, zone_node

MODES = ("walk", "cycle", "car", "bus", "metro")
ASSIGNED = ("car", "bus", "metro")


def national_shares(split: dict, commuters: float) -> pd.Series:
    """Walk / cycle / car / bus / metro shares for "ridership" or "shares"."""
    if split["method"] == "shares":
        shares = pd.Series(split["shares"], dtype=float).reindex(list(MODES)).fillna(0.0)
    elif split["method"] == "ridership":
        r = split["ridership"]
        shares = pd.Series(0.0, index=list(MODES))
        for mode in ("bus", "metro"):
            journeys = r["boardings_per_day"][mode] * r["commute_fraction"][mode] / r["boardings_per_journey"][mode]
            shares[mode] = journeys / r["journeys_per_commuter"] / commuters
        shares["walk"] = split["walk_share"]
        shares["car"] = 1.0 - shares[["walk", "cycle", "bus", "metro"]].sum()
        if shares["car"] < 0:
            raise ValueError(f"Ridership-derived shares exceed 1: {shares.to_dict()}")
    else:
        raise ValueError(f"Not a national method: {split['method']!r}")
    if not np.isclose(shares.sum(), 1.0):
        raise ValueError("mode shares must sum to 1")
    return shares


def work_shares(split: dict, od: pd.DataFrame) -> pd.DataFrame:
    """Mode shares per OD row for journeys to work."""
    if split["method"] == "bands":
        bands = pd.DataFrame(split["bands"]).sort_values("max_km")
        shares = bands.reindex(columns=list(MODES)).fillna(0.0).to_numpy()
        if not np.allclose(shares.sum(axis=1), 1.0):
            raise ValueError("mode_split.bands shares must sum to 1 in each band")
        band = np.minimum(
            np.searchsorted(bands["max_km"].to_numpy(), od["length_km"].to_numpy(), side="left"), len(bands) - 1
        )
        return pd.DataFrame(shares[band], columns=list(MODES), index=od.index)
    national = national_shares(split, od["trips"].sum())
    return pd.DataFrame([national.to_numpy()] * len(od), columns=list(MODES), index=od.index)


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", default=None)
@click.option("--scenario", default=None)
@click.option("--od", "od_path", required=True)
@click.option("--school-od", "school_od_path", default="")
@click.option("--skim", required=True)
@click.option("--nodes", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, island, scenario, od_path, school_od_path, skim, nodes, output, output_summary):
    cfg = read_config(config_path, island=island, scenario=scenario)
    split = cfg["mode_split"]
    skim = pd.read_parquet(skim)
    node_table = pd.read_parquet(nodes, columns=["node_id", "kind", "zone_id", "has_access"])

    def reach(kind):
        return set(node_table[(node_table.kind == f"zone_{kind}") & node_table.has_access]["zone_id"])

    reach_metro, reach_bus = reach("metro"), reach("bus")

    def restrict(od, s):
        """Move metro / bus shares where the ends cannot reach a station / stop."""
        no_metro = ~(od.origin_zone.isin(reach_metro) & od.destination_zone.isin(reach_metro))
        s.loc[no_metro, split["metro_fallback"]] += s.loc[no_metro, "metro"]
        s.loc[no_metro, "metro"] = 0.0
        no_bus = ~(od.origin_zone.isin(reach_bus) & od.destination_zone.isin(reach_bus))
        s.loc[no_bus, "car"] += s.loc[no_bus, "bus"]
        s.loc[no_bus, "bus"] = 0.0
        return s, int(no_metro.sum()), int(no_bus.sum())

    parts = []  # (purpose, od with trips, shares, peak share)
    work = pd.read_parquet(od_path).merge(skim, on=["origin_zone", "destination_zone"], how="left")
    parts.append(("work", work, work_shares(split, work), cfg["period"]["peak_period_share"]))
    school_cfg = cfg.get("school") or {}
    if school_od_path:
        school = pd.read_parquet(school_od_path).merge(skim, on=["origin_zone", "destination_zone"], how="left")
        for level, group in school.groupby("level"):
            shares = pd.Series(school_cfg["mode_split"][level], dtype=float).reindex(list(MODES)).fillna(0.0)
            s = pd.DataFrame([shares.to_numpy()] * len(group), columns=list(MODES), index=group.index)
            parts.append((f"school-{level}", group, s, school_cfg["peak_period_share"]))

    rows, summary_by_purpose = [], {}
    for purpose, od, s, peak in parts:
        s, no_metro, no_bus = restrict(od, s)
        trips = od["trips"].to_numpy() * peak
        for mode in ASSIGNED:
            rows.append(
                pd.DataFrame(
                    {
                        "origin_id": [zone_node(z, mode) for z in od.origin_zone],
                        "destination_id": [zone_node(z, mode) for z in od.destination_zone],
                        "value": trips * s[mode].to_numpy(),
                        "mode": mode,
                        "purpose": purpose,
                        "origin_zone": od.origin_zone.to_numpy(),
                        "destination_zone": od.destination_zone.to_numpy(),
                    }
                )
            )
        summary_by_purpose[purpose] = {
            "daily_trips": float(od["trips"].sum()),
            "period_trips": float(trips.sum()),
            "period_trips_by_mode": {mode: float((trips * s[mode]).sum()) for mode in MODES},
            "mean_shares": s.mean().to_dict(),
            "od_pairs_without_metro_access": no_metro,
            "od_pairs_without_bus_access": no_bus,
        }
    demand = pd.concat(rows, ignore_index=True)
    demand = demand[demand["value"] > 0]
    demand.to_parquet(output, index=False)

    summary = {"island": island, "scenario": scenario, "method": split["method"], "by_purpose": summary_by_purpose}
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2, default=float)


if __name__ == "__main__":
    main()
