"""Tests of splitting assets once on all hazard grids."""

import subprocess
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import LineString, MultiLineString, Point, box
from snail.intersection import GridDefinition
from snail.overlay import split_features

from damages.geozarr import grid_from_geozarr_attrs
from damages.intensity import split_intensity
from damages.split import (
    find_grid_id,
    grid_definitions,
    grid_extent,
    grids_match,
    index_columns,
    read_grids_metadata,
    split_on_grids,
    unique_grids,
    write_grids_metadata,
)
from damages.tests.conftest import REPO

CRS = "EPSG:32740"


def grid(x0=0.0, y0=400.0, n=4, cell=100.0, crs=CRS) -> GridDefinition:
    """An n x n grid with top left corner (x0, y0)."""
    return GridDefinition(crs=crs, width=n, height=n, transform=(cell, 0.0, x0, 0.0, -cell, y0))


def assets(*geometries, crs=CRS) -> gpd.GeoDataFrame:
    n = len(geometries)
    return gpd.GeoDataFrame(
        {"asset_id": [f"a{k}" for k in range(n)], "asset_type": "x", "geometry": list(geometries)}, crs=crs
    )


def cells(splits: gpd.GeoDataFrame, g: int) -> list[tuple[int, int]]:
    i_col, j_col = index_columns(f"g{g}")
    return list(zip(splits[i_col], splits[j_col]))


def test_line_on_two_offset_grids_is_split_in_three():
    # a line from x = 60 to 190 at y = 350 (row 0), 130 m long. The first grid has cell edges at x = 0, 100, 200,
    # so splits it in two, at 100. The second has edges at -50, 50, 150, 250, so splits it in two, at 150.
    line = LineString([(60, 350), (190, 350)])
    first, second = grid(0), grid(-50)
    assert len(split_features(assets(line), first)) == 2
    assert len(split_features(assets(line), second)) == 2

    splits, definitions = split_on_grids(assets(line), [first, second])
    assert [d["id"] for d in definitions] == ["g0", "g1"]
    assert len(splits) == 3
    np.testing.assert_allclose(sorted(splits.length_m), [40, 40, 50])
    assert splits.length_m.sum() == pytest.approx(130)
    assert splits.share.sum() == pytest.approx(1)
    assert splits.split_id.tolist() == [0, 1, 2]
    # in order along the line: x 60 to 100 is cell 0 of the first grid and 1 of the second (-50 to 50 is cell 0)
    by_x = splits.assign(x=splits.geometry.centroid.x).sort_values("x")
    assert cells(by_x, 0) == [(0, 0), (1, 0), (1, 0)]
    assert cells(by_x, 1) == [(1, 0), (1, 0), (2, 0)]
    np.testing.assert_allclose(by_x.length_m, [40, 50, 40])
    # each piece is within one cell of each grid
    for g, a_grid in enumerate([first, second]):
        i_col, j_col = index_columns(f"g{g}")
        a, _, x0, _, e, y0 = a_grid.transform
        for row in splits.itertuples():
            cell = box(
                x0 + a * getattr(row, i_col),
                y0 + e * (getattr(row, j_col) + 1),
                x0 + a * (getattr(row, i_col) + 1),
                y0 + e * getattr(row, j_col),
            )
            assert cell.buffer(1e-6).contains(row.geometry)


def test_pieces_are_common_refinement_for_crossing_lines_and_keep_parent_attributes():
    lines = assets(LineString([(10, 350), (390, 250)]), LineString([(250, 10), (250, 390)]))
    lines["name"] = ["first", "second"]
    first, second = grid(0), grid(30, 420)
    splits, _ = split_on_grids(lines, [first, second])
    assert splits.name.tolist().count("first") == (splits.asset_id == "a0").sum()
    for asset_id, original in lines.set_index("asset_id").geometry.items():
        rows = splits[splits.asset_id == asset_id]
        assert rows.length_m.sum() == pytest.approx(original.length)
        assert rows.share.sum() == pytest.approx(1)
        assert rows.geometry.union_all().length == pytest.approx(original.length)
        # pieces are never more numerous than the cuts: no piece repeats a pair of cells
        assert not pd.Series(list(zip(cells(rows, 0), cells(rows, 1)))).duplicated().any()
    # the vertical line x = 250 from y = 10 to 390 is cut by the first grid's edges y = 300, 200, 100 and by the
    # second's y = 320, 220, 120, 20 (its top edge y = 420 is above the line): 7 cuts, 8 pieces
    assert (splits.asset_id == "a1").sum() == 8


def test_polygon_partly_outside_one_grid_but_inside_another_is_not_dropped():
    # grid a covers x in [0, 200], grid b covers x in [100, 300], both y in [0, 100]
    a, b = grid(0, 100, n=2), grid(100, 100, n=2)
    # (the 2 x 2 grids are 200 m square, so the second row is y in [-100, 0]); polygon x 50..250, y 10..90
    polygon = box(50, 10, 250, 90)
    splits, _ = split_on_grids(assets(polygon), [a, b])
    assert splits.area_m2.sum() == pytest.approx(polygon.area)
    assert splits.share.sum() == pytest.approx(1)
    assert splits.geometry.union_all().area == pytest.approx(polygon.area)
    # cuts at x = 100 (a), 200 (a, and b's x = 200 edge) and b's extent edge at 100: pieces 50-100, 100-200, 200-250
    by_x = splits.assign(x=splits.geometry.centroid.x).sort_values("x")
    np.testing.assert_allclose(by_x.area_m2, [50 * 80, 100 * 80, 50 * 80])
    assert cells(by_x, 0) == [(0, 0), (1, 0), (-1, -1)]
    assert cells(by_x, 1) == [(-1, -1), (0, 0), (1, 0)]


def test_polygon_wrapping_around_a_grid_is_outside_it():
    # a ring around the grid has a centroid inside it, but is outside
    ring = box(-100, -100, 500, 500).difference(box(-10, -10, 410, 410))
    splits, _ = split_on_grids(assets(ring), [grid()])
    assert len(splits) == 1
    assert cells(splits, 0) == [(-1, -1)]
    assert splits.area_m2.iloc[0] == pytest.approx(ring.area)


def test_line_outside_a_grid_is_not_cut_by_it():
    # outside the first grid, so one piece there, but the second grid (x -300 to 100, y 100 to 500) cuts the part of it
    # that it covers, at x = -200, and leaves the rest of the line, from x = -500 to -300, as one piece
    line = LineString([(-500, 350), (-150, 350)])
    splits, _ = split_on_grids(assets(line), [grid(), grid(-300, 500)])
    assert len(splits) == 3
    assert (splits.index_i_g0 < 0).all()
    assert sorted(splits.index_i_g1) == [-1, 0, 1]
    assert splits.length_m.sum() == pytest.approx(350)
    alone, _ = split_on_grids(assets(line), [grid()])
    assert len(alone) == 1 and alone.length_m.iloc[0] == pytest.approx(350)


def test_identical_grids_are_used_once():
    a = grid()
    near = GridDefinition(crs=CRS, width=4, height=4, transform=(100.0 * (1 + 1e-13), 0.0, 1e-9, 0.0, -100.0, 400.0))
    other = grid(50)
    unique, position = unique_grids([a, near, other, a])
    assert len(unique) == 2 and position == [0, 0, 1, 0]
    assert grids_match(a, near) and not grids_match(a, other)
    assert not grids_match(a, grid(n=3))
    assert not grids_match(a, grid(crs="EPSG:32741"))

    line = assets(LineString([(10, 350), (390, 350)]))
    splits, definitions = split_on_grids(line, [a, near, a])
    assert len(definitions) == 1
    assert "index_i_g0" in splits and "index_i_g1" not in splits
    assert len(splits) == 4
    two, definitions = split_on_grids(line, [a, near, other])
    assert [d["id"] for d in definitions] == ["g0", "g1"]
    assert "index_i_g1" in two


def test_single_grid_matches_snail_for_lines_and_points():
    lines = assets(
        LineString([(10, 350), (390, 50)]),
        LineString([(120, 20), (120, 380)]),
        LineString([(30, 30), (60, 60), (130, 40), (180, 260)]),
        Point(150, 150),
        Point(420, 20),  # outside
        Point(0, 0),  # on the grid's corner, below and left of the cell (0, 3): outside
    )
    g = grid()
    ours, definitions = split_on_grids(lines, [g])
    assert len(definitions) == 1
    theirs = split_features(lines.iloc[:3], g)
    ours_lines = ours[ours.geom_kind == "line"]
    assert len(ours_lines) == len(theirs)
    for (asset_id, i, j), group in ours_lines.groupby(["asset_id", "index_i_g0", "index_j_g0"]):
        match = theirs[(theirs.asset_id == asset_id) & (theirs.index_i == i) & (theirs.index_j == j)]
        assert len(match) == 1
        assert group.geometry.union_all().length == pytest.approx(match.geometry.iloc[0].length)
    # points are one piece each, with snail's indices (the corner point is on the outside edge of the grid)
    points = ours[ours.geom_kind == "point"]
    assert points.asset_id.tolist() == ["a3", "a4", "a5"]
    snail_points = split_features(lines.iloc[3:], g)
    assert cells(points, 0) == list(zip(snail_points.index_i, snail_points.index_j))
    assert cells(points, 0) == [(1, 2), (-1, -1), (-1, -1)]
    assert points.share.tolist() == [1.0, 1.0, 1.0]


def test_cell_edges_are_half_open():
    # as snail: a point on a cell's left or top edge belongs to the cell, on the grid's right or bottom edge to none
    points = assets(Point(100, 300), Point(400, 200), Point(300, 0), Point(0, 400))
    splits, _ = split_on_grids(points, [grid()])
    assert cells(splits, 0) == [(1, 1), (-1, -1), (-1, -1), (0, 0)]


def test_empty_and_mixed_layers():
    empty, definitions = split_on_grids(assets(), [grid(), grid(50)])
    assert len(empty) == 0 and len(definitions) == 2
    for column in ("index_i_g0", "index_j_g1", "split_id", "geom_kind", "length_m", "area_m2", "share"):
        assert column in empty
    mixed = assets(
        Point(150, 150),
        LineString([(10, 350), (390, 350)]),
        box(10, 10, 390, 90),
        MultiLineString([[(10, 200), (90, 200)], [(310, 200), (390, 200)]]),
    )
    splits, _ = split_on_grids(mixed, [grid(), grid(25, 375)])
    kinds = splits.groupby("asset_id").geom_kind.agg(lambda s: sorted(set(s))).to_dict()
    assert kinds == {"a0": ["point"], "a1": ["line"], "a2": ["polygon"], "a3": ["line"]}
    assert (splits.asset_id == "a0").sum() == 1
    for asset_id, original in mixed.set_index("asset_id").geometry.items():
        rows = splits[splits.asset_id == asset_id]
        if original.geom_type != "Point":
            assert (rows.length_m + rows.area_m2).sum() == pytest.approx(
                original.area if original.geom_type == "Polygon" else original.length
            )
        assert rows.share.sum() == pytest.approx(1)


def test_grid_in_a_different_crs():
    # grid in geographic coordinates, assets in web mercator
    g = GridDefinition(crs="EPSG:4326", width=4, height=4, transform=(1.0, 0.0, 10.0, 0.0, -1.0, 4.0))
    line = gpd.GeoSeries([LineString([(10.5, 0.5), (13.5, 0.5)])], crs="EPSG:4326").to_crs("EPSG:3857").iloc[0]
    splits, _ = split_on_grids(assets(line, crs="EPSG:3857"), [g])
    assert splits.crs == "EPSG:3857"
    assert len(splits) == 4
    assert sorted(splits.index_i_g0) == [0, 1, 2, 3]
    assert splits.length_m.sum() == pytest.approx(line.length * 1.0, rel=0.01)  # geodesic vs planar mercator


def test_grid_metadata_round_trip_and_matching(tmp_path):
    first, second = grid(), grid(50)
    splits, definitions = split_on_grids(assets(LineString([(10, 350), (390, 350)])), [first, second])
    path = tmp_path / "split.gpq"
    splits.to_parquet(path)
    write_grids_metadata(path, definitions)
    assert read_grids_metadata(path) == definitions
    assert definitions[0].keys() == {"id", "crs", "shape", "transform"}
    assert definitions[0]["shape"] == [4, 4] and definitions[1]["transform"][2] == 50.0
    assert gpd.read_parquet(path).crs == CRS  # still GeoParquet
    assert find_grid_id(definitions, first) == "g0"
    assert find_grid_id(definitions, grid(50)) == "g1"
    assert find_grid_id(definitions, grid(60)) is None
    with pytest.raises(KeyError, match="mu_star:grids"):
        splits.to_parquet(tmp_path / "plain.gpq")
        read_grids_metadata(tmp_path / "plain.gpq")


def test_grid_extent_rotated_and_plain():
    assert grid_extent(grid()).equals(box(0, 0, 400, 400))
    rotated = GridDefinition(crs=CRS, width=2, height=1, transform=(10.0, 5.0, 0.0, -5.0, 10.0, 0.0))
    assert grid_extent(rotated).area == pytest.approx(2 * 1 * (10 * 10 + 5 * 5))


def hazard_store(path, g: GridDefinition, offset: float):
    """Zarr store of a one-variable hazard on a grid, with value ``offset + 10 j + i`` in cell (i, j)."""
    from damages.geozarr import with_geozarr_attrs

    a, _, x0, _, e, y0 = g.transform
    values = offset + 10 * np.arange(g.height)[:, None] + np.arange(g.width)[None, :]
    ds = xr.Dataset(
        {"depth": (("return_period", "y", "x"), values[None].astype(float))},
        coords={
            "return_period": [10],
            "y": y0 + e * (np.arange(g.height) + 0.5),
            "x": x0 + a * (np.arange(g.width) + 0.5),
        },
    )
    with_geozarr_attrs(ds.rio.write_crs(g.crs)).to_zarr(path, mode="w", consolidated=False)


def test_intensity_lookup_uses_the_matching_grid(tmp_path):
    first, second = grid(0, 400), grid(-50, 400)
    hazard_first, hazard_second = tmp_path / "first.zarr", tmp_path / "second.zarr"
    hazard_store(hazard_first, first, 100)
    hazard_store(hazard_second, second, 200)
    layer = assets(LineString([(60, 350), (190, 350)]), Point(120, 220))
    splits, definitions = split_on_grids(layer, [first, second])
    path = tmp_path / "split.gpq"
    splits.to_parquet(path)
    write_grids_metadata(path, definitions)

    for hazard_path, expected_id, offset in [(hazard_first, "g0", 100), (hazard_second, "g1", 200)]:
        store = xr.open_zarr(hazard_path, consolidated=False)
        found = find_grid_id(read_grids_metadata(path), grid_from_geozarr_attrs(store.attrs))
        assert found == expected_id
        i_col, j_col = index_columns(found)
        intensity = split_intensity(store.depth, pd.read_parquet(path), index_i=i_col, index_j=j_col)
        want = offset + 10 * splits[j_col].to_numpy() + splits[i_col].to_numpy()
        np.testing.assert_allclose(intensity.sel(return_period=10).to_numpy(), want)
    # the grids differ, so the same splits have different cells and so values on each
    assert cells(splits, 0) != cells(splits, 1)

    # the script, run directly, does the same and refuses a hazard on another grid
    third = tmp_path / "third.zarr"
    hazard_store(third, grid(10, 400), 0)
    script = REPO / "workflow" / "1-damage" / "damage_intensity.py"

    def run(hazard, output):
        args = ["--splits", path, "--hazard", hazard, "--variable", "depth", "--output", output]
        return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)

    ok = run(hazard_second, tmp_path / "ok.zarr")
    assert ok.returncode == 0, ok.stderr
    values = xr.open_zarr(tmp_path / "ok.zarr", consolidated=False).intensity.sel(return_period=10).to_numpy()
    np.testing.assert_allclose(values, 200 + 10 * splits.index_j_g1.to_numpy() + splits.index_i_g1.to_numpy())
    bad = run(third, tmp_path / "bad.zarr")
    assert bad.returncode != 0
    assert "matches none of the 2 grids" in bad.stderr
    assert "Re-run damage_split.py" in bad.stderr


def test_definitions_are_json_ready():
    import json

    json.dumps(grid_definitions([grid()]))
