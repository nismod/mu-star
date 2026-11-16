import geopandas
import pandas as pd
import pytest
from shapely.geometry import Point

from transport import open_osm as o


def way(way_id, node_ids, **tags):
    """A way along the x axis: node n sits at lon = n / 1000."""
    tags = {"highway": "residential", **tags}
    return {"id": way_id, "tags": tags, "nodes": [(n, n / 1000.0, 0.0) for n in node_ids]}


# --- roads --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "motorway_link"}, "motorway"),
        ({"highway": "trunk_link"}, "trunk"),
        ({"highway": "tertiary_link"}, "tertiary"),
        ({"highway": "living_street"}, "residential"),
        ({"highway": "service"}, "service"),
        ({"highway": "footway"}, None),
        ({"highway": "track"}, None),
        ({"highway": "construction"}, None),
        ({"highway": "residential", "access": "private"}, None),
        ({"highway": "residential", "access": "no"}, None),
        ({"highway": "residential", "access": "no", "motor_vehicle": "yes"}, "residential"),
        ({"highway": "residential", "motor_vehicle": "no"}, None),
        ({"highway": "service", "service": "emergency_access"}, None),
        ({"highway": "residential", "area": "yes"}, None),
        ({}, None),
    ],
)
def test_road_class(tags, expected):
    assert o.road_class(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"oneway": "yes"}, "forward"),
        ({"oneway": "true"}, "forward"),
        ({"oneway": "1"}, "forward"),
        ({"oneway": "-1"}, "reverse"),
        ({"oneway": "no"}, "both"),
        ({"oneway": "reversible"}, "both"),
        ({"junction": "roundabout"}, "forward"),
        ({"junction": "roundabout", "oneway": "no"}, "both"),
        ({"highway": "motorway"}, "forward"),
        ({"highway": "motorway_link"}, "forward"),
        ({"highway": "motorway", "oneway": "no"}, "both"),
        ({"highway": "residential"}, "both"),
    ],
)
def test_oneway_direction(tags, expected):
    assert o.oneway_direction(tags) == expected


def test_parse_speed_and_lanes():
    assert o.parse_speed_kph("50") == 50
    assert o.parse_speed_kph("50 km/h") == 50
    assert o.parse_speed_kph("30 mph") == pytest.approx(48.28, abs=0.01)
    assert pd.isna(o.parse_speed_kph("signals"))
    assert pd.isna(o.parse_speed_kph(None))
    assert o.parse_lanes("2") == 2
    assert o.parse_lanes("2;3") is None


def test_split_way_cuts_at_junctions_and_ends():
    assert o.split_way([1, 2, 3, 4, 5], {3}) == [(0, 2), (2, 4)]
    assert o.split_way([1, 2, 3], set()) == [(0, 2)]
    assert o.split_way([1, 2, 3], {1, 3}) == [(0, 2)]  # ends are not cut twice


def test_edges_are_noded_at_shared_nodes():
    # way 1 runs 1-2-3-4-5; way 2 joins it at node 3 from the side; way 3 is a footway (dropped)
    ways = [
        way(1, [1, 2, 3, 4, 5]),
        {"id": 2, "tags": {"highway": "service"}, "nodes": [(3, 0.003, 0.0), (9, 0.003, 0.001)]},
        way(3, [5, 6], highway="footway"),
    ]
    edges = o.edges_from_ways(ways)
    assert sorted(edges["edge_id"]) == ["1_0", "1_1", "2_0"]
    by_id = edges.set_index("edge_id")
    assert (by_id.at["1_0", "from_node"], by_id.at["1_0", "to_node"]) == ("1", "3")
    assert (by_id.at["1_1", "from_node"], by_id.at["1_1", "to_node"]) == ("3", "5")
    assert by_id.at["2_0", "road_class"] == "service"
    assert len(by_id.at["1_0", "geometry"].coords) == 3
    assert not by_id["oneway"].any()


def test_closed_way_with_one_junction_gives_loop_that_is_dropped():
    ways = [way(1, [1, 2, 3, 1]), way(2, [1, 7])]
    edges = o.edges_from_ways(ways)
    # the loop 1-2-3-1 starts and ends at the shared node 1: a self loop, dropped
    assert list(edges["edge_id"]) == ["2_0"]


def test_oneway_minus_one_reverses_geometry_and_nodes():
    edges = o.edges_from_ways([way(1, [1, 2, 3], oneway="-1"), way(2, [4, 5], oneway="yes")])
    e = edges.set_index("edge_id")
    assert (e.at["1_0", "from_node"], e.at["1_0", "to_node"]) == ("3", "1")
    assert next(iter(e.at["1_0", "geometry"].coords)) == (0.003, 0.0)
    assert e.at["1_0", "oneway"]
    assert (e.at["2_0", "from_node"], e.at["2_0", "to_node"]) == ("4", "5")


def test_roundabout_and_attributes():
    edges = o.edges_from_ways(
        [way(1, [1, 2], junction="roundabout", maxspeed="40", lanes="2", bridge="viaduct", name="X"), way(2, [2, 3])]
    )
    e = edges.set_index("edge_id")
    assert e.at["1_0", "oneway"]
    assert e.at["1_0", "speed_kph"] == 40
    assert e.at["1_0", "lanes"] == 2
    assert e.at["1_0", "bridge"]
    assert e.at["1_0", "name"] == "X"
    assert e.at["1_0", "osm_id"] == 1
    assert pd.isna(e.at["2_0", "speed_kph"])
    assert not e.at["2_0", "bridge"]


def test_island_assignment():
    assert o.island_of(57.5, -20.2) == "mauritius"
    assert o.island_of(63.42, -19.68) == "rodrigues"
    assert o.island_of(56.6, -10.4) == "agalega"
    assert o.island_of(0, 0) is None


def test_largest_component_drops_the_rest():
    ways = [way(1, [1, 2, 3]), way(2, [3, 4]), way(3, [10, 11])]
    edges = o.edges_from_ways(ways)
    kept, dropped = o.largest_component(edges)
    assert set(kept["from_node"]) | set(kept["to_node"]) == {"1", "3", "4"}
    assert dropped["components_dropped"] == 1
    assert dropped["edges_dropped"] == 1


# --- metro --------------------------------------------------------------------


def test_merge_way_chain_orients_ways():
    coords = {n: (float(n), 0.0) for n in range(1, 8)}
    # ways given in route order but individually reversed
    chain = o.merge_way_chain_ids([[3, 2, 1], [3, 4, 5], [6, 5]], coords)
    assert chain == [1, 2, 3, 4, 5, 6]


def test_station_matching_and_order():
    stations = [
        {"station_id": "rose-hill-central", "name": "Rose Hill Central", "xy": (57.4759, -20.2418)},
        {"station_id": "other", "name": "Other", "xy": (57.4759, -20.2417)},
    ]
    assert o.match_station("Rose Hill", (57.4761, -20.2417), stations)["station_id"] == "rose-hill-central"
    assert o.match_station("Nowhere", (58.0, -20.0), stations) is None
    order = o.line_station_order([stations[0], stations[0], stations[1]])
    assert [s["station_id"] for s in order] == ["rose-hill-central", "other"]
    assert o.slugify("Ébène Cybercity") == "ebene-cybercity"


def test_extension_path_along_disused_track():
    coords = {1: (57.0, -20.0), 2: (57.001, -20.0), 3: (57.002, -20.0)}
    path = o.extension_path([[1, 2], [2, 3]], coords, 1, (57.002, -20.0))
    assert path == [1, 2, 3]
    assert o.extension_path([[1, 2]], coords, 1, (58.0, -20.0)) is None


# --- schools ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "level", "how"),
    [
        ({"name": "Cottage Government School", "isced:level": "1"}, "primary", "isced"),
        ({"name": "Cosmopolitan College Girls", "isced:level": "2"}, "secondary", "isced"),
        ({"name": "Lycée Labourdonnais", "isced:level": "2;3"}, "secondary", "isced"),
        ({"name": "Royal College Port Louis"}, "secondary", "name"),
        ({"name": "Bel Air State Secondary School"}, "secondary", "name"),
        ({"name": "Ramsoondur Prayag SSS"}, "secondary", "name"),
        ({"name": "Sir Guy Forget Lycee Polytechnique"}, "secondary", "name"),
        ({"name": "Mesnil Government School"}, "primary", "name"),
        ({"name": "Reunion Governement School"}, "primary", "name"),
        ({"name": "Philippe Rivalland RCA"}, "primary", "name"),
        ({"name": "Le Nid Primary School"}, "primary", "name"),
        ({"name": "Lighthouse Primary and Secondary School"}, "primary", "default"),
        ({"name": "Ming Tek Centre"}, "primary", "default"),
        ({}, "primary", "default"),
        ({"name": "Les papillons école maternelle"}, None, "excluded"),
        ({"name": "Charles Telfair Institute"}, None, "excluded"),
        ({"name": "Something", "isced:level": "0"}, None, "excluded"),
        ({"name": "École du Centre - Collège Pierre Poivre", "isced:level": "1;2"}, "secondary", "name"),
    ],
)
def test_classify_school(tags, level, how):
    assert o.classify_school(tags) == (level, how)


def test_school_name_matching():
    assert o.names_similar("Barlow Govt School", "BARLOW Government School")
    assert o.names_similar("Petit Verger Government school", "PETIT VERGER Government School")
    assert not o.names_similar("Barlow Government School", "Cottage Government School")
    assert not o.names_similar("", "Cottage Government School")
    assert o.clean_official_name("�cole du Centre") == "École du Centre"


def test_match_official_by_distance_and_name():
    official = geopandas.GeoDataFrame(
        {"name": ["Barlow Government School", "Cottage Government School"]},
        geometry=[Point(0, 0), Point(1000, 0)],
        crs=32740,
    )
    assert o.match_official("Barlow Govt School", (100, 0), official) == 0
    assert o.match_official("Barlow Govt School", (250, 0), official) == 0  # identical core name, within 300 m
    assert o.match_official("Other Government School", (100, 0), official) is None  # near but different
    assert o.match_official("", (100, 0), official) == 0  # unnamed and near
    assert o.match_official("Cottage Govt School", (0, 0), official) is None


def test_aerodrome_id_prefers_iata():
    assert o.aerodrome_id({"iata": "MRU", "icao": "FIMP"}, 1) == "MRU"
    assert o.aerodrome_id({"icao": "FIMA", "name": "x"}, 1) == "FIMA"
    assert o.aerodrome_id({"name": "Some Strip"}, 1) == "some-strip"
