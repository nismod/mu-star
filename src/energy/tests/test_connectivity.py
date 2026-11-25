import pandas as pd
import pypsa
import pytest

from energy.connectivity import distribution_failures

ISLAND_CARRIERS = ("thermal", "hydro")


def _feeders():
    """Substation S feeds d1 to d4 through a loop d1 - d2 - d3 and a spur d3 - d4; T feeds t1; d4 - t1 joins the
    two areas."""
    network = pypsa.Network()
    names = ["bus::S", "d1", "d2", "d3", "d4", "bus::T", "t1"]
    buses = pd.DataFrame({"v_nom": 11.0, "x": range(7), "y": 0.0}, index=names)
    network.import_components_from_dataframe(buses, "Bus")
    lines = pd.DataFrame(
        [
            ("anchor::S::osm", "bus::S", "d1", 0.01, "substation_anchor"),
            ("l12", "d1", "d2", 1.0, "osm"),
            ("l13", "d1", "d3", 1.0, "osm"),
            ("l23", "d2", "d3", 1.5, "osm"),
            ("l34", "d3", "d4", 1.0, "osm"),
            ("tie", "d4", "t1", 1.0, "osm"),
            ("anchor::T::osm", "bus::T", "t1", 0.01, "substation_anchor"),
        ],
        columns=["name", "bus0", "bus1", "length", "source"],
    ).set_index("name")
    network.import_components_from_dataframe(lines.assign(x=0.1), "Line")
    weights = pd.DataFrame(
        {
            "bus_id": names,
            "service_weight": [0.0, 0.1, 0.1, 0.2, 0.2, 0.0, 0.4],
            "area_bus_id": ["bus::S"] * 5 + ["bus::T"] * 2,
            "island": "mauritius",
        }
    )
    return network, weights


def test_distribution_lines_run_radially_cut_off_the_nodes_beyond_them():
    network, weights = _feeders()
    demand = {"peak": pd.Series({"mauritius": 100.0}), "average": pd.Series({"mauritius": 50.0})}

    table, not_joined, counts = distribution_failures(
        network.lines, network.generators, weights, demand, island_carriers=ISLAND_CARRIERS
    )

    # l12 feeds d2 (10 MW) and l13 feeds d3 and d4 (40 MW), so l23, a longer route to d3, is open. The tie between
    # the areas is open too, and the build's connections fail with their substation, so they have no row.
    assert table["unmet_peak_mw"].to_dict() == pytest.approx(
        {"l12": 10.0, "l13": 40.0, "l23": 0.0, "l34": 20.0, "tie": 0.0}
    )
    assert table["in_service"].to_dict() == {"l12": True, "l13": True, "l23": False, "l34": True, "tie": False}
    assert table.at["l13", "unmet_average_mw"] == pytest.approx(20.0)
    assert table.at["tie", "area_bus_id"] == ""
    assert not_joined == {"peak": 0.0, "average": 0.0}
    assert counts == {"distribution_lines": 5, "in_service": 3, "between_areas": 1, "connections_added_by_the_build": 2}

    # A 30 MW thermal plant at d4 keeps d4 supplied, and supplies 30 of the 40 MW beyond l13.
    network.import_components_from_dataframe(
        pd.DataFrame({"bus": ["d4"], "p_nom": [30.0], "carrier": ["thermal"]}, index=["plant_d4"]), "Generator"
    )
    with_plant, _, _ = distribution_failures(
        network.lines, network.generators, weights, demand, island_carriers=ISLAND_CARRIERS
    )
    assert with_plant.loc[["l13", "l34"], "unmet_peak_mw"].tolist() == pytest.approx([10.0, 0.0])


def test_a_failure_starts_from_the_supply_with_nothing_failed():
    network, weights = _feeders()
    demand = {"peak": pd.Series({"mauritius": 100.0}), "average": pd.Series({"mauritius": 50.0})}
    # With nothing failed, the transmission network supplies only half of S's area at peak.
    supplied = {"peak": pd.Series({"bus::S": 0.5, "bus::T": 1.0}), "average": pd.Series({"bus::S": 1.0})}

    table, _, _ = distribution_failures(
        network.lines, network.generators, weights, demand, island_carriers=ISLAND_CARRIERS, supplied_share=supplied
    )

    assert table.at["l13", "unmet_peak_mw"] == pytest.approx(20.0)
    assert table.at["l13", "unmet_average_mw"] == pytest.approx(20.0)
