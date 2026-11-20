"""Decide how damage will be calculated for an asset type and hazard metric.

Used by the damage calculation, to choose what to do for each asset type, and
by the damage report, to show in advance what will be calculated and what
configuration is missing.

TODO - review this - is it entirely overdone, better handled by snakemake? and/or config parsing?
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from snail.damages import DamageCurve

from damages.config import ConfigError, CostParameters, DamageConfig
from damages.quantity import GEOMETRY_KINDS

CALCULATED = "calculated"
NOT_VULNERABLE = "not_vulnerable"
NOT_CONFIGURED = "not_configured"


@dataclass
class AssetTypePlan:
    asset_type: str
    metric: str
    status: str
    reason: str = ""
    cost: CostParameters | None = None
    curves: dict[str, DamageCurve] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)


def plan_asset_type(
    config: DamageConfig,
    asset_type: str,
    metric: str,
    assets: pd.DataFrame | None = None,
) -> AssetTypePlan:
    """Plan damage calculation for one asset type and intensity metric.

    Parameters
    ----------
    config : DamageConfig
        Damage configuration workbook.
    asset_type, metric : str
        Asset type and hazard intensity metric.
    assets : pandas.DataFrame, optional
        Assets of this type. If given, check that cost units suit their
        geometry types (from a ``geom_type`` column, e.g.
        ``GeoSeries.geom_type``) and that any attribute that costs are scaled
        by is present, finite and non-negative.

    Returns
    -------
    AssetTypePlan
        ``status`` is "calculated", "not_vulnerable" (zero damage) or
        "not_configured" (damage cannot be calculated, see ``reason``).
    """

    def blocked(reason):
        return AssetTypePlan(asset_type, metric, NOT_CONFIGURED, reason)

    vulnerable = config.is_vulnerable(asset_type, metric)
    if vulnerable is None:
        if asset_type not in set(config.asset_types.asset_type):
            return blocked(f"asset type {asset_type!r} is not listed in the workbook")
        return blocked(f"no vulnerability entry for {metric!r}")
    if not vulnerable:
        return AssetTypePlan(asset_type, metric, NOT_VULNERABLE, f"not vulnerable to {metric!r}")

    try:
        cost = config.cost(asset_type)
    except (ConfigError, ValueError) as err:
        return blocked(str(err))
    try:
        curves, weights = config.curves_for(asset_type, metric)
    except ConfigError as err:
        return blocked(str(err))
    if not curves:
        return blocked(f"vulnerable to {metric!r} but has no damage curves")
    if not np.isfinite(list(weights.values())).all() or sum(weights.values()) <= 0:
        return blocked(f"damage curve weights for {metric!r} must be finite, non-negative and sum > 0")

    if assets is not None:
        reason = _check_assets(assets, cost)
        if reason:
            return blocked(reason)

    return AssetTypePlan(asset_type, metric, CALCULATED, cost=cost, curves=curves, weights=weights)


def _check_assets(assets: pd.DataFrame, cost: CostParameters) -> str:
    """Reason the assets can't be costed in the given units, or empty."""
    unit = cost.unit
    if "geom_type" in assets:
        kinds = set(assets["geom_type"].map(GEOMETRY_KINDS).fillna("other"))
        required = {"length": "line", "area": "polygon"}.get(unit.kind)
        if required and kinds != {required}:
            return f"cost unit {unit.currency}_per_{unit.per} needs {required} geometries, found {sorted(kinds)}"
    if unit.kind == "attribute":
        if unit.per not in assets:
            return f"cost unit is per {unit.per!r}, but assets have no {unit.per!r} column"
        values = pd.to_numeric(assets[unit.per], errors="coerce")
        n_bad = int((~np.isfinite(values) | (values < 0)).sum())
        if n_bad:
            return f"{n_bad} assets have missing, non-numeric or negative {unit.per!r}"
    return ""
