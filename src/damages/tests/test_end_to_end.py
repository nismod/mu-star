"""End-to-end tests of the damage scripts on a tiny synthetic dataset with hand-checkable numbers.

See ``synthetic.py`` for the dataset. Uncertainty is off (zero coefficients of variation), so every
quantile equals the hand calculation exactly, apart from the float32 precision of intensity.
"""

import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from scipy import integrate

from damages.tests import synthetic as syn
from damages.tests.conftest import REPO

WORKFLOW = REPO / "workflow" / "1-damage"
PERIL, SUBPERIL = "flood", "synthetic"
LAYER_NAMES = ["line", "polygon", "point", "mixed"]
N_SAMPLES = 20
RTOL = 1e-5  # intensity is stored as float32
DIMS = ("epoch", "return_period", "quantile")


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, str(WORKFLOW / name), *map(str, args)], capture_output=True, text=True, cwd=REPO
    )


def write_inputs(data: Path, units: str = "m") -> dict[str, Path]:
    """Write hazard store, asset layers, workbook and metadata tables under ``data`` (a data root)."""
    (data / "processed" / "asset").mkdir(parents=True, exist_ok=True)
    hazard = data / "processed" / "hazard.zarr"
    syn.hazard_dataset(units).to_zarr(hazard, mode="w", consolidated=False)
    for name, layer in syn.layers().items():
        layer.to_parquet(data / "processed" / "asset" / f"layer-{name}.gpq")
    workbook = data.parent / "workbook.xlsx"
    syn.write_workbook(workbook)
    layers_csv = data.parent / "layer.csv"
    layers_csv.write_text("layer,path\n" + "".join(f"{n},processed/asset/layer-{n}.gpq\n" for n in LAYER_NAMES))
    hazards_csv = data.parent / "hazard.csv"
    hazards_csv.write_text(
        f"peril,subperil,path,variable,hazard\n{PERIL},{SUBPERIL},processed/hazard.zarr,depth,{syn.HAZARD_KEY}\n"
    )
    return {"workbook": workbook, "layers": layers_csv, "hazards": hazards_csv, "hazard_store": hazard}


def output_dir(data: Path, layer: str) -> Path:
    return data / "results" / "damage" / f"layer-{layer}" / "rp" / f"peril-{PERIL}" / f"subperil-{SUBPERIL}"


@dataclass
class Layer:
    name: str
    assets: gpd.GeoDataFrame  # input
    expected: syn.Expected
    split: gpd.GeoDataFrame
    intensity: xr.DataArray
    split_damage: xr.Dataset
    asset_damage: xr.Dataset
    type_damage: xr.Dataset


@dataclass
class Dataset:
    data: Path
    inputs: dict[str, Path]
    layers: dict[str, Layer]
    summary: pd.DataFrame
    split_logs: dict[str, str]

    def damage_args(self, layer: str, output: Path, *extra) -> list:
        out = output_dir(self.data, layer)
        return [
            "--splits", out / "split.gpq",
            "--intensity", out / "intensity.zarr",
            "--damage-config", self.inputs["workbook"],
            "--hazard", syn.HAZARD_KEY,
            "--n-samples", N_SAMPLES,
            "--output", output,
            *extra,
        ]  # fmt: skip


def _pipeline(data: Path, workbook: Path, layer: str) -> str:
    """Run split, intensity and calculate for a layer. Returns the log of the split step."""
    out = output_dir(data, layer)
    out.mkdir(parents=True, exist_ok=True)
    hazard = data / "processed" / "hazard.zarr"
    asset_layer = data / "processed" / "asset" / f"layer-{layer}.gpq"
    split, intensity, damage = out / "split.gpq", out / "intensity.zarr", out / "damage.zarr"
    steps = {
        "damage_split.py": ["--layer", asset_layer, "--hazard", hazard, "--measure-crs", syn.CRS, "--output", split],
        "damage_intensity.py": ["--splits", split, "--hazard", hazard, "--variable", "depth", "--output", intensity],
        "damage_calculate.py": [
            *["--splits", split, "--intensity", intensity, "--damage-config", workbook, "--hazard", syn.HAZARD_KEY],
            *["--n-samples", N_SAMPLES, "--seed", 1, "--output", damage],
        ],
    }
    logs = {}
    for script, args in steps.items():
        result = run_script(script, *args)
        assert result.returncode == 0, f"{script} on {layer}: {result.stderr}"
        logs[script] = result.stderr
    return logs["damage_split.py"]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Dataset:
    """Build the synthetic data, then run split, intensity and calculate for each layer, and summary."""
    data = tmp_path_factory.mktemp("synthetic") / "data"
    inputs = write_inputs(data)
    with ThreadPoolExecutor(len(LAYER_NAMES)) as pool:
        split_logs = dict(zip(LAYER_NAMES, pool.map(lambda x: _pipeline(data, inputs["workbook"], x), LAYER_NAMES)))
    paths = [output_dir(data, layer) / "damage.zarr" for layer in LAYER_NAMES]
    summary_path = data / "results" / "damage" / "summary.csv"
    args = [a for p in paths for a in ("--damage", p)]
    result = run_script("damage_summary.py", *args, "--output", summary_path)
    assert result.returncode == 0, result.stderr

    layers = {}
    for name, assets in syn.layers().items():
        out = output_dir(data, name)
        group = lambda g: xr.open_zarr(out / "damage.zarr", group=g, consolidated=False).load()  # noqa: E731
        layers[name] = Layer(
            name=name,
            assets=assets,
            expected=syn.expected_damages(assets),
            split=gpd.read_parquet(out / "split.gpq"),
            intensity=xr.open_zarr(out / "intensity.zarr", consolidated=False)["intensity"].load(),
            split_damage=group("split"),
            asset_damage=group("asset"),
            type_damage=group("asset_type"),
        )
    return Dataset(data, inputs, layers, pd.read_csv(summary_path), split_logs)


@pytest.fixture(params=LAYER_NAMES)
def layer(request, dataset) -> Layer:
    return dataset.layers[request.param]


def assert_quantiles_equal(actual: xr.DataArray, expected) -> None:
    """Every quantile of ``actual`` (with a trailing quantile dimension) equals ``expected``."""
    got = actual.transpose(..., "quantile").to_numpy()
    want = np.broadcast_to(np.asarray(expected, dtype=float)[..., None], got.shape)
    np.testing.assert_allclose(got, want, rtol=RTOL, atol=1e-9, equal_nan=True)


def split_rows(layer: Layer, calculated_only=True) -> list[int]:
    types = set(syn.COSTS) if calculated_only else set(layer.split.asset_type)
    return [k for k, t in enumerate(layer.split.asset_type) if t in types]


def expected_split_row(layer: Layer, k: int) -> pd.Series:
    row = layer.split.iloc[k]
    match = layer.expected.splits[
        (layer.expected.splits.asset_id == row.asset_id)
        & (layer.expected.splits.i == row.index_i)
        & (layer.expected.splits.j == row.index_j)
    ]
    assert len(match) == 1, (
        f"expected exactly one hand-calculated piece for {row.asset_id} in cell {row.index_i, row.index_j}"
    )
    return match.iloc[0]


# Reference calculations


def test_reference_ead():
    # constant damage: probability of exceeding the most frequent return period, times damage
    assert syn.ead(np.full(3, 100.0)) == pytest.approx(100 / 10)
    # no damage at the most frequent return period, full damage at rarer ones: a ramp in ln(p), integrated
    damage = np.array([0.0, 1.0, 1.0])
    # p from 0.001 to 0.01 is flat (1), from 0.01 to 0.1 rises 1 -> 0 with ln p
    numeric, _ = integrate.quad(lambda p: 1 - np.log(p / 0.01) / np.log(10), 0.01, 0.1)
    assert syn.ead(damage) == pytest.approx(0.001 * 1 + 0.009 * 1 + numeric)


def test_hand_numbers(dataset):
    """A few expected values worked out by hand, to check the reference calculation itself."""
    # epoch 2020, return period 10: first column of each array
    costs = {}
    for layer in dataset.layers.values():
        costs.update(layer.expected.assets.set_index("asset_id").cost.to_dict())

    def total(asset):
        return costs[asset]

    # L1: 80, 100, 100, 80 m in depths 0, 0.4, 1.0, 2.0 m: damage 0, 0.2, 0.5, 1 of 100,000 per km
    assert total("L1")[0, 0] == pytest.approx(100_000 * (0.0 * 0.08 + 0.2 * 0.1 + 0.5 * 0.1 + 1.0 * 0.08))
    # C1: whole-asset cost 50,000 shared by length 70, 100, 100, 70 of 340 m, curve depth / 4
    assert total("C1")[0, 0] == pytest.approx(50_000 * (0.1 * 70 + 0.15 * 100 + 0.2 * 100) / 340)
    # C2: all of the asset in 1.0 m, damage 0.25
    assert total("C2")[0, 0] == pytest.approx(0.25 * 50_000)
    # B2: 3600 m2 at 4 m, fully damaged, 20 per m2
    assert total("B2")[0, 0] == pytest.approx(3600 * 20)
    # S1: 10 MW at 0.6 m, damage 0.3, 1000 per MW; at return periods 100 and 1000 depth is 0.9 and 1.2 m
    np.testing.assert_allclose(total("S1")[0], [3000, 4500, 6000])
    # M1: 8 MW at 0.5 m, damage 0.25
    assert total("M1")[0, 0] == pytest.approx(0.25 * 8 * 1000)
    # M4: two halves of 3000 m2 at 0.2 m (damage 0.1) and 0 m
    assert total("M4")[0, 0] == pytest.approx(3000 * 0.1 * 20)
    # epoch 2050 is 1.25 times deeper, so S1 at return period 10 is 0.75 m, damage 0.375
    assert total("S1")[1, 0] == pytest.approx(0.375 * 10 * 1000)


# Splits


def test_splits_conserve_length_area_and_share(layer):
    split = layer.split
    for asset in layer.assets.itertuples():
        rows = split[split.asset_id == asset.asset_id]
        # shares are of the unsplit asset: less than one for a polygon partly outside the grid
        in_grid_share = sum(m for _, _, m in syn.pieces(asset.geometry)) / syn.whole(asset.geometry)
        assert rows.share.sum() == pytest.approx(in_grid_share)
        # lengths and areas, from the column and from the split geometries
        if asset.geometry.geom_type == "LineString":
            assert rows.length_m.sum() == pytest.approx(asset.geometry.length)
            assert rows.geometry.length.sum() == pytest.approx(asset.geometry.length)
            assert (rows.geom_kind == "line").all()
            assert (rows.area_m2 == 0).all()
        elif asset.geometry.geom_type == "Polygon":
            in_grid = asset.geometry.intersection(syn.GRID_BOX).area
            assert rows.area_m2.sum() == pytest.approx(in_grid)
            assert rows.geometry.area.sum() == pytest.approx(in_grid)
            assert (rows.geom_kind == "polygon").all()
            assert (rows.length_m == 0).all()
        else:
            assert len(rows) == 1
            assert (rows.geom_kind == "point").all()


def test_splits_match_hand_calculated_pieces(layer):
    assert not layer.split.duplicated(["asset_id", "index_i", "index_j"]).any()
    for asset_id, geometry in layer.assets.set_index("asset_id").geometry.items():
        pieces = {(i, j): m for i, j, m in syn.pieces(geometry)}
        rows = layer.split[layer.split.asset_id == asset_id]
        assert {(r.index_i, r.index_j) for r in rows.itertuples()} == set(pieces)
        for r in rows.itertuples():
            measure = {"line": r.length_m, "polygon": r.area_m2}.get(r.geom_kind, 1.0)
            assert measure == pytest.approx(pieces[(r.index_i, r.index_j)])
            assert r.share == pytest.approx(pieces[(r.index_i, r.index_j)] / syn.whole(geometry))


def test_conservation_warning_only_for_clipped_polygon(dataset):
    # B3 is 60 m x 50 m in the grid and 40 m x 50 m outside it, and snail drops the part outside the grid
    polygon = dataset.layers["polygon"].split
    assert polygon[polygon.asset_id == "B3"].area_m2.sum() == pytest.approx(3000)
    assert (
        "2 assets have split lengths or areas differing from the unsplit asset by more than 0.1%"
        in (dataset.split_logs["polygon"])
    )
    assert "B3 (40.0% off)" in dataset.split_logs["polygon"]
    assert "R3 (60.0% off)" in dataset.split_logs["polygon"]
    # R3's shares are of the whole reservoir, so only the 40% inside the grid can be damaged
    assert polygon[polygon.asset_id == "R3"].share.sum() == pytest.approx(0.4)
    # lines keep their part outside the grid, so there is nothing to report for them
    for name in ("line", "point", "mixed"):
        assert "differing from the unsplit asset" not in dataset.split_logs[name]


def test_outputs_align_with_split_rows(layer):
    split = layer.split
    for ds in (layer.intensity, layer.split_damage):
        assert ds.sizes["split"] == len(split)
        assert (ds.asset_id.values == split.asset_id.to_numpy()).all()
        assert (ds.split_id.values == split.split_id.to_numpy()).all()
        assert (ds.asset_type.values == split.asset_type.to_numpy()).all()
    assert split.split_id.tolist() == split.groupby("asset_id", sort=False).cumcount().tolist()
    assert set(layer.asset_damage.asset_id.values) == set(layer.assets.asset_id)
    assert layer.asset_damage.n_splits.values.tolist() == [
        int((split.asset_id == a).sum()) for a in layer.asset_damage.asset_id.values
    ]
    assert set(layer.type_damage.asset_type.values) == set(layer.assets.asset_type)
    assert layer.type_damage.n_assets.values.tolist() == [
        int((layer.assets.asset_type == t).sum()) for t in layer.type_damage.asset_type.values
    ]


# Intensity


def test_intensity_is_depth_of_cell(layer):
    depth = syn.depth_cube()
    assert layer.intensity.dims == ("split", "epoch", "return_period")
    assert layer.intensity.attrs["units"] == "m"
    for k, row in enumerate(layer.split.itertuples()):
        if row.index_i < 0:
            assert row.index_j < 0
            assert np.isnan(layer.intensity[k]).all()
        else:
            np.testing.assert_allclose(layer.intensity[k], depth[:, :, row.index_j, row.index_i], rtol=1e-6)


# Damage


def test_split_damage_matches_hand_calculation(layer):
    damage = layer.split_damage
    rows = split_rows(layer)
    assert rows, "layer has vulnerable splits"
    assert damage.damage_cost.dims == ("split", "epoch", "return_period", "quantile")
    assert damage.ead.dims == ("split", "epoch", "quantile")
    expected = [expected_split_row(layer, k) for k in rows]
    assert_quantiles_equal(damage.damage_fraction.isel(split=rows), np.stack([e.fraction for e in expected]))
    assert_quantiles_equal(damage.damage_cost.isel(split=rows), np.stack([e.cost for e in expected]))
    assert_quantiles_equal(damage.ead.isel(split=rows), np.stack([e.ead for e in expected]))


def test_asset_damage_is_sum_of_split_damages(layer):
    damage = layer.asset_damage
    expected = layer.expected.assets.set_index("asset_id")
    ids = [a for a in damage.asset_id.values if a in expected.index]
    assert ids
    assert_quantiles_equal(
        damage.damage_cost.isel(asset=[list(damage.asset_id.values).index(a) for a in ids]),
        np.stack(expected.cost[ids]),
    )
    assert_quantiles_equal(
        damage.ead.isel(asset=[list(damage.asset_id.values).index(a) for a in ids]), np.stack(expected.ead[ids])
    )


def test_asset_type_totals_are_sums_over_assets(layer):
    damage = layer.type_damage
    expected = layer.expected.asset_types.set_index("asset_type")
    for asset_type, row in expected.iterrows():
        totals = damage.sel(asset_type=asset_type)
        assert str(totals.status.values) == "calculated"
        assert str(totals.currency.values) == "usd"
        assert_quantiles_equal(totals.damage_cost, row.cost)
        assert_quantiles_equal(totals.ead, row.ead)


def test_all_nan_epoch_gives_nan(layer):
    for name, group in [("split", layer.split_damage), ("asset", layer.asset_damage)]:
        calculated = group.asset_type.isin(list(syn.COSTS)).values
        for var in ("damage_cost", "ead"):
            values = group[var].isel({name: calculated})
            assert values.sel(epoch=2080).isnull().all(), (name, var)
            assert values.sel(epoch=[2020, 2050]).notnull().all(), (name, var)
    totals = layer.type_damage.sel(asset_type=[t for t in layer.type_damage.asset_type.values if t in syn.COSTS])
    assert totals.damage_cost.sel(epoch=2080).isnull().all()
    assert totals.ead.sel(epoch=2080).isnull().all()


def test_nan_and_outside_grid_splits_have_zero_damage(dataset):
    def zero(layer, asset_id, rows_with=None):
        damage = layer.split_damage
        k = np.flatnonzero(damage.asset_id.values == asset_id)
        if rows_with is not None:
            k = [i for i in k if rows_with(layer.split.iloc[i])]
        assert len(k)
        for var in ("damage_fraction", "damage_cost", "ead"):
            assert (damage[var].isel(split=k).sel(epoch=[2020, 2050]) == 0).all(), (asset_id, var)

    points, lines = dataset.layers["point"], dataset.layers["line"]
    zero(points, "S3")  # in the NaN cell
    zero(points, "S4")  # outside the grid
    zero(lines, "L3")  # in the NaN cell
    zero(lines, "L4", lambda row: row.index_i < 0)  # the part outside the grid
    # so the damage to S3, S4 and L3 is zero, and L4 is damaged only by its part in the 0.5 m cell
    assets = lines.asset_damage.sel(epoch=2020, return_period=10, quantile=0.5).damage_cost
    assert float(assets.sel(asset=list(lines.asset_damage.asset_id.values).index("L3"))) == 0
    assert float(
        points.asset_damage.damage_cost.sel(epoch=2020, return_period=10, quantile=0.5).sum()
    ) == pytest.approx(
        # S1 3000, S2 20 MW at 4 m (fully damaged) 20,000, S5 5 MW at 1.0 m (0.5) 2500
        3000 + 20_000 + 2_500
    )
    np.testing.assert_allclose(
        float(assets.sel(asset=list(lines.asset_damage.asset_id.values).index("L4"))),
        # (2,3) holds 50 of 350 m at 0.5 m: damage 0.25 of 100,000 per km
        0.25 * 100_000 * 0.05,
        rtol=RTOL,
    )


def test_mixed_layer_quantities_by_cost_unit(dataset):
    mixed = dataset.layers["mixed"]
    kinds = mixed.split.groupby("asset_type").geom_kind.agg(lambda s: sorted(set(s))).to_dict()
    assert kinds == {
        "substation": ["point"],
        "pylon_nv": ["point"],
        "road_line": ["line"],
        "building": ["polygon"],
        "mystery_pipe": ["line"],
    }
    totals = mixed.type_damage.damage_cost.sel(epoch=2020, return_period=10, quantile=0.5)
    # per attribute: 8 MW (share 1) at 0.5 m, damage 0.25, 1000 per MW
    assert float(totals.sel(asset_type="substation")) == pytest.approx(0.25 * 8 * 1000)
    # per m2: two halves of 3000 m2, at 0.2 m (damage 0.1) and 0 m, 20 per m2
    assert float(totals.sel(asset_type="building")) == pytest.approx(3000 * 0.1 * 20)
    # per km: M3 is 160 m east and 60 m south, so 170.88 m long. Its east-west extent is 30 m in (1,2) at 0.8 m
    # (damage 0.4), 76.67 m in (2,2) at 0 m, 23.33 m in (2,3) at 0.5 m (0.25) and 30 m in (3,3) at 4 m (1)
    length_km = np.hypot(160, 60) / 1000
    road = 100_000 * length_km * (0.4 * 30 / 160 + 0.25 * (70 / 3) / 160 + 1.0 * 30 / 160)
    assert float(totals.sel(asset_type="road_line")) == pytest.approx(road, rel=RTOL)


def test_not_vulnerable_type_has_zero_damage(dataset):
    mixed = dataset.layers["mixed"]
    total = mixed.type_damage.sel(asset_type=syn.NOT_VULNERABLE)
    assert str(total.status.values) == "not_vulnerable"
    assert int(total.n_assets) == 1
    for ds, selector in [(mixed.split_damage, mixed.split_damage.asset_type == syn.NOT_VULNERABLE)]:
        sub = ds.isel(split=np.flatnonzero(selector.values))
        for var in ("damage_fraction", "damage_cost", "ead"):
            assert (sub[var].sel(epoch=[2020, 2050]) == 0).all()
    sub = mixed.asset_damage.sel(asset=mixed.asset_damage.asset_type == syn.NOT_VULNERABLE)
    assert (sub.damage_cost.sel(epoch=[2020, 2050]) == 0).all() and (sub.ead.sel(epoch=[2020, 2050]) == 0).all()
    for var in ("damage_cost", "ead"):
        assert (total[var].sel(epoch=[2020, 2050]) == 0).all()


def test_unknown_type_is_nan_and_not_configured(dataset):
    mixed = dataset.layers["mixed"]
    total = mixed.type_damage.sel(asset_type=syn.UNKNOWN)
    assert str(total.status.values) == "not_configured"
    assert str(total.currency.values) == ""
    assert int(total.n_assets) == 1
    assert total.damage_cost.isnull().all() and total.ead.isnull().all()
    k = np.flatnonzero(mixed.split_damage.asset_type.values == syn.UNKNOWN)
    assert len(k) == 1
    for var in ("damage_fraction", "damage_cost", "ead"):
        assert mixed.split_damage[var].isel(split=k).isnull().all()
    a = np.flatnonzero(mixed.asset_damage.asset_type.values == syn.UNKNOWN)
    assert mixed.asset_damage.damage_cost.isel(asset=a).isnull().all()
    # other types in the same layer are unaffected
    assert not mixed.type_damage.sel(asset_type="road_line").damage_cost.sel(epoch=2020).isnull().any()


def test_strict_fails_on_unknown_type(dataset, tmp_path):
    result = run_script("damage_calculate.py", *dataset.damage_args("mixed", tmp_path / "strict.zarr", "--strict"))
    assert result.returncode != 0
    assert syn.UNKNOWN in result.stderr
    assert "not listed in the workbook" in result.stderr
    # layers whose asset types are all configured are fine under --strict
    result = run_script("damage_calculate.py", *dataset.damage_args("line", tmp_path / "line.zarr", "--strict"))
    assert result.returncode == 0, result.stderr


# Summary


def check_summary(summary: pd.DataFrame, layers: dict[str, Layer]) -> None:
    assert set(summary.layer) == set(LAYER_NAMES)
    assert set(summary.peril) == {PERIL} and set(summary.subperil) == {SUBPERIL}
    assert (summary.q_5 == summary.q_50).where(summary.q_5.notna(), True).all()
    assert (summary.q_50 == summary.q_95).where(summary.q_50.notna(), True).all()
    for name, layer in layers.items():
        rows = summary[summary.layer == name]
        for asset_type, expected in layer.expected.asset_types.set_index("asset_type").iterrows():
            mine = rows[rows.asset_type == asset_type]
            assert (mine.status == "calculated").all()
            assert (mine.currency == "usd").all()
            for e, epoch in enumerate(syn.EPOCHS):
                ead = mine[(mine.metric == "ead") & (mine.epoch == epoch)]
                np.testing.assert_allclose(ead.q_50.to_numpy(), [expected.ead[e]], rtol=RTOL)
                for k, rp in enumerate(syn.RETURN_PERIODS):
                    cost = mine[(mine.metric == "damage_cost") & (mine.epoch == epoch) & (mine.return_period == rp)]
                    np.testing.assert_allclose(cost.q_50.to_numpy(), [expected.cost[e, k]], rtol=RTOL)
    mixed = summary[summary.layer == "mixed"]
    assert set(mixed[mixed.asset_type == syn.UNKNOWN].status) == {"not_configured"}
    assert mixed[mixed.asset_type == syn.UNKNOWN].q_50.isna().all()
    assert set(mixed[mixed.asset_type == syn.NOT_VULNERABLE].status) == {"not_vulnerable"}


def test_summary(dataset):
    check_summary(dataset.summary, dataset.layers)


@pytest.mark.integration
def test_snakemake_workflow(dataset, tmp_path):
    """The same data through snakemake gives the same summary."""
    if shutil.which("snakemake") is None:
        pytest.skip("snakemake not installed")
    data = tmp_path / "data"
    inputs = write_inputs(data)
    config = tmp_path / "config.yaml"
    config.write_text(
        f"data_root: '{data}'\n"
        "metadata:\n"
        f"  layer: '{inputs['layers']}'\n"
        f"  hazard: '{inputs['hazards']}'\n"
        "damage:\n"
        f"  config: '{inputs['workbook']}'\n"
        f"  n_samples: {N_SAMPLES}\n"
        f"  measure_crs: '{syn.CRS}'\n"
        "  strict: false\n"
    )
    target = data / "results" / "damage" / "summary.csv"
    subprocess.run(
        ["snakemake", "--cores", "2", "--nolock", str(target), "--configfile", str(config)], cwd=REPO, check=True
    )
    summary = pd.read_csv(target)
    check_summary(summary, dataset.layers)
    # and the scripts, run directly, agree with snakemake on every number
    key = ["layer", "asset_type", "metric", "epoch", "return_period"]
    left = summary.sort_values(key, ignore_index=True)
    right = dataset.summary.sort_values(key, ignore_index=True)
    pd.testing.assert_frame_equal(left, right)


# Damage report


def report(data: Path, inputs: dict, *extra, layers=None):
    layers_csv = inputs["layers"]
    if layers is not None:
        layers_csv = data.parent / "layers-subset.csv"
        layers_csv.write_text("layer,path\n" + "".join(f"{n},processed/asset/layer-{n}.gpq\n" for n in layers))
    return run_script(
        "damage_report.py",
        "--data-root", data,
        "--layers", layers_csv,
        "--hazards", inputs["hazards"],
        "--damage-config", inputs["workbook"],
        *extra,
    )  # fmt: skip


def test_report_unknown_asset_type(dataset):
    result = report(dataset.data, dataset.inputs)
    assert result.returncode == 0, result.stderr
    assert f"asset types not in workbook 'asset-type' sheet: {syn.UNKNOWN}" in result.stdout
    assert "Layers: 4 of 4 available" in result.stdout
    # 4 layers x 1 hazard, by asset type: line 2, polygon 2, point 1 and mixed 3 calculated
    assert "8 will be calculated, 1 not vulnerable (zero damage), 1 missing configuration" in result.stdout
    assert f"| {syn.UNKNOWN} | 1 | not_configured |" in result.stdout
    assert f"| {syn.NOT_VULNERABLE} | 1 | not_vulnerable |" in result.stdout
    assert report(dataset.data, dataset.inputs, "--strict").returncode != 0
    # without the mixed layer, nothing is missing
    clean = report(dataset.data, dataset.inputs, "--strict", layers=["line", "polygon", "point"])
    assert clean.returncode == 0, clean.stdout + clean.stderr


def test_report_units_mismatch(tmp_path):
    data = tmp_path / "data"
    inputs = write_inputs(data, units="cm")
    result = report(data, inputs, layers=["line", "polygon", "point"])
    assert result.returncode == 0, result.stderr
    assert "has units 'cm', but the workbook gives 'm' for 'depth_m'" in result.stdout
    assert report(data, inputs, "--strict", layers=["line", "polygon", "point"]).returncode != 0
    # a spelling of the same unit is not a mismatch
    fixed = write_inputs(tmp_path / "ok" / "data", units="metres")
    ok = report(tmp_path / "ok" / "data", fixed, "--strict", layers=["line", "polygon", "point"])
    assert ok.returncode == 0, ok.stdout
