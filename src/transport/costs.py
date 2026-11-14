"""Generalised cost and per-path journey metrics for the land transport analysis.

Every network edge carries ``time_min`` (perceived minutes: in-vehicle time plus
weighted waiting and walking), ``length_km`` and ``fare``. Its ``cost`` (the
attribute that transport_flow_model routes on) is

    cost = per_minute * time_min + per_km[mode] * length_km + fare

Under disruption we report the changes in journey time and distance as well as
in cost. ``transport_flow_model`` only returns path costs, so we recompute time
and distance by summing edge attributes along each path's ``edge_path``.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


def generalised_cost(
    time_min: np.ndarray | pd.Series,
    length_km: np.ndarray | pd.Series,
    mode: np.ndarray | pd.Series,
    *,
    per_minute: float,
    per_km: Mapping[str, float],
    fare: np.ndarray | pd.Series | float = 0.0,
) -> np.ndarray:
    """Return the generalised cost per edge (currency per person-trip)."""
    mode = pd.Series(np.asarray(mode))
    unknown = set(mode.unique()) - set(per_km)
    if unknown:
        raise ValueError(f"No per_km cost for modes: {sorted(unknown)}")
    km_rate = mode.map(per_km).to_numpy(dtype=float)
    return (
        per_minute * np.asarray(time_min, dtype=float)
        + km_rate * np.asarray(length_km, dtype=float)
        + np.asarray(fare, dtype=float)
    )


def path_metrics(
    paths: pd.DataFrame,
    edges: pd.DataFrame,
    metrics: tuple[str, ...] = ("time_min", "length_km", "cost"),
) -> pd.DataFrame:
    """Sum edge ``metrics`` along each path.

    Parameters
    ----------
    paths
        One row per allocated path, with ``origin_id``, ``destination_id``,
        ``flow`` and ``edge_path`` (a list of edge ids), as in
        ``transport_flow_model`` path tables.
    edges
        Edge table with ``edge_id`` and the ``metrics`` columns.

    Returns
    -------
    ``paths`` without ``edge_path``, plus one column per metric (per trip, not
    multiplied by flow).
    """
    exploded = paths[["edge_path"]].explode("edge_path").rename(columns={"edge_path": "edge_id"})
    lookup = edges.set_index("edge_id")[list(metrics)]
    values = exploded.join(lookup, on="edge_id")[list(metrics)]
    sums = values.groupby(level=0).sum()
    out = paths.drop(columns=["edge_path"]).copy()
    # Drop any existing metric column (e.g. tfm's "cost") so the recomputed one wins.
    out = out.drop(columns=[m for m in metrics if m in out.columns])
    return out.join(sums)


def od_metrics(path_table: pd.DataFrame, metrics: tuple[str, ...] = ("time_min", "length_km", "cost")) -> pd.DataFrame:
    """Turn per-path metrics into per-OD flow totals and flow-weighted means.

    Returns one row per (origin_id, destination_id) with ``flow`` and, for each
    metric, ``<metric>`` (mean per trip) and ``<metric>_total`` (summed over flow).
    """
    df = path_table.copy()
    for metric in metrics:
        df[f"{metric}_total"] = df[metric] * df["flow"]
    grouped = df.groupby(["origin_id", "destination_id"], as_index=False)[
        ["flow"] + [f"{m}_total" for m in metrics]
    ].sum()
    for metric in metrics:
        grouped[metric] = grouped[f"{metric}_total"] / grouped["flow"]
    return grouped


def disruption_deltas(
    affected_baseline_paths: pd.DataFrame,
    rerouted_paths: pd.DataFrame,
    isolated: pd.DataFrame,
    metrics: tuple[str, ...] = ("time_min", "length_km", "cost"),
) -> pd.DataFrame:
    """Per-OD change in journey metrics for one disruption scenario.

    ``affected_baseline_paths`` and ``rerouted_paths`` are per-path tables that
    have already been through ``path_metrics``. Only the affected flow changes;
    the rest of each OD pair's flow keeps its baseline path.

    Returns one row per affected OD pair with ``affected_flow``,
    ``rerouted_flow``, ``isolated_flow`` and, for each metric,
    ``delta_<metric>_total``: the extra metric summed over rerouted flow
    (rerouted totals minus the baseline totals of the same flow). Isolated flow
    is reported separately and is not included in the deltas.
    """
    keys = ["origin_id", "destination_id"]
    base = od_metrics(affected_baseline_paths, metrics).rename(columns={"flow": "affected_flow"})
    new = od_metrics(rerouted_paths, metrics).rename(columns={"flow": "rerouted_flow"})
    iso = isolated.rename(columns={"value": "isolated_flow", "flow": "isolated_flow"})[[*keys, "isolated_flow"]]
    out = base[[*keys, "affected_flow", *list(metrics)]].merge(
        new[keys + ["rerouted_flow"] + [f"{m}_total" for m in metrics]], on=keys, how="left"
    )
    out = out.merge(iso.groupby(keys, as_index=False).sum(), on=keys, how="left")
    out[["rerouted_flow", "isolated_flow"]] = out[["rerouted_flow", "isolated_flow"]].fillna(0.0)
    for metric in metrics:
        rerouted_total = out[f"{metric}_total"].fillna(0.0)
        # Baseline metric for the flow that was actually rerouted (mean per trip x rerouted flow).
        out[f"delta_{metric}_total"] = rerouted_total - out[metric] * out["rerouted_flow"]
        out = out.drop(columns=[f"{metric}_total"]).rename(columns={metric: f"baseline_{metric}"})
    return out
