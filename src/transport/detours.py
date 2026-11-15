"""Bus detours around a failed road edge.

When a road edge fails, buses on the routes that use it do not stop running:

1. **Detour**: between the same two stops, the bus takes the fastest road path
   that avoids the failed edge. Its in-vehicle time becomes the scheduled
   time plus (detour road time - original road time) / speed_factor.
2. **Skip stops**: if the next stop cannot be reached (it is only accessible
   over the failed edge), the bus goes on to the following stop, skipping up
   to ``max_skipped_stops`` stops in a row. Skipped stops lose their
   boarding and alighting links for that route; passengers riding through
   them stay on the bus.
3. **Cut**: if no stop within reach can be reached, the segment over the
   failed edge is removed (the route is cut there).

A detour is only accepted if its road time is at most ``max_detour_factor``
times the original road time of the stretch it replaces.

The result per failed road edge is a set of *link changes* (bus links that
stay, with new time and length) and *removed links* (boarding / alighting at
skipped stops, cut segments), which the criticality step applies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


@dataclass
class Detour:
    """What one failed road edge does to the bus network."""

    changes: list[dict] = field(default_factory=list)  # {segment_id, time_min, length_km}
    skipped_stops: list[tuple] = field(default_factory=list)  # (route_id, direction, stop_id)
    cut_segments: list[str] = field(default_factory=list)  # segment_id


class RoadGraph:
    """Directed road graph (time weights) that can drop one physical edge at a time."""

    def __init__(self, edges: pd.DataFrame):
        two_way = ~edges["oneway"].astype(bool).to_numpy()
        tails = np.r_[edges["from_node"].to_numpy(), edges["to_node"].to_numpy()[two_way]]
        heads = np.r_[edges["to_node"].to_numpy(), edges["from_node"].to_numpy()[two_way]]
        arcs = pd.DataFrame(
            {
                "tail": tails,
                "head": heads,
                "edge_id": np.r_[edges["edge_id"].to_numpy(), edges["edge_id"].to_numpy()[two_way]],
                "time": np.r_[edges["time_min"].to_numpy(), edges["time_min"].to_numpy()[two_way]],
                "length": np.r_[edges["length_km"].to_numpy(), edges["length_km"].to_numpy()[two_way]],
            }
        )
        self.nodes = pd.Index(pd.unique(np.r_[arcs["tail"].to_numpy(), arcs["head"].to_numpy()]))
        arcs["u"] = self.nodes.get_indexer(arcs["tail"])
        arcs["v"] = self.nodes.get_indexer(arcs["head"])
        # Parallel arcs: keep them all, sorted fastest first within each node pair.
        self.arcs = arcs.sort_values(["u", "v", "time"], kind="stable").reset_index(drop=True)
        self.edge_time = edges.set_index("edge_id")["time_min"]

    def _matrix(self, failed: set) -> tuple[csr_matrix, pd.DataFrame]:
        usable = self.arcs[~self.arcs["edge_id"].isin(failed)]
        best = usable.drop_duplicates(["u", "v"], keep="first")
        n = len(self.nodes)
        matrix = csr_matrix(
            (best["time"].to_numpy() + 1e-9, (best["u"].to_numpy(), best["v"].to_numpy())), shape=(n, n)
        )
        return matrix, best.set_index(["u", "v"])

    def detour(self, matrix, best, source, target, limit):
        """(time, length) of the fastest path source -> target, or None."""
        if source not in self.nodes or target not in self.nodes:
            return None
        s, t = self.nodes.get_loc(source), self.nodes.get_loc(target)
        if s == t:
            return 0.0, 0.0
        dist, pred = dijkstra(matrix, indices=s, limit=limit, return_predecessors=True)
        if not np.isfinite(dist[t]):
            return None
        length, node = 0.0, t
        while node != s:
            prev = pred[node]
            length += best.at[(prev, node), "length"]
            node = prev
        return float(dist[t]), float(length)


def bus_detours(
    road_edges: pd.DataFrame,
    bus_segments: pd.DataFrame,
    stop_nodes: Mapping,
    failed_edges: Iterable,
    *,
    speed_factor: float,
    max_skipped_stops: int = 3,
    max_detour_factor: float = 4.0,
) -> dict:
    """Detours, skipped stops and cut segments for each failed road edge.

    Parameters
    ----------
    road_edges
        edge_id, from_node, to_node, oneway, time_min, length_km.
    bus_segments
        segment_id, route_id, direction, seq, from_stop, to_stop,
        road_edge_ids (list), time_min (scheduled), length_km.
    stop_nodes
        stop_id -> road node id.
    failed_edges
        Road edge ids to evaluate, one failure at a time.

    Returns
    -------
    dict
        road edge id -> :class:`Detour`.
    """
    graph = RoadGraph(road_edges)
    segments = bus_segments.sort_values(["route_id", "direction", "seq"]).reset_index(drop=True)
    road_time = graph.edge_time
    segments["road_time"] = [float(road_time.reindex(ids).fillna(0.0).sum()) for ids in segments.road_edge_ids]
    exploded = segments[["road_edge_ids"]].explode("road_edge_ids").dropna()
    uses = exploded.groupby("road_edge_ids").groups  # road edge -> segment row positions

    results = {}
    for edge in failed_edges:
        if edge not in uses:
            continue
        matrix, best = graph._matrix({edge})
        result = Detour()
        affected = segments.loc[sorted(set(uses[edge]))]
        for (route, direction), _ in affected.groupby(["route_id", "direction"]):
            run = segments[(segments.route_id == route) & (segments.direction == direction)].reset_index(drop=True)
            hit = run["road_edge_ids"].map(lambda ids: edge in ids).to_numpy()
            k = 0
            while k < len(run):
                if not hit[k]:
                    k += 1
                    continue
                anchor = run.loc[k, "from_stop"]
                done = False
                # Try the next stop, then skip up to max_skipped_stops further stops.
                for j in range(k, min(len(run), k + 1 + max_skipped_stops)):
                    stretch = run.loc[k:j]
                    target = run.loc[j, "to_stop"]
                    original = stretch["road_time"].sum()
                    found = graph.detour(
                        matrix,
                        best,
                        stop_nodes.get(anchor),
                        stop_nodes.get(target),
                        limit=max(original, 1.0) * max_detour_factor,
                    )
                    if found is None:
                        continue
                    detour_time, detour_length = found
                    scheduled = stretch["time_min"].to_numpy(dtype=float)
                    new_total = scheduled.sum() + (detour_time - original) / speed_factor
                    weights = (
                        scheduled / scheduled.sum() if scheduled.sum() > 0 else np.full(len(stretch), 1 / len(stretch))
                    )
                    for (_, seg), w in zip(stretch.iterrows(), weights):
                        result.changes.append(
                            {
                                "segment_id": seg["segment_id"],
                                "time_min": float(max(new_total * w, 0.0)),
                                "length_km": float(detour_length * w),
                            }
                        )
                    for stop in stretch["to_stop"].iloc[:-1]:
                        result.skipped_stops.append((route, direction, stop))
                    k = j + 1
                    done = True
                    break
                if not done:
                    result.cut_segments.append(run.loc[k, "segment_id"])
                    k += 1
        results[edge] = result
    return results
