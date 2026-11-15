"""Single-asset failure scenarios to evaluate, in batches.

Output: scenario_id (<asset_type>/<asset_id>), asset_type, asset_id, road_class,
link_ids (list of network links removed), baseline_flow (max flow over its
links), batch (batch-NNNN).
"""

import click
import numpy as np
import pandas as pd

from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--failure-units", required=True)
@click.option("--link-flows", required=True)
@click.option("--output", required=True)
def main(config_path, failure_units, link_flows, output):
    cfg = read_config(config_path)["criticality"]
    units = pd.read_parquet(failure_units)
    flows = pd.read_parquet(link_flows, columns=["edge_id", "flow"]).set_index("edge_id")["flow"]

    units = units[units.asset_type.isin(cfg["fail"])]
    if cfg["road_classes"]:
        units = units[(units.asset_type != "road") | units.road_class.isin(cfg["road_classes"])]
    # Flow on the links a failure removes or changes (bus detours).
    touched = [
        list(ids) + [c["edge_id"] for c in (changes if changes is not None else [])]
        for ids, changes in zip(units["link_ids"], units.get("link_changes", [None] * len(units)))
    ]
    exploded = pd.DataFrame({"asset_id": units["asset_id"].to_numpy(), "link_ids": touched}).explode("link_ids")
    exploded["flow"] = exploded["link_ids"].map(flows).fillna(0.0)
    units = units.merge(
        exploded.groupby("asset_id", as_index=False)["flow"].max().rename(columns={"flow": "baseline_flow"}),
        on="asset_id",
    )
    units = units[units.baseline_flow >= cfg["min_baseline_flow"]]

    # Deal scenarios round-robin in descending flow, so batches get a similar mix
    # of heavy and light scenarios.
    units = units.sort_values("baseline_flow", ascending=False).reset_index(drop=True)
    units["batch"] = [f"batch-{i:04d}" for i in np.arange(len(units)) % int(cfg["n_batches"])]
    units["scenario_id"] = units["asset_type"] + "/" + units["asset_id"].astype(str)
    columns = [
        "scenario_id",
        "asset_type",
        "asset_id",
        "road_class",
        "link_ids",
        "link_changes",
        "baseline_flow",
        "batch",
    ]
    units[columns].to_parquet(output, index=False)


if __name__ == "__main__":
    main()
