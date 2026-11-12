import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from energy.osm import (
    REGION_GROUPS,
    REGIONS,
    OSMDownloadRequired,
    deduplicate_two_way_roads,
    fetch_osm_aoi,
    fetch_osm_power_features,
    fetch_osm_roads,
    osm_aoi_path,
    osm_power_path,
    osm_roads_path,
    region_members,
    region_query,
    region_slug,
    roads_cache_relative,
)


def test_region_shortcuts_and_paths(tmp_path):
    assert {"rodrigues", "agalega", "st_brandon"} <= set(REGIONS)
    assert region_query("mauritius") == "Mauritius Island, Mauritius"
    # Any query is accepted and turned into a file-name-safe key.
    assert region_query("Rodrigues, Mauritius") == "Rodrigues, Mauritius"
    assert region_slug("Rodrigues, Mauritius") == "rodrigues_mauritius"
    assert region_slug("drive_service") == "drive_service"
    assert (
        roads_cache_relative("Rodrigues").as_posix()
        == "incoming/Infrastructure/Energy/OpenStreetMap/rodrigues/roads.parquet"
    )
    assert osm_roads_path("Rodrigues", data_root=tmp_path) == tmp_path / roads_cache_relative("Rodrigues")
    assert osm_roads_path("Rodrigues", "all", data_root=tmp_path).name == "roads-all.parquet"
    assert osm_power_path("Rodrigues", data_root=tmp_path).name == "power.parquet"
    assert osm_aoi_path("Rodrigues", data_root=tmp_path).name == "aoi.parquet"
    assert region_members("mauritius-rodrigues") == ("mauritius", "rodrigues")
    assert region_slug("mauritius-rodrigues") == "mauritius-rodrigues"
    assert "mauritius-rodrigues" in REGION_GROUPS


def test_uncached_region_requires_download(tmp_path):
    with pytest.raises(OSMDownloadRequired, match="fetch_energy_osm"):
        fetch_osm_roads("Nowhere Test Region 99999", data_root=tmp_path)
    with pytest.raises(OSMDownloadRequired):
        fetch_osm_power_features("Nowhere Test Region 99999", data_root=tmp_path)
    with pytest.raises(OSMDownloadRequired):
        fetch_osm_aoi("Nowhere Test Region 99999", data_root=tmp_path)


def test_fetch_osm_roads_reads_explicit_path(tmp_path):
    """An explicit ``path`` (e.g. a configured energy.osm.roads) is used as-is."""
    roads = gpd.GeoDataFrame(
        {
            "source": ["osm_roads"],
            "region": ["rodrigues"],
            "highway": ["residential"],
            "geometry": [LineString([(63.42, -19.72), (63.421, -19.72)])],
        },
        crs="EPSG:4326",
    )
    supplied = tmp_path / "user_roads.parquet"
    roads.to_parquet(supplied)

    assert fetch_osm_roads("Rodrigues", path=supplied, data_root=tmp_path) == supplied


def test_fetch_osm_roads_explicit_missing_path_is_reported(tmp_path):
    """A configured roads path that does not exist is reported, never downloaded."""
    with pytest.raises(FileNotFoundError):
        fetch_osm_roads("Rodrigues", path=tmp_path / "absent.parquet", data_root=tmp_path)


def test_two_way_roads_are_deduplicated():
    forward = LineString([(57.5, -20.2), (57.51, -20.2)])
    backward = LineString([(57.51, -20.2), (57.5, -20.2)])
    parallel = LineString([(57.5, -20.2), (57.505, -20.201), (57.51, -20.2)])
    roads = gpd.GeoDataFrame({"geometry": [forward, backward, parallel, forward]}, crs="EPSG:4326")

    kept = deduplicate_two_way_roads(roads)

    assert len(kept) == 2
    assert kept.geometry.iloc[0].equals(forward)
    assert kept.geometry.iloc[1].equals(parallel)


def test_fetch_osm_roads_preserves_highway_class_and_drops_reverse_twins(monkeypatch, tmp_path):
    ox = pytest.importorskip("osmnx")

    edges = gpd.GeoDataFrame(
        {
            # osmnx gives a string for most ways, a list for merged edges and None if missing.
            "highway": ["residential", ["tertiary", "service"], "Primary", None, "residential"],
            "geometry": [
                LineString([(57.50, -20.20), (57.501, -20.20)]),
                LineString([(57.51, -20.21), (57.511, -20.21)]),
                LineString([(57.52, -20.22), (57.521, -20.22)]),
                LineString([(57.53, -20.23), (57.531, -20.23)]),
                LineString([(57.501, -20.20), (57.50, -20.20)]),  # reverse twin of the first
            ],
        },
        crs="EPSG:4326",
    )

    monkeypatch.setattr(ox, "graph_from_place", lambda query, network_type: object())
    monkeypatch.setattr(ox.convert, "to_undirected", lambda graph: graph)
    monkeypatch.setattr(ox, "graph_to_gdfs", lambda graph, nodes: edges.copy())

    output = fetch_osm_roads("mauritius", network_type="drive", overwrite=True, allow_download=True, data_root=tmp_path)
    roads = gpd.read_parquet(output)

    assert output == osm_roads_path("mauritius", data_root=tmp_path)
    assert list(roads.columns) == ["source", "region", "highway", "geometry"]
    assert len(roads) == 4
    highway = list(roads["highway"])
    assert highway[:3] == ["residential", "tertiary", "primary"]
    assert pd.isna(highway[3])
    assert set(roads["source"]) == {"osm_roads"}
    assert set(roads["region"]) == {"mauritius"}


def test_fetch_osm_power_features_handles_osmnx_multiindex(monkeypatch, tmp_path):
    ox = pytest.importorskip("osmnx")

    index = pd.MultiIndex.from_tuples(
        [("way", 1001), ("node", 2002)],
        names=["element_type", "osmid"],
    )
    features = gpd.GeoDataFrame(
        {"power": ["substation", "generator"], "name": ["Fort George", None], "operator": ["CEB", None]},
        geometry=[Point(57.55, -20.25), Point(57.58, -20.29)],
        crs="EPSG:4326",
        index=index,
    )

    def fake_features_from_place(query, tags):
        assert query == "Mauritius Island, Mauritius"
        assert tags == {"power": ["substation", "plant", "generator"]}
        return features

    monkeypatch.setattr(ox, "features_from_place", fake_features_from_place)

    path = fetch_osm_power_features("mauritius", overwrite=True, allow_download=True, data_root=tmp_path)

    power = gpd.read_parquet(path)
    assert list(power["bus_id"]) == ["MAURITIUS_SUB_001", "MAURITIUS_SUB_002"]
    assert list(power["power"]) == ["substation", "generator"]
    assert power["name"].iloc[0] == "Fort George" and power["operator"].iloc[0] == "CEB"
    assert power["name"].isna().iloc[1]
    assert power["plant_source"].isna().all()
    assert power.crs == "EPSG:4326"


def _write_member_caches(tmp_path, members):
    for region, longitude in members:
        roads_path = osm_roads_path(region, data_root=tmp_path)
        roads_path.parent.mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame(
            {
                "source": ["osm_roads"],
                "region": [region],
                "geometry": [LineString([(longitude, -20.0), (longitude + 0.001, -20.0)])],
            },
            crs="EPSG:4326",
        ).to_parquet(roads_path)
        gpd.GeoDataFrame(
            {
                "source": ["osm_power"],
                "region": [region],
                "bus_id": [f"{region.upper()}_SUB_001"],
                "power": ["substation"],
                "geometry": [Point(longitude, -20.0)],
            },
            crs="EPSG:4326",
        ).to_parquet(osm_power_path(region, data_root=tmp_path))


def test_composite_region_combines_cached_roads_and_power(tmp_path):
    _write_member_caches(tmp_path, (("mauritius", 57.5), ("rodrigues", 63.4)))

    roads = gpd.read_parquet(fetch_osm_roads("mauritius-rodrigues", data_root=tmp_path))
    power = gpd.read_parquet(fetch_osm_power_features("mauritius-rodrigues", data_root=tmp_path))

    assert len(roads) == 2
    assert set(roads["region"]) == {"mauritius", "rodrigues"}
    assert set(power["bus_id"]) == {"MAURITIUS_SUB_001", "RODRIGUES_SUB_001"}


def test_group_power_fetch_needs_every_member_before_writing(tmp_path):
    """A group cache is never written from a subset of its members."""
    _write_member_caches(tmp_path, (("mauritius", 57.5),))

    with pytest.raises(OSMDownloadRequired):
        fetch_osm_power_features("mauritius-rodrigues", data_root=tmp_path)

    assert not osm_power_path("mauritius-rodrigues", data_root=tmp_path).exists()


def test_fetch_osm_aoi_combines_members(monkeypatch, tmp_path):
    ox = pytest.importorskip("osmnx")
    outlines = {
        "Mauritius Island, Mauritius": box(57.3, -20.6, 57.9, -19.9),
        "Rodrigues, Mauritius": box(63.3, -19.8, 63.5, -19.6),
    }
    monkeypatch.setattr(
        ox, "geocode_to_gdf", lambda query: gpd.GeoDataFrame({"geometry": [outlines[query]]}, crs="EPSG:4326")
    )

    path = fetch_osm_aoi("mauritius-rodrigues", allow_download=True, data_root=tmp_path)
    aoi = gpd.read_parquet(path)

    assert path == osm_aoi_path("mauritius-rodrigues", data_root=tmp_path)
    assert list(aoi["region"]) == ["mauritius", "rodrigues"]
    assert set(aoi["source"]) == {"osm_nominatim"}
    assert aoi.geometry.geom_type.eq("Polygon").all()
