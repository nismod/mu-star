import numpy as np
import pandas as pd
import pytest

from transport.costs import disruption_deltas, generalised_cost, path_metrics
from transport.radiation import prune_small_flows, radiation_probabilities, radiation_trips

INF = np.inf


def line_impedance(positions):
    positions = np.asarray(positions, dtype=float)
    return np.abs(positions[:, None] - positions[None, :])


def test_intervening_opportunities_are_closer_zones():
    # Zones on a line at 0, 1, 2, 3; all masses 1.
    d = line_impedance([0, 1, 2, 3])
    ones = np.ones(4)
    p = radiation_probabilities(d, ones, ones, normalisation="none")
    # From zone 0: s_01 = 0, s_02 = 1, s_03 = 2
    expected = [1 / (1 * 2), 1 / (2 * 3), 1 / (3 * 4)]
    np.testing.assert_allclose(p[0, 1:], expected)
    assert p[0, 0] == 0


def test_ties_count_as_intervening():
    # From zone 0, zones 1 and 2 are equidistant: each is intervening for the other.
    d = np.array([[0, 1, 1], [1, 0, 2], [1, 2, 0]], dtype=float)
    ones = np.ones(3)
    p = radiation_probabilities(d, ones, ones, normalisation="none")
    np.testing.assert_allclose(p[0, 1], 1 / (2 * 3))
    np.testing.assert_allclose(p[0, 2], 1 / (2 * 3))


def test_row_normalisation_and_threshold():
    d = line_impedance([0, 1, 2, 10])
    ones = np.ones(4)
    p = radiation_probabilities(d, ones, ones, max_impedance=5, normalisation="row")
    np.testing.assert_allclose(p.sum(axis=1)[:3], 1.0)
    assert p[0, 3] == 0
    assert p[3].sum() == 0  # nothing within 5 of zone 3


def test_unreachable_and_zero_mass():
    d = np.array([[0, INF, 1], [INF, 0, INF], [1, INF, 0]])
    p = radiation_probabilities(d, np.array([1.0, 1.0, 0.0]), np.array([1.0, 1.0, 1.0]))
    assert p[0, 1] == 0
    assert p[2].sum() == 0  # zero origin mass gives no trips


def test_trips_keep_origin_totals():
    d = line_impedance([0, 1, 2, 3])
    workers = np.array([100.0, 50.0, 10.0, 0.0])
    jobs = np.array([10.0, 60.0, 80.0, 10.0])
    t = radiation_trips(d, workers, workers, jobs, normalisation="row")
    np.testing.assert_allclose(t.sum(axis=1), workers)
    pruned = prune_small_flows(t, 5.0)
    np.testing.assert_allclose(pruned.sum(axis=1), workers)
    assert ((pruned == 0) | (pruned >= 5.0)).all()


def test_invalid_normalisation():
    with pytest.raises(ValueError):
        radiation_probabilities(np.zeros((2, 2)), np.ones(2), np.ones(2), normalisation="bad")


def test_generalised_cost():
    cost = generalised_cost(
        [10, 10], [5, 5], ["car", "bus"], per_minute=2.0, per_km={"car": 1.0, "bus": 0.0}, fare=[0, 3]
    )
    np.testing.assert_allclose(cost, [25, 23])


def test_path_metrics_and_deltas():
    edges = pd.DataFrame(
        {
            "edge_id": ["AB", "BC", "AC"],
            "time_min": [1.0, 1.0, 5.0],
            "length_km": [1.0, 1.0, 3.0],
            "cost": [2.0, 2.0, 10.0],
        }
    )
    base = pd.DataFrame(
        {"origin_id": ["A"], "destination_id": ["C"], "flow": [10.0], "edge_path": [["AB", "BC"]], "cost": [4.0]}
    )
    rerouted = pd.DataFrame(
        {"origin_id": ["A"], "destination_id": ["C"], "flow": [6.0], "edge_path": [["AC"]], "cost": [10.0]}
    )
    isolated = pd.DataFrame({"origin_id": ["A"], "destination_id": ["C"], "value": [4.0]})
    base_m = path_metrics(base, edges)
    assert base_m.loc[0, "time_min"] == 2.0
    deltas = disruption_deltas(base_m, path_metrics(rerouted, edges), isolated)
    row = deltas.iloc[0]
    assert row["affected_flow"] == 10
    assert row["rerouted_flow"] == 6
    assert row["isolated_flow"] == 4
    assert row["delta_time_min_total"] == pytest.approx(6 * (5 - 2))
    assert row["delta_length_km_total"] == pytest.approx(6 * (3 - 2))
