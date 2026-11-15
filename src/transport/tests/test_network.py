import geopandas
import pandas as pd
import pytest
from shapely.geometry import Point

from transport.network import car_connectors, road_links, zones_without_car_access

CONNECTORS = {"car_k": 2, "car_max_km": 1.0, "car_speed_kph": 30}


def nodes():
    return geopandas.GeoDataFrame(
        {"node_id": ["a", "b", "c"], "geometry": [Point(0, 0), Point(500, 0), Point(5000, 0)]}
    )


def zones():
    return geopandas.GeoDataFrame(
        {"zone_id": ["near", "far"], "jobs": [10.0, 3.0], "geometry": [Point(0, 300), Point(0, 3000)]}
    )


def test_road_links_both_directions_unless_oneway():
    roads = pd.DataFrame(
        {
            "edge_id": ["e1", "e2"],
            "from_node": ["a", "b"],
            "to_node": ["b", "c"],
            "oneway": [False, True],
            "length_km": [0.5, 4.5],
            "time_min": [1.0, 9.0],
        }
    )
    links = road_links(roads)
    assert sorted(links.edge_id) == ["road/e1/ab", "road/e1/ba", "road/e2/ab"]
    back = links.set_index("edge_id").loc["road/e1/ba"]
    assert (back.edge_from, back.edge_to) == ("road/b", "road/a")


def test_car_connectors_and_missing_zones():
    links = car_connectors(zones(), nodes(), CONNECTORS)
    # "near" reaches a (300 m) and b (583 m); "far" (3 km away) reaches nothing.
    assert set(links.edge_from) == {"zone/near/car", "road/a", "road/b"}
    inbound = links[links.edge_id.str.endswith("/in")].set_index("edge_to")
    assert inbound.loc["road/a", "time_min"] == pytest.approx(0.3 / 30 * 60)

    missing = zones_without_car_access(zones(), nodes(), CONNECTORS)
    assert missing.zone_id.tolist() == ["far"]
    assert missing.loc[0, "nearest_road_node"] == "a"
    assert missing.loc[0, "nearest_km"] == pytest.approx(3.0)
    assert missing.loc[0, "jobs"] == 3.0
