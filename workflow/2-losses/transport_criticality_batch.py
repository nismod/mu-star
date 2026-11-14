"""Evaluate one batch of single-asset failures against the baseline.

For each scenario, transport_flow_model.disrupt reroutes the baseline paths
that use a removed link (capacity constrained, on top of the unaffected
flows). Demand with no remaining path within capacity is isolated.

Outputs
    scenario-impacts.parquet  one row per scenario: affected_flow, rerouted_flow,
        isolated_flow, delta_time_min_total (person-minutes), delta_length_km_total
        (person-km), delta_cost_total, isolated_cost, each also split by mode (_car, _pt)
    od-impacts.parquet  one row per scenario and affected OD pair (see
        transport.costs.disruption_deltas), with scenario_id and mode
"""

import click
import numpy as np
import pandas as pd
import pyarrow as pa
from transport_flow_model import AssignmentResult, Network, Scenario, disrupt

from transport.costs import disruption_deltas, path_metrics
from transport.land import read_config

BIG_CAPACITY = 1e15
NETWORK_COLUMNS = ["edge_id", "edge_from", "edge_to", "cost", "capacity"]
TOTALS = [
    "affected_flow",
    "rerouted_flow",
    "isolated_flow",
    "delta_time_min_total",
    "delta_length_km_total",
    "delta_cost_total",
]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--batch", required=True)
@click.option("--links", "links_path", required=True)
@click.option("--paths", "paths_path", required=True)
@click.option("--link-flows", required=True)
@click.option("--failure-sets", required=True)
@click.option("--output-scenario-impacts", required=True)
@click.option("--output-od-impacts", required=True)
def main(
    config_path, batch, links_path, paths_path, link_flows, failure_sets, output_scenario_impacts, output_od_impacts
):
    cfg = read_config(config_path)
    isolated_cost = cfg["cost"]["isolated_trip_cost"]

    links = pd.read_parquet(links_path)
    table = links[NETWORK_COLUMNS].copy()
    table["capacity"] = table["capacity"].replace(np.inf, BIG_CAPACITY)
    network = Network(table)

    paths = pd.read_parquet(paths_path)
    flows = pd.read_parquet(link_flows, columns=["edge_id", "flow"])
    base = AssignmentResult.from_tables(
        pa.Table.from_pandas(flows, preserve_index=False), paths=pa.Table.from_pandas(paths, preserve_index=False)
    )
    base_metrics = path_metrics(paths, links)
    # Which baseline paths use each link, to find a scenario's affected flow.
    path_links = paths[["edge_path"]].explode("edge_path").reset_index()

    sets = pd.read_parquet(failure_sets)
    sets = sets[sets.batch == batch]
    scenarios = [
        Scenario.remove_links(row.scenario_id, list(row.link_ids), asset_type=row.asset_type, asset_id=row.asset_id)
        for row in sets.itertuples()
    ]
    results = disrupt(network, scenarios, base=base, capacity_constrained=True, directed=True, skip_unaffected=True)

    od_rows = []
    for result in results:
        removed = set(result.scenario.removed_links)
        affected_index = path_links.loc[path_links.edge_path.isin(removed), "index"].unique()
        rerouted = result.rerouted.to_pandas()
        deltas = disruption_deltas(
            base_metrics.loc[affected_index],
            path_metrics(rerouted, links) if len(rerouted) else base_metrics.iloc[0:0],
            result.isolated.to_pandas(),
        )
        deltas.insert(0, "scenario_id", result.scenario.id)
        od_rows.append(deltas)

    columns = ["scenario_id", "origin_id", "destination_id", *TOTALS]
    od = pd.concat(od_rows, ignore_index=True) if od_rows else pd.DataFrame(columns=columns)
    od["mode"] = od["origin_id"].str.rsplit("/", n=1).str[-1]
    od.to_parquet(output_od_impacts, index=False)

    total = od.groupby("scenario_id")[TOTALS].sum()
    by_mode = od.pivot_table(index="scenario_id", columns="mode", values=TOTALS, aggfunc="sum", fill_value=0.0)
    by_mode.columns = [f"{value}_{mode}" for value, mode in by_mode.columns]
    impacts = sets[["scenario_id"]].set_index("scenario_id").join(total).join(by_mode).fillna(0.0)
    impacts["isolated_cost"] = impacts["isolated_flow"] * isolated_cost if isolated_cost is not None else np.nan
    impacts["evaluated"] = ~impacts.index.isin([s.id for s in results.skipped])
    impacts.reset_index().to_parquet(output_scenario_impacts, index=False)


if __name__ == "__main__":
    main()
