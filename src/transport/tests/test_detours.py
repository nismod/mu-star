import pandas as pd
import pytest

from transport.detours import bus_detours


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
