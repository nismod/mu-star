import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from energy.osm import (
    REGION_GROUPS,
    REGIONS,
    OSMDownloadRequired,
    fetch_osm_power_features,
    fetch_osm_roads,
    osm_power_path,
    osm_roads_path,
    region_members,
    region_query,
    region_slug,
)


def test_region_shortcuts_and_paths():
    assert {"rodrigues", "agalega", "st_brandon"} <= set(REGIONS)
    assert region_query("mauritius") == "Mauritius Island, Mauritius"
    # Open-ended: any query is accepted, and slugged for cache/output paths.
    assert region_query("Rodrigues, Mauritius") == "Rodrigues, Mauritius"
    assert region_slug("Rodrigues, Mauritius") == "rodrigues_mauritius"
    assert osm_roads_path("Rodrigues").name == "roads.parquet"
    assert osm_roads_path("Rodrigues").parent.name == "rodrigues"
    assert osm_roads_path("Rodrigues", "all").name == "roads-all.parquet"
    assert osm_roads_path("Rodrigues", "drive") != osm_roads_path("Rodrigues", "all")
    assert osm_power_path("Rodrigues").name == "power.parquet"
    assert region_members("mauritius-rodrigues") == ("mauritius", "rodrigues")
    assert region_slug("mauritius-rodrigues") == "mauritius-rodrigues"
    assert "mauritius-rodrigues" in REGION_GROUPS


def test_uncached_region_requires_download():
    with pytest.raises(OSMDownloadRequired):
        fetch_osm_roads("Nowhere Test Region 99999")
    with pytest.raises(OSMDownloadRequired):
        fetch_osm_power_features("Nowhere Test Region 99999")


def test_fetch_osm_roads_preserves_highway_class(monkeypatch, tmp_path):
    ox = pytest.importorskip("osmnx")
    monkeypatch.setenv("MU_STAR_DATA_ROOT", str(tmp_path))

    edges = gpd.GeoDataFrame(
        {
            # osmnx yields a plain string for most ways and a list for merged
            # edges; missing tags come through as None.
            "highway": ["residential", ["tertiary", "service"], "Primary", None],
            "geometry": [
                LineString([(57.50, -20.20), (57.501, -20.20)]),
                LineString([(57.51, -20.21), (57.511, -20.21)]),
                LineString([(57.52, -20.22), (57.521, -20.22)]),
                LineString([(57.53, -20.23), (57.531, -20.23)]),
            ],
        },
        crs="EPSG:4326",
    )

    monkeypatch.setattr(ox, "graph_from_place", lambda query, network_type: object())
    monkeypatch.setattr(ox, "graph_to_gdfs", lambda graph, nodes: edges.copy())

    output = fetch_osm_roads("mauritius", network_type="drive", overwrite=True, allow_download=True)
    roads = gpd.read_parquet(output.path)

    assert "highway" in roads.columns
    assert list(roads.columns) == ["source", "region", "highway", "geometry"]
    highway = list(roads["highway"])
    assert highway[:3] == ["residential", "tertiary", "primary"]
    assert pd.isna(highway[3])
    assert set(roads["source"]) == {"osm_roads"}
    assert set(roads["region"]) == {"mauritius"}
    assert output.path.name == "roads.parquet"


def test_fetch_osm_power_features_handles_osmnx_multiindex(monkeypatch, tmp_path):
    ox = pytest.importorskip("osmnx")

    monkeypatch.setenv("MU_STAR_DATA_ROOT", str(tmp_path))
    index = pd.MultiIndex.from_tuples(
        [("way", 1001), ("node", 2002)],
        names=["element_type", "osmid"],
    )
    features = gpd.GeoDataFrame(
        {"power": ["substation", "generator"]},
        geometry=[Point(57.55, -20.25), Point(57.58, -20.29)],
        crs="EPSG:4326",
        index=index,
    )

    def fake_features_from_place(query, tags):
        assert query == "Mauritius Island, Mauritius"
        assert tags == {"power": ["substation", "plant", "generator"]}
        return features

    monkeypatch.setattr(ox, "features_from_place", fake_features_from_place)

    path = fetch_osm_power_features(
        "mauritius",
        overwrite=True,
        allow_download=True,
    )

    power = gpd.read_parquet(path)
    assert list(power["bus_id"]) == ["MAURITIUS_SUB_001", "MAURITIUS_SUB_002"]
    assert list(power["power"]) == ["substation", "generator"]
    assert power.crs == "EPSG:4326"


def test_composite_region_combines_cached_roads_and_power(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_STAR_DATA_ROOT", str(tmp_path))
    for region, longitude in (("mauritius", 57.5), ("rodrigues", 63.4)):
        roads_path = osm_roads_path(region)
        roads_path.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(
            {
                "source": ["osm_roads"],
                "region": [region],
                "geometry": [LineString([(longitude, -20.0), (longitude + 0.001, -20.0)])],
            },
            crs="EPSG:4326",
        ).to_parquet(roads_path)

        power_path = osm_power_path(region)
        gpd.GeoDataFrame(
            {
                "source": ["osm_power"],
                "region": [region],
                "bus_id": [f"{region.upper()}_SUB_001"],
                "power": ["substation"],
                "geometry": [Point(longitude, -20.0)],
            },
            crs="EPSG:4326",
        ).to_parquet(power_path)

    roads_output = fetch_osm_roads("mauritius-rodrigues")
    power_output = fetch_osm_power_features("mauritius-rodrigues")
    roads = gpd.read_parquet(roads_output.path)
    power = gpd.read_parquet(power_output)

    assert roads_output.edge_count == 2
    assert set(roads["region"]) == {"mauritius", "rodrigues"}
    assert set(power["bus_id"]) == {"MAURITIUS_SUB_001", "RODRIGUES_SUB_001"}
