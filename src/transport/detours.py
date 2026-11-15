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

:func:`detours_table` and :func:`read_detours` convert the results to and
from the ``detours.parquet`` table (:data:`DETOUR_SCHEMA`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

#: Added to every arc time, so that zero-time arcs are still edges for scipy.
EPSILON = 1e-9


@dataclass
class Detour:
    """What one failed road edge does to the bus network."""

    changes: list[dict] = field(default_factory=list)  # {segment_id, time_min, length_km}
    skipped_stops: list[tuple] = field(default_factory=list)  # (route_id, direction, stop_id)
    cut_segments: list[str] = field(default_factory=list)  # segment_id


class RoadGraph:
    """Directed road graph (time weights) that can drop physical edges temporarily.

    The CSR matrix holds one entry per node pair: the fastest of its parallel
    arcs. :meth:`without` swaps the entries of a failed edge's arcs for the
    next fastest parallel arc (or infinity) and restores them afterwards, so
    the matrix is built once.
    """

    def __init__(self, edges: pd.DataFrame):
        two_way = ~edges["oneway"].astype(bool).to_numpy()
        tails = np.r_[edges["from_node"].to_numpy(), edges["to_node"].to_numpy()[two_way]]
        heads = np.r_[edges["to_node"].to_numpy(), edges["from_node"].to_numpy()[two_way]]
        arcs = pd.DataFrame(
            {
                "tail": tails,
                "head": heads,
                "edge_id": np.r_[edges["edge_id"].to_numpy(), edges["edge_id"].to_numpy()[two_way]],
                "time": np.r_[edges["time_min"].to_numpy(), edges["time_min"].to_numpy()[two_way]].astype(float),
                "length": np.r_[edges["length_km"].to_numpy(), edges["length_km"].to_numpy()[two_way]].astype(float),
            }
        )
        self.nodes = pd.Index(pd.unique(np.r_[arcs["tail"].to_numpy(), arcs["head"].to_numpy()]))
        arcs["u"] = self.nodes.get_indexer(arcs["tail"])
        arcs["v"] = self.nodes.get_indexer(arcs["head"])
        # Parallel arcs: keep them all, sorted fastest first within each node pair.
        arcs = arcs.sort_values(["u", "v", "time"], kind="stable").reset_index(drop=True)
        self.arcs = arcs
        self.edge_time = edges.set_index("edge_id")["time_min"]

        # One CSR entry per node pair (in the same order as the sorted arcs):
        # entry p covers arcs pair_start[p]:pair_start[p + 1], fastest first.
        u, v = arcs["u"].to_numpy(), arcs["v"].to_numpy()
        new_pair = np.r_[True, (u[1:] != u[:-1]) | (v[1:] != v[:-1])] if len(arcs) else np.array([], dtype=bool)
        self.pair_start = np.r_[np.flatnonzero(new_pair), len(arcs)]
        self.arc_pair = np.cumsum(new_pair) - 1
        first = self.pair_start[:-1]
        n = len(self.nodes)
        indptr = np.searchsorted(u[first], np.arange(n + 1))
        self.matrix = csr_matrix((arcs["time"].to_numpy()[first] + EPSILON, v[first], indptr), shape=(n, n))
        self.length = arcs["length"].to_numpy()[first].copy()  # length of the arc in each entry
        self._arc_time = arcs["time"].to_numpy()
        self._arc_length = arcs["length"].to_numpy()
        self._arc_edge = arcs["edge_id"].to_numpy()
        self._edge_arcs = {edge: idx.to_numpy() for edge, idx in arcs.groupby("edge_id").groups.items()}

    @contextmanager
    def without(self, failed: Iterable):
        """Within the block, the matrix routes around the edges in ``failed``."""
        failed = set(failed)
        pairs = sorted({int(self.arc_pair[a]) for e in failed for a in self._edge_arcs.get(e, ())})
        saved = [(p, self.matrix.data[p], self.length[p]) for p in pairs]
        for p in pairs:
            spare = [a for a in range(self.pair_start[p], self.pair_start[p + 1]) if self._arc_edge[a] not in failed]
            if spare:
                self.matrix.data[p] = self._arc_time[spare[0]] + EPSILON
                self.length[p] = self._arc_length[spare[0]]
            else:
                self.matrix.data[p] = np.inf
        try:
            yield self
        finally:
            for p, time, length in saved:
                self.matrix.data[p] = time
                self.length[p] = length

    def _entry(self, tail: int, head: int) -> int:
        start, end = self.matrix.indptr[tail], self.matrix.indptr[tail + 1]
        return start + int(np.searchsorted(self.matrix.indices[start:end], head))

    def path(self, source, target, limit):
        """(time, length) of the fastest path source -> target on the current matrix, or None."""
        if source not in self.nodes or target not in self.nodes:
            return None
        s, t = self.nodes.get_loc(source), self.nodes.get_loc(target)
        if s == t:
            return 0.0, 0.0
        dist, pred = dijkstra(self.matrix, indices=s, limit=limit, return_predecessors=True)
        if not np.isfinite(dist[t]):
            return None
        length, node = 0.0, t
        while node != s:
            prev = pred[node]
            length += self.length[self._entry(prev, node)]
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
        road edge id -> :class:`Detour`, for the failed edges that buses use.
    """
    graph = RoadGraph(road_edges)
    segments = bus_segments.sort_values(["route_id", "direction", "seq"]).reset_index(drop=True)
    road_time = graph.edge_time

    # Each route direction as plain arrays, and which positions use each road edge.
    runs = {}
    uses = defaultdict(lambda: defaultdict(list))  # road edge -> run key -> positions
    for key, run in segments.groupby(["route_id", "direction"], sort=True):
        ids = list(run["road_edge_ids"])
        runs[key] = {
            "segment_id": run["segment_id"].to_numpy(),
            "from_stop": run["from_stop"].to_numpy(),
            "to_stop": run["to_stop"].to_numpy(),
            "time_min": run["time_min"].to_numpy(dtype=float),
            "road_time": np.array([float(road_time.reindex(list(e)).fillna(0.0).sum()) for e in ids]),
        }
        for k, edges in enumerate(ids):
            for edge in dict.fromkeys(edges):
                uses[edge][key].append(k)

    results = {}
    for edge in failed_edges:
        if edge not in uses or edge in results:
            continue
        result = Detour()
        with graph.without({edge}):
            for key in sorted(uses[edge]):
                _detour_run(
                    graph,
                    runs[key],
                    key,
                    set(uses[edge][key]),
                    stop_nodes,
                    result,
                    speed_factor=speed_factor,
                    max_skipped_stops=max_skipped_stops,
                    max_detour_factor=max_detour_factor,
                )
        results[edge] = result
    return results


def _detour_run(graph, run, key, hit, stop_nodes, result, *, speed_factor, max_skipped_stops, max_detour_factor):
    """Add the changes, skips and cuts on one route direction to ``result``."""
    route, direction = key
    n = len(run["segment_id"])
    k = 0
    while k < n:
        if k not in hit:
            k += 1
            continue
        anchor = stop_nodes.get(run["from_stop"][k])
        done = False
        # Try the next stop, then skip up to max_skipped_stops further stops.
        for j in range(k, min(n, k + 1 + max_skipped_stops)):
            original = run["road_time"][k : j + 1].sum()
            found = graph.path(anchor, stop_nodes.get(run["to_stop"][j]), limit=max(original, 1.0) * max_detour_factor)
            if found is None:
                continue
            detour_time, detour_length = found
            scheduled = run["time_min"][k : j + 1]
            total = scheduled.sum()
            new_total = total + (detour_time - original) / speed_factor
            weights = scheduled / total if total > 0 else np.full(len(scheduled), 1 / len(scheduled))
            for segment_id, w in zip(run["segment_id"][k : j + 1], weights):
                result.changes.append(
                    {
                        "segment_id": segment_id,
                        "time_min": float(max(new_total * w, 0.0)),
                        "length_km": float(detour_length * w),
                    }
                )
            for stop in run["to_stop"][k:j]:
                result.skipped_stops.append((route, direction, stop))
            k = j + 1
            done = True
            break
        if not done:
            result.cut_segments.append(run["segment_id"][k])
            k += 1


# --- detours.parquet ----------------------------------------------------------------------

#: One row per failed road edge that buses use.
DETOUR_SCHEMA = pa.schema(
    [
        ("road_edge_id", pa.string()),
        (
            "changes",
            pa.list_(pa.struct([("segment_id", pa.string()), ("time_min", pa.float64()), ("length_km", pa.float64())])),
        ),
        (
            "skipped_stops",
            pa.list_(pa.struct([("route_id", pa.string()), ("direction", pa.int64()), ("stop_id", pa.string())])),
        ),
        ("cut_segments", pa.list_(pa.string())),
    ]
)


def detours_table(detours: Mapping) -> pa.Table:
    """``bus_detours`` result as a table with :data:`DETOUR_SCHEMA`."""
    edges = list(detours)
    return pa.table(
        {
            "road_edge_id": [str(e) for e in edges],
            "changes": [
                [
                    {"segment_id": str(c["segment_id"]), "time_min": c["time_min"], "length_km": c["length_km"]}
                    for c in detours[e].changes
                ]
                for e in edges
            ],
            "skipped_stops": [
                [{"route_id": str(r), "direction": int(d), "stop_id": str(s)} for r, d, s in detours[e].skipped_stops]
                for e in edges
            ],
            "cut_segments": [[str(s) for s in detours[e].cut_segments] for e in edges],
        },
        schema=DETOUR_SCHEMA,
    )


def read_detours(path) -> dict:
    """detours.parquet -> {road edge id: :class:`Detour`}."""
    import pyarrow.parquet as pq

    rows = pq.read_table(path).to_pylist()
    return {
        row["road_edge_id"]: Detour(
            changes=list(row["changes"]),
            skipped_stops=[(s["route_id"], s["direction"], s["stop_id"]) for s in row["skipped_stops"]],
            cut_segments=list(row["cut_segments"]),
        )
        for row in rows
    }
