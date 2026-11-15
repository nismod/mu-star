import itertools
import time

import numpy as np
import pandas as pd
import pytest

from transport.detours import RoadGraph, bus_detours, detours_table, read_detours


def network():
    # A - B - C - D along a main road, with a slow bypass A - E - C around B.
    #       B is a dead end off the main road for stop "sB" via edge "BB".
    edges = pd.DataFrame(
        [
            ("AB", "A", "B", 1.0, 1.0),
            ("BC", "B", "C", 1.0, 1.0),
            ("CD", "C", "D", 1.0, 1.0),
            ("AE", "A", "E", 2.0, 2.0),
            ("EC", "E", "C", 2.0, 2.0),
            ("BB", "B", "F", 0.5, 0.5),  # spur to stop sF
        ],
        columns=["edge_id", "from_node", "to_node", "time_min", "length_km"],
    )
    edges["oneway"] = False
    return edges


def segments():
    return pd.DataFrame(
        [
            ("R/0/1", "R", 0, 1, "sA", "sB", ["AB"], 2.0, 1.0),
            ("R/0/2", "R", 0, 2, "sB", "sC", ["BC"], 2.0, 1.0),
            ("R/0/3", "R", 0, 3, "sC", "sD", ["CD"], 2.0, 1.0),
            ("S/0/1", "S", 0, 1, "sA", "sF", ["AB", "BB"], 3.0, 1.5),
        ],
        columns=[
            "segment_id",
            "route_id",
            "direction",
            "seq",
            "from_stop",
            "to_stop",
            "road_edge_ids",
            "time_min",
            "length_km",
        ],
    )


SEGMENT_COLUMNS = ["segment_id", "route_id", "direction", "seq", "from_stop", "to_stop", "road_edge_ids"]
SEGMENT_COLUMNS += ["time_min", "length_km"]

STOPS = {"sA": "A", "sB": "B", "sC": "C", "sD": "D", "sF": "F"}


def test_detour_between_stops():
    # Failing BC: route R can still go B -> A -> E -> C (1 + 2 + 2 = 5 min road time vs 1)
    result = bus_detours(network(), segments(), STOPS, ["BC"], speed_factor=0.5, max_detour_factor=10)["BC"]
    assert result.skipped_stops == []
    change = {c["segment_id"]: c for c in result.changes}
    assert change["R/0/2"]["time_min"] == pytest.approx(2.0 + (5.0 - 1.0) / 0.5)
    assert change["R/0/2"]["length_km"] == pytest.approx(5.0)


def test_skip_unreachable_stop():
    # Failing AB: sB is still reachable via A-E-C-B, so it is a detour, not a skip.
    # Failing BB: sF is only reachable over BB, and route S ends there -> cut.
    detours = bus_detours(network(), segments(), STOPS, ["AB", "BB"], speed_factor=1.0)
    assert {c["segment_id"] for c in detours["AB"].changes} >= {"R/0/1"}
    assert detours["BB"].cut_segments == ["S/0/1"]


def test_skip_stop_when_detour_too_long():
    # Failing AB with a tight detour limit: A -> B via E and C is 5 min vs 1 (factor 5 > 4),
    # but A -> C (skipping sB) is 4 min vs 2 (factor 2), so sB is skipped.
    result = bus_detours(network(), segments(), STOPS, ["AB"], speed_factor=1.0, max_detour_factor=4.0)["AB"]
    assert ("R", 0, "sB") in result.skipped_stops
    change = {c["segment_id"]: c for c in result.changes}
    assert change["R/0/1"]["time_min"] + change["R/0/2"]["time_min"] == pytest.approx(4.0 + (4.0 - 2.0))


def test_detour_limit_leads_to_skipping():
    # Same failure with the default limit (4x): the 5-minute detour around a
    # 1-minute edge is rejected, so the bus skips sC and goes on to sD.
    result = bus_detours(network(), segments(), STOPS, ["BC"], speed_factor=0.5)["BC"]
    assert result.skipped_stops == [("R", 0, "sC")]


def test_parallel_edges_next_fastest_takes_over():
    # Two parallel roads A - B: fast "F" (1 min) and slow "S" (3 min). Failing F
    # sends the bus over S; failing S changes nothing for the fastest path.
    edges = pd.DataFrame(
        [("F", "A", "B", 1.0, 1.0), ("S", "B", "A", 3.0, 2.5), ("BC", "B", "C", 1.0, 1.0)],
        columns=["edge_id", "from_node", "to_node", "time_min", "length_km"],
    ).assign(oneway=False)
    segs = pd.DataFrame(
        [("R/0/1", "R", 0, 1, "sA", "sB", ["F"], 2.0, 1.0), ("R/1/1", "R", 1, 1, "sB", "sA", ["F"], 2.0, 1.0)],
        columns=SEGMENT_COLUMNS,
    )
    stops = {"sA": "A", "sB": "B"}
    result = bus_detours(edges, segs, stops, ["F"], speed_factor=1.0)["F"]
    change = {c["segment_id"]: c for c in result.changes}
    for segment in ("R/0/1", "R/1/1"):  # both directions
        assert change[segment]["time_min"] == pytest.approx(2.0 + (3.0 - 1.0))
        assert change[segment]["length_km"] == pytest.approx(2.5)
    graph = RoadGraph(edges)
    with graph.without({"S"}):
        assert graph.path("A", "B", limit=np.inf) == pytest.approx((1.0, 1.0))
    with graph.without({"F", "S"}):
        assert graph.path("A", "B", limit=np.inf) is None
    # Restored afterwards.
    assert graph.path("A", "B", limit=np.inf) == pytest.approx((1.0, 1.0))


def grid(n, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for j in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    t = float(rng.uniform(0.5, 2.0))
                    rows.append((f"e{i}_{j}_{di}", f"n{i}_{j}", f"n{i + di}_{j + dj}", t, t * 0.6))
    edges = pd.DataFrame(rows, columns=["edge_id", "from_node", "to_node", "time_min", "length_km"])
    edges["oneway"] = rng.uniform(size=len(edges)) < 0.1
    return edges


def test_without_matches_rebuilt_graph():
    edges = grid(8)
    # Add parallel arcs (some faster, some slower) to a few edges.
    extra = edges.sample(15, random_state=1).copy()
    extra["edge_id"] = extra["edge_id"] + "p"
    extra["time_min"] = extra["time_min"] * np.where(np.arange(15) % 2, 0.7, 1.5)
    edges = pd.concat([edges, extra], ignore_index=True)
    graph = RoadGraph(edges)
    for failed in list(edges.edge_id.sample(25, random_state=2)):
        reference = RoadGraph(edges[edges.edge_id != failed])
        with graph.without({failed}):
            for source, target in (("n0_0", "n7_7"), ("n3_2", "n4_6"), ("n7_0", "n0_7")):
                got, want = graph.path(source, target, 30.0), reference.path(source, target, 30.0)
                assert (got is None) == (want is None)
                if got is not None:
                    assert got == pytest.approx(want)


def test_detours_table_round_trip(tmp_path):
    import pyarrow.parquet as pq

    detours = bus_detours(network(), segments(), STOPS, ["AB", "BB", "BC"], speed_factor=0.5)
    path = tmp_path / "detours.parquet"
    pq.write_table(detours_table(detours), path)
    assert read_detours(path) == detours


def test_many_failures_are_fast():
    n = 60
    edges = grid(n)
    stops, segs = {}, []
    for r, j in enumerate((5, 20, 35, 50)):  # four east-west routes, a stop every 2 nodes
        for direction in (0, 1):
            columns = range(0, n, 2) if direction == 0 else range(n - 1 - (n - 1) % 2, -1, -2)
            cols = list(columns)
            for seq, (a, b) in enumerate(itertools.pairwise(cols)):
                for c in (a, b):
                    stops[f"s{c}_{j}"] = f"n{c}_{j}"
                lo = min(a, b)
                segs.append(
                    (f"R{r}/{direction}/{seq}", f"R{r}", direction, seq, f"s{a}_{j}", f"s{b}_{j}",
                     [f"e{lo}_{j}_1", f"e{lo + 1}_{j}_1"], 3.0, 1.2)
                )  # fmt: skip
    segs = pd.DataFrame(
        segs,
        columns=SEGMENT_COLUMNS,
    )
    failed = sorted({e for ids in segs.road_edge_ids for e in ids})
    start = time.perf_counter()
    detours = bus_detours(edges, segs, stops, failed, speed_factor=0.7)
    elapsed = time.perf_counter() - start
    assert len(detours) == len(failed) > 200
    assert elapsed < 10, f"{len(failed)} failures took {elapsed:.1f} s"
