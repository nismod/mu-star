import numpy as np
import pandas as pd
import pytest

from transport.fares import bus_fare, path_fares

SCHEDULE = [
    {"max_stages": 1, "fare": 17},
    {"max_stages": 2, "fare": 24},
    {"max_stages": 4, "fare": 30},
    {"max_stages": 39, "fare": 47},
]


def links():
    return pd.DataFrame(
        [
            # walk, board route R1 at stage 1, ride two segments to stage 3, alight
            ("w1", "walk_access", 0.0, None, np.nan, np.nan, None, None),
            ("b/R1", "bus_board", 30.0, None, np.nan, np.nan, None, None),
            ("R1/1", "bus", 1.0, "R1/0", 1, 2, None, None),
            ("R1/2", "bus", 1.0, "R1/0", 2, 3, None, None),
            ("a/R1", "bus_alight", 0.0, None, np.nan, np.nan, None, None),
            # transfer to R2 with no stage data: approximate fare
            ("b/R2", "bus_board", 30.0, None, np.nan, np.nan, None, None),
            ("R2/1", "bus", 2.0, "R2/0", np.nan, np.nan, None, None),
            ("a/R2", "bus_alight", 0.0, None, np.nan, np.nan, None, None),
            # metro: main line A-B, change at B to the branch B-C
            ("mb/main", "metro_board", 32.0, None, np.nan, np.nan, None, None),
            ("m/main/A-B", "metro", 2.0, None, np.nan, np.nan, "A", "B"),
            ("ma/main", "metro_alight", 0.0, None, np.nan, np.nan, None, None),
            ("x/B", "interchange", 0.0, None, np.nan, np.nan, None, None),
            ("mb/branch", "metro_board", 32.0, None, np.nan, np.nan, None, None),
            ("m/branch/B-C", "metro", 1.0, None, np.nan, np.nan, "B", "C"),
            ("ma/branch", "metro_alight", 0.0, None, np.nan, np.nan, None, None),
        ],
        columns=["edge_id", "layer", "fare", "route_key", "stage_from", "stage_to", "station_from", "station_to"],
    )


def test_bus_fare_schedule():
    assert bus_fare(1, SCHEDULE) == 17
    assert bus_fare(3, SCHEDULE) == 30
    assert bus_fare(50, SCHEDULE) == 47
    assert np.isnan(bus_fare(np.nan, SCHEDULE))


def test_path_fares_bus_legs_and_metro_journey():
    paths = pd.DataFrame(
        {
            "edge_path": [
                ["w1", "b/R1", "R1/1", "R1/2", "a/R1", "w1"],  # 3 stages -> 30
                ["b/R1", "R1/1", "a/R1", "b/R2", "R2/1", "a/R2"],  # 2 stages -> 24, then approx 32
                ["w1", "mb/main", "m/main/A-B", "ma/main", "x/B", "mb/branch", "m/branch/B-C", "ma/branch"],
                ["w1"],
            ]
        }
    )
    stages = pd.DataFrame({"from_station": ["A"], "to_station": ["C"], "stage": [3]})
    fares = path_fares(paths, links(), SCHEDULE, {1: 32, 2: 38, 3: 43}, stages)
    assert fares.tolist() == pytest.approx([30.0, 24.0 + 32.0, 43.0, 0.0])


def test_path_fares_without_stage_data_uses_approximation():
    paths = pd.DataFrame({"edge_path": [["mb/main", "m/main/A-B", "ma/main"]]})
    fares = path_fares(paths, links(), SCHEDULE, {1: 32}, None)
    assert fares.iloc[0] == pytest.approx(34.0)
