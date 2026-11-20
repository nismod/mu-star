"""Read the damage configuration workbook.

The workbook relates asset types, hazards, vulnerability, costs and damage
curves. Each worksheet is a table; see the README sheet of the workbook and
``docs/src/damages.md`` for column definitions. Sheets used here:

- ``asset-type`` (asset_type, sector, description)
- ``hazard-metric`` (intensity_metric, description, optional unit)
- ``hazard`` (hazard, intensity_metric, source, description)
- ``asset-type-hazard-vulnerability`` (asset_type, hazard_intensity_metric, vulnerable)
- ``asset-type-cost`` (asset_type, cost_unit, cost_mean, cost_systemic_cv, cost_asset_cv, source)
- ``damage-curves`` (curve_id, hazard_intensity_metric, source, notes)
- ``damage-curve-data`` (curve_id, hazard_intensity, damage_fraction)
- ``asset-type-damage-curve`` (asset_type, hazard_intensity_metric, curve_id, curve_weight)

Impairment curve sheets may be present and are ignored here.

Formula cells are read using the values cached by the spreadsheet
application when the file was last saved.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from snail.damages import PiecewiseLinearDamageCurve

from damages.quantity import CostUnit, parse_cost_unit

SHEETS = {
    "asset_types": ("asset-type", ["asset_type"]),
    "hazard_metrics": ("hazard-metric", ["intensity_metric"]),
    "hazards": ("hazard", ["hazard", "intensity_metric"]),
    "vulnerability": ("asset-type-hazard-vulnerability", ["asset_type", "hazard_intensity_metric", "vulnerable"]),
    "costs": ("asset-type-cost", ["asset_type", "cost_unit", "cost_mean", "cost_systemic_cv", "cost_asset_cv"]),
    "curves": ("damage-curves", ["curve_id", "hazard_intensity_metric"]),
    "curve_data": ("damage-curve-data", ["curve_id", "hazard_intensity", "damage_fraction"]),
    "asset_curves": ("asset-type-damage-curve", ["asset_type", "hazard_intensity_metric", "curve_id", "curve_weight"]),
}


FORMULA_HINT = (
    "if these cells contain formulas, open the workbook in a spreadsheet application and save it, "
    "so that the calculated values are stored"
)

_TRUE = {"1", "1.0", "true", "yes"}
_FALSE = {"0", "0.0", "false", "no"}


class ConfigError(ValueError):
    """Damage configuration is invalid or incomplete."""


def parse_vulnerable(value) -> bool | None:
    """Interpret a ``vulnerable`` cell: 1/0, True/False, "yes"/"no" or "true"/"false" (any case).

    Returns None for blank cells and anything else.
    """
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | series.map(lambda v: isinstance(v, str) and not v.strip())


@dataclass(frozen=True)
class CostParameters:
    unit: CostUnit
    mean: float
    systemic_cv: float
    asset_cv: float


@dataclass
class DamageConfig:
    asset_types: pd.DataFrame
    hazard_metrics: pd.DataFrame
    hazards: pd.DataFrame
    vulnerability: pd.DataFrame
    costs: pd.DataFrame
    curves: pd.DataFrame
    curve_data: pd.DataFrame
    asset_curves: pd.DataFrame

    def intensity_metric(self, hazard: str) -> str:
        """Intensity metric of a hazard dataset listed on the hazard sheet."""
        rows = self.hazards.loc[self.hazards.hazard == hazard, "intensity_metric"]
        if rows.empty:
            raise ConfigError(f"Hazard {hazard!r} not listed in the 'hazard' sheet")
        return rows.iloc[0]

    def is_vulnerable(self, asset_type: str, metric: str) -> bool | None:
        """Whether an asset type is vulnerable to a metric (None if not listed)."""
        v = self.vulnerability
        rows = v.loc[(v.asset_type == asset_type) & (v.hazard_intensity_metric == metric), "vulnerable"]
        if rows.empty:
            return None
        # entries that are not understood are ignored here; validate_damage_config reports them
        values = [x for x in rows.map(parse_vulnerable) if x is not None]
        if not values:
            return None
        return any(values)

    def metric_unit(self, metric: str) -> str | None:
        """Unit of a hazard intensity metric from the optional ``unit`` column of the hazard-metric sheet."""
        table = self.hazard_metrics
        if "unit" not in table.columns:
            return None
        units = table.loc[table.intensity_metric == metric, "unit"].dropna()
        units = units[units.astype(str).str.strip() != ""]
        return str(units.iloc[0]).strip() if len(units) else None

    def curve_weights(self, asset_type: str, metric: str) -> dict[str, float]:
        """Curve identifiers and relative weights for an asset type and metric."""
        ac = self.asset_curves
        rows = ac.loc[(ac.asset_type == asset_type) & (ac.hazard_intensity_metric == metric)]
        weight = pd.to_numeric(rows["curve_weight"], errors="coerce")
        return weight.groupby(rows["curve_id"], sort=False).sum().astype(float).to_dict()

    def curve(self, curve_id: str) -> PiecewiseLinearDamageCurve:
        """Piecewise linear damage curve built from the curve data sheet."""
        data = self.curve_data.loc[self.curve_data.curve_id == curve_id].dropna(
            subset=["hazard_intensity", "damage_fraction"]
        )
        if data.empty:
            raise ConfigError(f"No complete data points for damage curve {curve_id!r}")
        data = data.sort_values("hazard_intensity")
        return PiecewiseLinearDamageCurve(
            pd.DataFrame(
                {
                    "intensity": data.hazard_intensity.astype(float).to_numpy(),
                    "damage": data.damage_fraction.astype(float).to_numpy(),
                }
            )
        )

    def curves_for(
        self, asset_type: str, metric: str
    ) -> tuple[dict[str, PiecewiseLinearDamageCurve], dict[str, float]]:
        weights = self.curve_weights(asset_type, metric)
        return {curve_id: self.curve(curve_id) for curve_id in weights}, weights

    def cost(self, asset_type: str) -> CostParameters:
        rows = self.costs.loc[self.costs.asset_type == asset_type]
        if rows.empty:
            raise ConfigError(f"No cost given for asset type {asset_type!r}")
        if len(rows) > 1:
            raise ConfigError(f"More than one cost row for asset type {asset_type!r}")
        row = rows.iloc[0]
        if pd.isna(row.cost_unit) or pd.isna(row.cost_mean):
            raise ConfigError(f"cost_unit and cost_mean are required for asset type {asset_type!r}")
        return CostParameters(
            unit=parse_cost_unit(row.cost_unit),
            mean=float(row.cost_mean),
            systemic_cv=0.0 if pd.isna(row.cost_systemic_cv) else float(row.cost_systemic_cv),
            asset_cv=0.0 if pd.isna(row.cost_asset_cv) else float(row.cost_asset_cv),
        )


def _clean(frame: pd.DataFrame, required: list[str], sheet: str) -> pd.DataFrame:
    # Drop unnamed spare columns and blank rows
    frame = frame.loc[:, [c for c in frame.columns if not str(c).startswith("Unnamed")]]
    frame = frame.dropna(how="all")
    missing = set(required) - set(frame.columns)
    if missing:
        raise ConfigError(f"Sheet {sheet!r} is missing columns {sorted(missing)}")
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(lambda v: v.strip() if isinstance(v, str) else v)
    return frame.reset_index(drop=True)


def read_damage_config(path: str | Path) -> DamageConfig:
    """Read the damage configuration workbook into tables."""
    sheets = pd.read_excel(path, sheet_name=None)
    tables = {}
    for name, (sheet, required) in SHEETS.items():
        if sheet not in sheets:
            raise ConfigError(f"Workbook {path} is missing sheet {sheet!r}")
        tables[name] = _clean(sheets[sheet], required, sheet)
    return DamageConfig(**tables)


def validate_damage_config(config: DamageConfig) -> list[str]:
    """Check references between tables and the curves needed for damage.

    Returns a list of human-readable problems, empty if none were found.
    """
    problems = []
    asset_types = set(config.asset_types.asset_type.dropna())
    metrics = set(config.hazard_metrics.intensity_metric.dropna())
    curve_ids = set(config.curves.curve_id.dropna())

    def check_refs(frame, column, valid, sheet, target):
        for value in sorted(set(frame[column].dropna()) - valid):
            problems.append(f"{sheet}: {column} {value!r} not found in {target}")

    def check_blank(frame, columns, sheet):
        for column in columns:
            n_blank = int(_blank(frame[column]).sum())
            if n_blank:
                problems.append(f"{sheet}: {n_blank} rows have a blank {column} ({FORMULA_HINT})")

    check_blank(config.asset_types, ["asset_type"], "asset-type")
    check_blank(config.hazard_metrics, ["intensity_metric"], "hazard-metric")
    check_blank(config.hazards, ["hazard", "intensity_metric"], "hazard")
    check_blank(
        config.vulnerability,
        ["asset_type", "hazard_intensity_metric", "vulnerable"],
        "asset-type-hazard-vulnerability",
    )
    check_blank(config.costs, ["asset_type"], "asset-type-cost")
    check_blank(config.curves, ["curve_id", "hazard_intensity_metric"], "damage-curves")
    check_blank(config.curve_data, ["curve_id"], "damage-curve-data")
    check_blank(
        config.asset_curves,
        ["asset_type", "hazard_intensity_metric", "curve_id", "curve_weight"],
        "asset-type-damage-curve",
    )

    if config.asset_types.asset_type.dropna().duplicated().any():
        problems.append("asset-type: duplicate asset_type values")
    if config.costs.asset_type.dropna().duplicated().any():
        problems.append("asset-type-cost: duplicate asset_type values")
    if config.curves.curve_id.dropna().duplicated().any():
        problems.append("damage-curves: duplicate curve_id values")
    check_refs(config.hazards, "intensity_metric", metrics, "hazard", "hazard-metric")
    check_refs(config.vulnerability, "asset_type", asset_types, "asset-type-hazard-vulnerability", "asset-type")
    check_refs(
        config.vulnerability, "hazard_intensity_metric", metrics, "asset-type-hazard-vulnerability", "hazard-metric"
    )
    check_refs(config.costs, "asset_type", asset_types, "asset-type-cost", "asset-type")
    check_refs(config.curves, "hazard_intensity_metric", metrics, "damage-curves", "hazard-metric")
    check_refs(config.curve_data, "curve_id", curve_ids, "damage-curve-data", "damage-curves")
    check_refs(config.asset_curves, "asset_type", asset_types, "asset-type-damage-curve", "asset-type")
    check_refs(config.asset_curves, "curve_id", curve_ids, "asset-type-damage-curve", "damage-curves")

    vulnerability = config.vulnerability.assign(_parsed=config.vulnerability.vulnerable.map(parse_vulnerable)).dropna(
        subset=["asset_type", "hazard_intensity_metric"]
    )
    unclear = vulnerability.loc[vulnerability._parsed.isna() & ~_blank(vulnerability.vulnerable)]
    for row in unclear.itertuples():
        problems.append(
            f"asset-type-hazard-vulnerability: {row.asset_type} / {row.hazard_intensity_metric} has vulnerable "
            f"{row.vulnerable!r}, which is not 1/0, true/false or yes/no"
        )
    for (asset_type, metric), group in vulnerability.groupby(["asset_type", "hazard_intensity_metric"], sort=False):
        if group._parsed.dropna().nunique() > 1:
            problems.append(
                f"asset-type-hazard-vulnerability: {asset_type} / {metric} is listed {len(group)} times "
                "with conflicting vulnerable values"
            )

    curve_metric = config.curves.set_index("curve_id").hazard_intensity_metric.to_dict()
    for row in config.asset_curves.itertuples():
        if row.curve_id in curve_metric and curve_metric[row.curve_id] != row.hazard_intensity_metric:
            problems.append(
                f"asset-type-damage-curve: {row.asset_type} lists curve {row.curve_id!r} under "
                f"{row.hazard_intensity_metric!r}, but the curve is for {curve_metric[row.curve_id]!r}"
            )
        weight = pd.to_numeric(row.curve_weight, errors="coerce")
        if not pd.isna(row.curve_weight) and (pd.isna(weight) or weight < 0):
            problems.append(f"asset-type-damage-curve: {row.asset_type} / {row.curve_id} needs a non-negative weight")

    used_curves = set(config.asset_curves.curve_id.dropna())
    for curve_id in sorted(used_curves & curve_ids):
        data = config.curve_data.loc[config.curve_data.curve_id == curve_id]
        if data.empty:
            problems.append(f"damage-curve-data: no data points for curve {curve_id!r}")
            continue
        n_missing = data[["hazard_intensity", "damage_fraction"]].isna().any(axis=1).sum()
        if n_missing:
            problems.append(f"damage-curve-data: curve {curve_id!r} has {n_missing} incomplete data points")
        damage = data.damage_fraction.dropna().astype(float)
        if ((damage < 0) | (damage > 1)).any():
            problems.append(f"damage-curve-data: curve {curve_id!r} has damage fractions outside [0, 1]")
        intensity = data.hazard_intensity.dropna().astype(float)
        if intensity.duplicated().any():
            problems.append(f"damage-curve-data: curve {curve_id!r} has repeated intensity values")

    vulnerable = vulnerability.loc[vulnerability._parsed.map(lambda v: v is True)]
    for row in vulnerable.drop_duplicates(["asset_type", "hazard_intensity_metric"]).itertuples():
        weights = config.curve_weights(row.asset_type, row.hazard_intensity_metric)
        if not weights:
            problems.append(f"{row.asset_type} is vulnerable to {row.hazard_intensity_metric} but has no damage curves")
        elif not np.isclose(sum(weights.values()), 1.0):
            problems.append(
                f"asset-type-damage-curve: weights for {row.asset_type} / {row.hazard_intensity_metric} "
                f"sum to {sum(weights.values()):g} (they are normalised, but check this is intended)"
            )
        try:
            config.cost(row.asset_type)
        except (ConfigError, ValueError) as err:
            problems.append(str(err))

    listed = set(zip(config.vulnerability.asset_type, config.vulnerability.hazard_intensity_metric))
    for row in (
        config.asset_curves.dropna(subset=["asset_type", "hazard_intensity_metric"])
        .drop_duplicates(["asset_type", "hazard_intensity_metric"])
        .itertuples()
    ):
        if (row.asset_type, row.hazard_intensity_metric) not in listed:
            problems.append(
                f"asset-type-damage-curve: {row.asset_type} has curves for {row.hazard_intensity_metric} "
                "but no entry in asset-type-hazard-vulnerability"
            )
    # drop repeats, keeping order
    return list(dict.fromkeys(problems))
