"""Mode fallback under disruption.

When a disruption leaves trips with no path on their own mode (isolated
demand), they can switch to other modes in turn, e.g. metro -> bus -> car.
Each fallback mode is assigned on the disrupted network, on top of the flows
already there (capacity constrained), between that mode's zone centroids.

Fallback paths are returned with the *original* origin_id / destination_id
(e.g. zone/<id>/metro), so that they can be compared with the trips' baseline
paths, plus a ``fallback_mode`` column.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

BIG_CAPACITY = 1e15
NETWORK_COLUMNS = ["edge_id", "edge_from", "edge_to", "cost", "capacity"]
PATH_COLUMNS = ["origin_id", "destination_id", "flow", "edge_path", "cost", "fallback_mode"]


def node_mode(node_ids: pd.Series) -> pd.Series:
    """zone/<id>/<mode> -> <mode>."""
    return node_ids.str.rsplit("/", n=1).str[-1]


def with_mode(node_ids: pd.Series, modes: pd.Series) -> pd.Series:
    """zone/<id>/<any> -> zone/<id>/<mode>, row by row."""
    return node_ids.str.replace(r"/[^/]+$", "", regex=True) + "/" + modes


def disrupted_network_table(links: pd.DataFrame, link_flows: pd.DataFrame, removed: Iterable) -> pd.DataFrame:
    """Network links without the removed ones, with current flow as occupied capacity."""
    table = links.loc[~links["edge_id"].isin(set(removed)), NETWORK_COLUMNS].copy()
    table["capacity"] = table["capacity"].replace(np.inf, BIG_CAPACITY)
    table["flow"] = table["edge_id"].map(link_flows.set_index("edge_id")["flow"]).fillna(0.0)
    return table


def mode_fallback(
    links: pd.DataFrame,
    link_flows: pd.DataFrame,
    removed: Iterable,
    isolated: pd.DataFrame,
    fallbacks: Mapping[str, Sequence[str]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign isolated trips to fallback modes in order.

    Parameters
    ----------
    links
        Multimodal link table (edge_id, edge_from, edge_to, cost, capacity).
    link_flows
        edge_id, flow on the disrupted network (tfm ScenarioResult.link_flows).
    removed
        Link ids removed in the scenario.
    isolated
        origin_id, destination_id, value: demand with no path on its own mode.
    fallbacks
        For each original mode, the modes to try in order, e.g.
        ``{"metro": ["bus", "car"]}``. Unlisted modes stay isolated.

    Returns
    -------
    (paths, still_isolated)
        paths: PATH_COLUMNS, with the original origin and destination ids.
        still_isolated: origin_id, destination_id, value.
    """
    from transport_flow_model import Demand, Network, assign

    removed = set(removed)
    remaining = isolated[["origin_id", "destination_id", "value"]].copy()
    remaining["mode"] = node_mode(remaining["origin_id"])
    flows = link_flows[["edge_id", "flow"]].copy()
    found = []
    steps = max((len(modes) for modes in fallbacks.values()), default=0)

    for step in range(steps):
        target = remaining["mode"].map(lambda m: (list(fallbacks.get(m, [])) + [None] * steps)[step])
        todo = remaining[target.notna() & (remaining["value"] > 0)].copy()
        if todo.empty:
            break
        todo["fallback_mode"] = target[todo.index]
        todo["f_origin"] = with_mode(todo["origin_id"], todo["fallback_mode"])
        todo["f_destination"] = with_mode(todo["destination_id"], todo["fallback_mode"])

        table = disrupted_network_table(links, flows, removed)
        nodes = set(table["edge_from"]) | set(table["edge_to"])
        todo = todo[todo.f_origin.isin(nodes) & todo.f_destination.isin(nodes)]
        if todo.empty:
            continue
        requested = todo.groupby(["f_origin", "f_destination"], as_index=False)["value"].sum()
        demand = Demand(requested.set_axis(["origin_id", "destination_id", "value"], axis=1))
        result = assign(Network(table), demand, "sequential", include_paths=True, capacity_constrained=True)
        paths = result.paths.to_pandas().rename(columns={"origin_id": "f_origin", "destination_id": "f_destination"})

        # Share each fallback pair's paths back over the original OD rows, by demand.
        rows = todo.merge(requested.rename(columns={"value": "pair_value"}), on=["f_origin", "f_destination"])
        rows["weight"] = rows["value"] / rows["pair_value"]
        matched = paths.merge(
            rows[["f_origin", "f_destination", "origin_id", "destination_id", "weight", "fallback_mode"]],
            on=["f_origin", "f_destination"],
        )
        matched["flow"] = matched["flow"] * matched["weight"]
        found.append(matched[PATH_COLUMNS])

        served = matched.groupby(["origin_id", "destination_id"])["flow"].sum()
        key = pd.MultiIndex.from_frame(remaining[["origin_id", "destination_id"]])
        remaining["value"] = (remaining["value"].to_numpy() - served.reindex(key).fillna(0.0).to_numpy()).clip(min=0)

        # Fallback flows occupy capacity for later steps.
        edge_flow = paths[["edge_path", "flow"]].explode("edge_path").groupby("edge_path")["flow"].sum()
        flows["flow"] = flows["flow"] + flows["edge_id"].map(edge_flow).fillna(0.0)

    still = remaining.loc[remaining["value"] > 1e-9, ["origin_id", "destination_id", "value"]]
    out = pd.concat(found, ignore_index=True) if found else pd.DataFrame(columns=PATH_COLUMNS)
    return out, still
