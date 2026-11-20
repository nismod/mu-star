"""Report what the direct damages workflow will calculate, and what is missing.

Given the layer and hazard metadata tables, the damage configuration workbook
and the data root, checks:

- the damage configuration workbook (see ``damage_validate_config.py``)
- each asset layer: file present, required columns, asset types, geometry
- each hazard: store present, intensity variable, return periods, GeoZarr
  grid attributes, hazard key in the workbook
- each layer and hazard combination, by asset type: whether damage will be
  calculated, is zero (not vulnerable) or will be missing, and why
- which damage outputs already exist, and whether they are older than their
  inputs

Writes a Markdown report. This reads asset attributes and geometry types, and
hazard metadata, but no hazard values, so it is quick to run. For the exact
jobs snakemake will run, use ``snakemake --dry-run``.

Example:

    python workflow/1-damage/damage_report.py \\
        --data-root example \\
        --layers example/config/layer.csv \\
        --hazards example/config/hazard.csv \\
        --damage-config example/config/asset-type-cost-curves.xlsx
"""

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import click
import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr

from damages.config import ConfigError, DamageConfig, read_damage_config, validate_damage_config
from damages.geozarr import check_coords_match_grid, grid_from_geozarr_attrs
from damages.metadata import TABLES, load_metadata_table
from damages.plan import CALCULATED, NOT_CONFIGURED, NOT_VULNERABLE, plan_asset_type
from damages.units import UnitError, units_match

# Snakemake writes a .snakemake_timestamp file in directory outputs, which zarr reports when opening the store
warnings.filterwarnings("ignore", message=r"Object at \.snakemake_timestamp is not recognized")

OK = "ok"
MISSING = "missing"
INVALID = "invalid"


@dataclass
class LayerCheck:
    name: str
    path: Path
    status: str = OK
    problems: list[str] = field(default_factory=list)
    n_assets: int = 0
    assets: pd.DataFrame | None = None  # non-geometry columns, plus geom_type

    @property
    def asset_types(self) -> dict[str, int]:
        if self.assets is None or "asset_type" not in self.assets:
            return {}
        return self.assets.asset_type.astype(str).value_counts().sort_index().to_dict()


@dataclass
class HazardCheck:
    peril: str
    subperil: str
    path: Path
    variable: str
    hazard: str
    status: str = OK
    problems: list[str] = field(default_factory=list)
    metric: str | None = None
    sizes: dict[str, int] = field(default_factory=dict)
    return_periods: list[float] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.peril}/{self.subperil}"


def _usable_rows(table: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    if any(c not in table.columns for c in required):
        return table.iloc[0:0].reindex(columns=required)
    return table.dropna(subset=required)


def check_layer(name: str, path: Path, config: DamageConfig | None) -> LayerCheck:
    check = LayerCheck(name, path)
    if not path.exists():
        check.status = MISSING
        check.problems.append("file not found")
        return check
    try:
        columns = pq.read_schema(path).names
        frame = gpd.read_parquet(path)
    except Exception as err:
        check.status = INVALID
        check.problems.append(f"could not read: {err}")
        return check

    check.n_assets = len(frame)
    for column in ("asset_id", "asset_type"):
        if column not in columns:
            check.status = INVALID
            check.problems.append(f"no {column!r} column")
    if "asset_id" in frame:
        if frame.asset_id.isna().any():
            check.problems.append(f"{int(frame.asset_id.isna().sum())} assets have no asset_id")
        n_dup = int(frame.asset_id.duplicated().sum())
        if n_dup:
            check.status = INVALID
            check.problems.append(f"{n_dup} duplicate asset_id values")
    if "asset_type" in frame and frame.asset_type.isna().any():
        check.problems.append(f"{int(frame.asset_type.isna().sum())} assets have no asset_type (will be 'nan')")
    if frame.crs is None:
        check.problems.append("no CRS")
    n_empty = int((frame.geometry.isna() | frame.geometry.is_empty).sum())
    if n_empty:
        check.problems.append(f"{n_empty} assets have empty geometry (will be dropped)")
    if config is not None and "asset_type" in frame:
        known = set(config.asset_types.asset_type)
        unknown = sorted(set(frame.asset_type.dropna().astype(str)) - known)
        if unknown:
            check.problems.append(f"asset types not in workbook 'asset-type' sheet: {', '.join(unknown)}")

    assets = pd.DataFrame(frame.drop(columns=frame.geometry.name))
    assets["geom_type"] = frame.geometry.geom_type.to_numpy()
    check.assets = assets
    return check


def check_hazard(row, data_root: Path, config: DamageConfig | None) -> HazardCheck:
    check = HazardCheck(row.peril, row.subperil, data_root / row.path, row.variable, row.hazard)
    if config is not None:
        try:
            check.metric = config.intensity_metric(row.hazard)
        except ConfigError as err:
            check.problems.append(str(err))
        else:
            if check.metric not in set(config.hazard_metrics.intensity_metric):
                check.problems.append(f"intensity metric {check.metric!r} not in 'hazard-metric' sheet")
    if not check.path.exists():
        check.status = MISSING
        check.problems.append("store not found")
        return check
    try:
        ds = xr.open_zarr(check.path, consolidated=False)
    except Exception as err:
        check.status = INVALID
        check.problems.append(f"could not open: {err}")
        return check

    if row.variable not in ds:
        check.status = INVALID
        check.problems.append(f"no variable {row.variable!r}; found {', '.join(map(str, ds.data_vars))}")
        return check
    da = ds[row.variable]
    check.sizes = dict(da.sizes)
    if "return_period" not in da.dims:
        check.status = INVALID
        check.problems.append("no 'return_period' dimension")
    else:
        rps = np.asarray(da.return_period.values, dtype=float)
        check.return_periods = rps.tolist()
        if len(rps) < 2:
            check.problems.append("fewer than two return periods, EAD will be crude")
        if (rps < 1).any() or len(set(rps)) != len(rps):
            check.status = INVALID
            check.problems.append("return periods must be unique and at least one year")
    try:
        grid = grid_from_geozarr_attrs(ds.attrs)
        y_dim, x_dim = ds.attrs.get("spatial:dimensions", ["y", "x"])
        check_coords_match_grid(ds[x_dim].values, ds[y_dim].values, grid)
        if {y_dim, x_dim} - set(da.dims):
            raise ValueError(f"variable lacks spatial dimensions {y_dim!r}, {x_dim!r}")
    except (KeyError, ValueError) as err:
        check.status = INVALID
        check.problems.append(f"GeoZarr grid: {err}")
    units = da.attrs.get("units") or da.attrs.get("unit")
    expected = config.metric_unit(check.metric) if config is not None and check.metric else None
    if not units:
        suffix = f" (workbook gives {expected!r} for {check.metric!r})" if expected else ""
        check.problems.append(f"variable {row.variable!r} has no units attribute{suffix}")
    elif expected:
        try:
            matched = units_match(units, expected)
        except UnitError as err:
            check.status = INVALID
            check.problems.append(f"variable {row.variable!r} units {str(units)!r} vs workbook {expected!r}: {err}")
        else:
            if not matched:
                check.status = INVALID
                check.problems.append(
                    f"variable {row.variable!r} has units {str(units)!r}, "
                    f"but the workbook gives {expected!r} for {check.metric!r}"
                )
    return check


def output_paths(data_root: Path, layer: str, hazard: HazardCheck) -> dict[str, Path]:
    base = (
        data_root
        / "results"
        / "damage"
        / f"layer-{layer}"
        / "rp"
        / f"peril-{hazard.peril}"
        / f"subperil-{hazard.subperil}"
    )
    return {name: base / name for name in ("split.gpq", "intensity.zarr", "damage.zarr")}


def _mtime(path: Path) -> float:
    if path.is_dir():
        candidates = [path / "zarr.json", path / ".zgroup", path]
        return max(p.stat().st_mtime for p in candidates if p.exists())
    return path.stat().st_mtime


def output_state(outputs: dict[str, Path], inputs: list[Path]) -> str:
    damage = outputs["damage.zarr"]
    if not damage.exists():
        done = [name for name, path in outputs.items() if path.exists()]
        return f"to run (have {', '.join(done)})" if done else "to run"
    newest_input = max((_mtime(p) for p in inputs if p.exists()), default=0)
    return "stale (inputs changed)" if newest_input > _mtime(damage) else "done"


def _table(header: list[str], rows: list[list]) -> list[str]:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return [*lines, ""]


def _fmt_weights(weights: dict[str, float]) -> str:
    return ", ".join(f"{k} ({v:g})" for k, v in weights.items())


def build_report(data_root: Path, layers_path: Path, hazards_path: Path, config_path: Path) -> tuple[str, int]:
    """Build the Markdown report. Returns the report and the number of blocking problems."""
    lines = ["# Direct damage report", ""]
    lines += [
        f"- Data root: `{data_root}`",
        f"- Layers: `{layers_path}`",
        f"- Hazards: `{hazards_path}`",
        f"- Damage configuration: `{config_path}`",
        "",
    ]
    blocking = 0

    # Damage configuration
    config, config_problems = None, []
    if not config_path.exists():
        config_problems = ["workbook not found: no damage can be calculated"]
        blocking += 1
    else:
        try:
            config = read_damage_config(config_path)
            config_problems = validate_damage_config(config)
        except (ConfigError, ValueError) as err:
            config_problems = [f"could not read workbook: {err}"]
            blocking += 1

    layer_table, layer_problems = load_metadata_table(layers_path, "layer")
    hazard_table, hazard_problems = load_metadata_table(hazards_path, "hazard")
    metadata_problems = layer_problems + hazard_problems
    blocking += len(metadata_problems)
    # rows with blank required cells, or tables without the required columns, cannot be checked
    layer_table = _usable_rows(layer_table, TABLES["layer"]["required"])
    hazard_table = _usable_rows(hazard_table, TABLES["hazard"]["required"])
    layers = [check_layer(row.layer, data_root / row.path, config) for row in layer_table.itertuples()]
    hazards = [check_hazard(row, data_root, config) for row in hazard_table.itertuples()]

    # Combinations
    combo_rows, detail = [], []
    counts = {CALCULATED: 0, NOT_VULNERABLE: 0, NOT_CONFIGURED: 0}
    run_counts = {"done": 0, "to run": 0, "stale": 0, "blocked": 0}
    for layer in layers:
        for hazard in hazards:
            outputs = output_paths(data_root, layer.name, hazard)
            inputs = [layer.path, hazard.path / "zarr.json", config_path]
            if layer.status != OK or hazard.status != OK or config is None:
                reasons = []
                if layer.status != OK:
                    reasons.append(f"layer {layer.status}")
                if hazard.status != OK:
                    reasons.append(f"hazard {hazard.status}")
                if config is None:
                    reasons.append("no damage configuration")
                state = "blocked: " + ", ".join(reasons)
                run_counts["blocked"] += 1
                combo_rows.append([layer.name, hazard.label, state, "", "", ""])
                continue
            state = output_state(outputs, inputs)
            run_counts[state.split(" (")[0]] += 1

            plans = []
            for asset_type, n in layer.asset_types.items():
                assets = layer.assets[layer.assets.asset_type.astype(str) == asset_type]
                plan = plan_asset_type(config, asset_type, hazard.metric, assets) if hazard.metric else None
                if plan is None:
                    status, reason = NOT_CONFIGURED, f"hazard {hazard.hazard!r} has no intensity metric"
                else:
                    status, reason = plan.status, plan.reason
                counts[status] += 1
                plans.append((asset_type, n, status, reason, plan))
            n_calc = sum(p[2] == CALCULATED for p in plans)
            n_missing = sum(p[2] == NOT_CONFIGURED for p in plans)
            combo_rows.append(
                [layer.name, hazard.label, state, n_calc, sum(p[2] == NOT_VULNERABLE for p in plans), n_missing]
            )

            detail += [f"### {layer.name} x {hazard.label} ({hazard.metric or 'no intensity metric'})", ""]
            rows = []
            for asset_type, n, status, reason, plan in plans:
                if status == CALCULATED:
                    cost = plan.cost
                    how = f"{cost.mean:g} {cost.unit.currency}" + (f" per {cost.unit.per}" if cost.unit.per else "")
                    how += f" (cv systemic {cost.systemic_cv:g}, asset {cost.asset_cv:g})"
                    how += f"; curves {_fmt_weights(plan.weights)}"
                else:
                    how = reason
                rows.append([asset_type, n, status, how])
            detail += _table(["asset type", "assets", "status", "cost and curves, or reason"], rows)

    # Summary
    lines += ["## Summary", ""]
    lines += [
        f"- Layers: {sum(c.status == OK for c in layers)} of {len(layers)} available",
        f"- Hazards: {sum(c.status == OK for c in hazards)} of {len(hazards)} available",
        f"- Layer x hazard damage outputs: {run_counts['done']} done, {run_counts['to run']} to run, "
        f"{run_counts['stale']} stale, {run_counts['blocked']} blocked",
        f"- Asset type x hazard: {counts[CALCULATED]} will be calculated, {counts[NOT_VULNERABLE]} not vulnerable "
        f"(zero damage), {counts[NOT_CONFIGURED]} missing configuration (NaN damage, or an error if strict)",
        f"- Damage configuration problems: {len(config_problems)}",
        f"- Layer and hazard table problems: {len(metadata_problems)}",
        "",
    ]
    blocking += sum(c.status != OK for c in layers) + sum(c.status != OK for c in hazards) + counts[NOT_CONFIGURED]

    lines += ["## Damage configuration", ""]
    lines += [f"- {p}" for p in config_problems] if config_problems else ["No problems found."]
    lines += [""]

    if metadata_problems:
        lines += ["## Layer and hazard table problems", ""]
        lines += [f"- {p}" for p in metadata_problems]
        lines += [""]

    lines += ["## Asset layers", ""]
    rows = []
    for c in layers:
        geom = ""
        if c.assets is not None:
            geom = ", ".join(f"{k} {v}" for k, v in c.assets.geom_type.value_counts().sort_index().items())
        types = ", ".join(f"{k} ({v})" for k, v in c.asset_types.items())
        rows.append([c.name, f"`{c.path}`", c.status, c.n_assets, geom, types, "; ".join(c.problems)])
    lines += _table(["layer", "path", "status", "assets", "geometry", "asset types", "problems"], rows)

    lines += ["## Hazards", ""]
    rows = []
    for c in hazards:
        dims = ", ".join(f"{k} {v}" for k, v in c.sizes.items())
        rps = ", ".join(f"{rp:g}" for rp in c.return_periods)
        rows.append(
            [
                c.label,
                f"`{c.path}`",
                c.status,
                c.variable,
                f"{c.hazard} ({c.metric or '?'})",
                dims,
                rps,
                "; ".join(c.problems),
            ]
        )
    lines += _table(
        ["hazard", "path", "status", "variable", "hazard key (metric)", "dimensions", "return periods", "problems"],
        rows,
    )

    lines += ["## Damage calculations", ""]
    lines += [
        "Outputs are 'done' if `damage.zarr` exists and is newer than the layer, hazard and workbook files.",
        "Use `snakemake --dry-run` to see exactly which jobs snakemake will run.",
        "",
    ]
    if combo_rows:
        lines += _table(
            ["layer", "hazard", "outputs", "asset types calculated", "not vulnerable", "missing configuration"],
            combo_rows,
        )
    else:
        lines += ["No layer and hazard combinations to calculate: add layers and hazards to the metadata tables.", ""]
    lines += detail
    return "\n".join(lines).rstrip() + "\n", blocking


@click.command()
@click.option("--data-root", default="data", show_default=True, type=click.Path(file_okay=False, path_type=Path))
@click.option("--layers", "layers_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--hazards", "hazards_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--damage-config", "config_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--output", "output_path", default=None, type=click.Path(dir_okay=False), help="Write to file")
@click.option("--strict/--no-strict", default=False, help="Exit with an error if anything is missing")
def main(data_root, layers_path, hazards_path, config_path, output_path, strict):
    report, blocking = build_report(data_root, layers_path, hazards_path, config_path)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report)
    click.echo(report)
    if strict and blocking:
        raise click.ClickException(f"{blocking} blocking problems found")


if __name__ == "__main__":
    main()
