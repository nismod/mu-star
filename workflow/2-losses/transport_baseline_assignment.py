"""Baseline assignment of peak-period demand (journeys to work and, if enabled,
to school) on the multimodal network.

Uses transport_flow_model sequential assignment, capacity constrained (bus and
metro links have finite capacity; roads are unconstrained unless
road.capacity_constrained is true).

Outputs
    link-flows.parquet    edge_id, layer, mode, asset_id, flow (persons in period),
                          capacity, utilisation
    paths.parquet         tfm path table: origin_id, destination_id, flow, edge_path, cost
                          (the base for disruption)
    od-journeys.parquet   origin_id, destination_id, mode, flow, time_min, length_km,
                          fare (exact stage fares where stage data exist), cost
                          (means per trip) and *_total (summed over flow)
    purpose-shares.parquet origin_id, destination_id, purpose, share: each node
                          pair's demand by purpose (purposes share paths)
    unassigned.parquet    origin_id, destination_id, mode, value
    summary.json          totals by mode
"""

import json

import click
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from transport_flow_model import AssignmentResult, Demand, Network, assign

from transport.costs import journey_metrics, od_metrics
from transport.fares import path_fares
from transport.land import read_config

# tfm needs finite numbers; this stands in for "unconstrained".
BIG_CAPACITY = 1e15
NETWORK_COLUMNS = ["edge_id", "edge_from", "edge_to", "cost", "capacity"]


def load_network(links: pd.DataFrame) -> Network:
    table = links[NETWORK_COLUMNS].copy()
    table["capacity"] = table["capacity"].replace(np.inf, BIG_CAPACITY)
    return Network(table)


def mode_of(node_ids: pd.Series) -> pd.Series:
    """zone/<id>/<mode> -> <mode> (car, bus or metro)."""
    return node_ids.str.rsplit("/", n=1).str[-1]


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--links", "links_path", required=True)
@click.option("--demand", "demand_path", required=True)
@click.option("--output-link-flows", required=True)
@click.option("--output-paths", required=True)
@click.option("--output-od-journeys", required=True)
@click.option("--output-unassigned", required=True)
@click.option("--output-summary", required=True)
@click.option("--output-purpose-shares", required=True)
@click.option("--island", default=None)
@click.option("--scenario", default=None)
@click.option("--metro-fare-stages", default="")
def main(
    config_path,
    links_path,
    demand_path,
    output_link_flows,
    output_paths,
    output_od_journeys,
    output_unassigned,
    output_summary,
    output_purpose_shares,
    island,
    scenario,
    metro_fare_stages,
):
    cfg = read_config(config_path, island=island, scenario=scenario)
    links = pd.read_parquet(links_path)
    network = load_network(links)
    demand = pd.read_parquet(demand_path)
    if "purpose" not in demand:
        demand["purpose"] = "work"
    od = demand.groupby(["origin_id", "destination_id"], as_index=False)["value"].sum()
    by_purpose = demand.groupby(["origin_id", "destination_id", "purpose"], as_index=False)["value"].sum()
    by_purpose["share"] = by_purpose["value"] / by_purpose.groupby(["origin_id", "destination_id"])["value"].transform(
        "sum"
    )
    by_purpose[["origin_id", "destination_id", "purpose", "share"]].to_parquet(output_purpose_shares, index=False)

    options = dict(capacity_constrained=True, directed=True)
    result = assign(network, Demand(od), "sequential", include_paths=True, **options)
    paths = result.paths.to_pandas()
    unassigned = result.unassigned.to_pandas()

    if cfg["mode_split"]["pt_overflow"] == "car":
        # Second pass: bus/metro trips that did not fit go by car, on top of the first-pass loads.
        # TODO: check that tfm treats the network "flow" column as occupied capacity here
        # (it does for capacity-constrained allocation, per its docs).
        is_pt = mode_of(unassigned.origin_id).isin(["bus", "metro"])
        overflow = unassigned[is_pt].copy()
        if len(overflow):
            for column in ("origin_id", "destination_id"):
                overflow[column] = overflow[column].str.replace(r"/(bus|metro)$", "/car", regex=True)
            loaded = links[NETWORK_COLUMNS].copy()
            loaded["capacity"] = loaded["capacity"].replace(np.inf, BIG_CAPACITY)
            loaded = loaded.merge(result.link_flows.to_pandas()[["edge_id", "flow"]], on="edge_id")
            second = assign(Network(loaded), Demand(overflow), "sequential", include_paths=True, **options)
            paths = pd.concat([paths, second.paths.to_pandas()], ignore_index=True)
            unassigned = pd.concat([unassigned[~is_pt], second.unassigned.to_pandas()], ignore_index=True)

    # Recompute link flows from all paths, so the second pass is included.
    edge_flow = paths[["edge_path", "flow"]].explode("edge_path").groupby("edge_path")["flow"].sum()
    link_flows = links[["edge_id", "layer", "mode", "asset_id", "capacity"]].copy()
    link_flows["flow"] = link_flows["edge_id"].map(edge_flow).fillna(0.0)
    link_flows["utilisation"] = link_flows["flow"] / link_flows["capacity"]
    link_flows.to_parquet(output_link_flows, index=False)

    # Saved in tfm's path schema so that disrupt() can take it as the base.
    base = AssignmentResult.from_tables(
        pa.Table.from_pandas(link_flows[["edge_id", "flow"]], preserve_index=False),
        paths=pa.Table.from_pandas(paths, preserve_index=False),
    )
    pq.write_table(base.paths, output_paths)

    stage_cfg = cfg["cost"].get("stage_fares") or {}
    metro_stages = pd.read_csv(metro_fare_stages, dtype=str) if metro_fare_stages else None

    def fare_fn(p, edge_table):
        return path_fares(p, edge_table, stage_cfg.get("bus"), stage_cfg.get("metro"), metro_stages)

    journeys = od_metrics(journey_metrics(paths, links, fare_fn), ("time_min", "length_km", "fare", "cost"))
    journeys["mode"] = mode_of(journeys["origin_id"])
    journeys.to_parquet(output_od_journeys, index=False)

    unassigned["mode"] = mode_of(unassigned["origin_id"])
    unassigned.to_parquet(output_unassigned, index=False)

    by_mode = journeys.groupby("mode")[["flow", "time_min_total", "length_km_total", "fare_total", "cost_total"]].sum()
    pt_links = link_flows[link_flows.layer.isin(["bus", "metro"])]
    summary = {
        "demand": float(od.value.sum()),
        "assigned": float(journeys.flow.sum()),
        "unassigned_by_mode": unassigned.groupby("mode")["value"].sum().to_dict(),
        "by_mode": by_mode.to_dict(orient="index"),
        "pt_links_at_capacity": int((pt_links.utilisation >= 0.999).sum()),
        "demand_by_purpose": demand.groupby("purpose")["value"].sum().to_dict(),
        "currency": cfg["cost"]["currency"],
    }
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2, default=float)


if __name__ == "__main__":
    main()
