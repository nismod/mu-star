import numpy as np
import pandas as pd
import pytest

from transport.costs import disruption_deltas, generalised_cost, path_metrics


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
