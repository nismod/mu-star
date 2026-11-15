"""Evaluate one batch of single-asset failures against the baseline.

For each failure (scenario):

1. transport_flow_model.disrupt reroutes the baseline paths that use a
   removed link (capacity constrained, on top of the unaffected flows).
2. Bus links on a detour (failure unit link_changes) stay in use with new
   times and lengths: passengers on them keep their path, at the new
   time / distance / cost.
3. Trips left with no path on their own mode try the fallback modes in
   criticality.fallback (e.g. metro -> bus -> car), assigned on the disrupted
   network on top of the existing flows. Bus trips do not fall back: buses
   detour or skip stops instead. What is left is isolated (counted, not
   costed).
4. Journey time, distance, fare (exact stage fares where stage data exist)
   and generalised cost are compared with the baseline for each affected OD
   pair (transport.costs.disruption_deltas). Changes for trips that switch
   mode are reported raw (they can be negative); criticality.mode_shift_floor
   floors them at zero instead.
5. Each OD pair's changes are split by trip purpose (work, school-<level>)
   using the baseline purpose shares.

Outputs
    scenario-impacts.parquet  one row per scenario: affected_flow, rerouted_flow,
        isolated_flow, delta_time_min_total (person-minutes),
        delta_length_km_total (person-km), delta_fare_total, delta_cost_total,
        shifted_to_<mode>; also split by original mode (_car, _bus, _metro) and
        by purpose (_work, _school-primary, ...).
    od-impacts.parquet  one row per scenario, affected OD pair and purpose,
        with mode and the flags shifted (switched mode) for later flooring.
"""

import click
import numpy as np
import pandas as pd
import pyarrow as pa
from transport_flow_model import AssignmentResult, Network, Scenario, disrupt

from transport.costs import apply_link_changes, disruption_deltas, journey_metrics
from transport.fallback import mode_fallback
from transport.fares import path_fares
from transport.land import read_config

BIG_CAPACITY = 1e15
NETWORK_COLUMNS = ["edge_id", "edge_from", "edge_to", "cost", "capacity"]
TOTALS = [
    "affected_flow",
    "rerouted_flow",
    "isolated_flow",
    "delta_time_min_total",
    "delta_length_km_total",
    "delta_fare_total",
    "delta_cost_total",
]
KEYS = ["origin_id", "destination_id"]


def empty_paths():
    return pd.DataFrame(columns=["origin_id", "destination_id", "flow", "edge_path", "cost"])


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", default=None)
@click.option("--scenario", default=None)
@click.option("--batch", required=True)
@click.option("--links", "links_path", required=True)
@click.option("--paths", "paths_path", required=True)
@click.option("--link-flows", required=True)
@click.option("--purpose-shares", required=True)
@click.option("--failure-sets", required=True)
@click.option("--metro-fare-stages", default="")
@click.option("--output-scenario-impacts", required=True)
@click.option("--output-od-impacts", required=True)
def main(
    config_path,
    island,
    scenario,
    batch,
    links_path,
    paths_path,
    link_flows,
    purpose_shares,
    failure_sets,
    metro_fare_stages,
    output_scenario_impacts,
    output_od_impacts,
):
    cfg = read_config(config_path, island=island, scenario=scenario)
    fallbacks = cfg["criticality"].get("fallback") or {}
    floor_shift = bool(cfg["criticality"].get("mode_shift_floor", False))
    stage_cfg = cfg["cost"].get("stage_fares") or {}
    metro_stages = pd.read_csv(metro_fare_stages, dtype=str) if metro_fare_stages else None

    def fare_fn(p, edge_table):
        return path_fares(p, edge_table, stage_cfg.get("bus"), stage_cfg.get("metro"), metro_stages)

    links = pd.read_parquet(links_path)
    table = links[NETWORK_COLUMNS].copy()
    table["capacity"] = table["capacity"].replace(np.inf, BIG_CAPACITY)
    network = Network(table)

    paths = pd.read_parquet(paths_path)
    flows = pd.read_parquet(link_flows, columns=["edge_id", "flow"])
    base = AssignmentResult.from_tables(
        pa.Table.from_pandas(flows, preserve_index=False), paths=pa.Table.from_pandas(paths, preserve_index=False)
    )
    base_metrics = journey_metrics(paths, links, fare_fn)
    # Which baseline paths use each link, to find a scenario's affected flow.
    path_links = paths[["edge_path"]].explode("edge_path").reset_index()

    sets = pd.read_parquet(failure_sets)
    sets = sets[sets.batch == batch]
    scenarios = [
        Scenario.remove_links(row.scenario_id, list(row.link_ids), asset_type=row.asset_type, asset_id=row.asset_id)
        for row in sets.itertuples()
    ]
    results = disrupt(network, scenarios, base=base, capacity_constrained=True, directed=True, skip_unaffected=True)
    by_id = {r.scenario.id: r for r in results}

    od_rows = []
    evaluated = []
    for row in sets.itertuples():
        result = by_id.get(row.scenario_id)
        removed = set(row.link_ids)
        changes = list(row.link_changes) if row.link_changes is not None else []
        changed = {c["edge_id"] for c in changes}
        if result is None and not changed:
            continue  # no baseline flow on anything this failure touches
        evaluated.append(row.scenario_id)
        scenario_links = apply_link_changes(links, changes, cfg["cost"])

        uses_removed = path_links.loc[path_links.edge_path.isin(removed), "index"].unique()
        uses_changed = path_links.loc[path_links.edge_path.isin(changed), "index"].unique()
        affected_index = np.union1d(uses_removed, uses_changed)
        if len(affected_index) == 0:
            continue

        rerouted = result.rerouted.to_pandas() if result is not None else empty_paths()
        isolated = result.isolated.to_pandas() if result is not None else pd.DataFrame(columns=[*KEYS, "value"])
        current = result.link_flows.to_pandas() if result is not None else flows
        fallback_paths, isolated = mode_fallback(links, current, removed, isolated, fallbacks)
        # Paths that only use detoured links keep their route at the new times.
        kept = paths.loc[np.setdiff1d(uses_changed, uses_removed)]
        new_paths = pd.concat([rerouted, fallback_paths.drop(columns="fallback_mode"), kept], ignore_index=True)

        deltas = disruption_deltas(
            base_metrics.loc[affected_index],
            journey_metrics(new_paths, scenario_links, fare_fn) if len(new_paths) else base_metrics.iloc[0:0],
            isolated,
        )
        shifted = fallback_paths.pivot_table(index=KEYS, columns="fallback_mode", values="flow", aggfunc="sum")
        shifted.columns = [f"shifted_to_{mode}" for mode in shifted.columns]
        deltas = deltas.merge(shifted.reset_index(), on=KEYS, how="left")
        shift_cols = [c for c in deltas.columns if c.startswith("shifted_to_")]
        deltas["shifted"] = deltas[shift_cols].fillna(0.0).sum(axis=1) > 0 if shift_cols else False
        if floor_shift:
            delta_columns = [c for c in deltas.columns if c.startswith("delta_")]
            deltas.loc[deltas.shifted, delta_columns] = deltas.loc[deltas.shifted, delta_columns].clip(lower=0.0)
        deltas.insert(0, "scenario_id", row.scenario_id)
        od_rows.append(deltas)

    od = pd.concat(od_rows, ignore_index=True) if od_rows else pd.DataFrame(columns=["scenario_id", *KEYS, *TOTALS])
    for column in TOTALS:
        if column not in od:
            od[column] = 0.0
    od["mode"] = od["origin_id"].str.rsplit("/", n=1).str[-1]

    # Split each OD pair by purpose (work, school-<level>) with the baseline shares.
    shares = pd.read_parquet(purpose_shares)
    od = od.merge(shares, on=KEYS, how="left")
    od["purpose"] = od["purpose"].fillna("work")
    od["share"] = od["share"].fillna(1.0)
    shift_columns = sorted(c for c in od.columns if c.startswith("shifted_to_"))
    od[shift_columns] = od[shift_columns].fillna(0.0)
    value_columns = [*TOTALS, *shift_columns]
    od[value_columns] = od[value_columns].mul(od["share"], axis=0)
    od.to_parquet(output_od_impacts, index=False)

    total = od.groupby("scenario_id")[value_columns].sum()
    parts = [total]
    for split in ("mode", "purpose"):
        pivot = od.pivot_table(index="scenario_id", columns=split, values=TOTALS, aggfunc="sum", fill_value=0.0)
        pivot.columns = [f"{value}_{key}" for value, key in pivot.columns]
        parts.append(pivot)
    impacts = sets[["scenario_id"]].set_index("scenario_id")
    for part in parts:
        impacts = impacts.join(part)
    impacts = impacts.fillna(0.0)
    impacts["evaluated"] = impacts.index.isin(evaluated)
    impacts.reset_index().to_parquet(output_scenario_impacts, index=False)


if __name__ == "__main__":
    main()
