"""Calculate direct damages to splits and assets of one layer, for one hazard.

For each asset type in the layer, look up vulnerability, damage curves and
costs from the damage configuration workbook, then sample damages with snail,
with one call per scenario combination (case), for all return periods. Writes
a Zarr store with ``split``, ``asset``, ``asset_type`` and ``layer`` groups
(see ``damages.output``), filled in as each asset type and case is done.

Asset types that are not vulnerable to the hazard's intensity metric get zero
damage. Asset types missing from the workbook, or vulnerable but missing
curves or costs, get NaN damage and a warning (or an error with ``--strict``),
and make the layer total NaN.

Layer totals add up the total damage samples of every asset type, sample by
sample, before taking quantiles and EAD. With ``--systemic-draws shared`` (the
default) all asset types share curve choice and systemic cost draws; with
``independent`` each asset type has its own.

Example:

    python workflow/1-damage/damage_calculate.py \\
        --splits split.gpq \\
        --intensity intensity.zarr \\
        --damage-config example/config/asset-type-cost-curves.xlsx \\
        --hazard coastal_aqueduct \\
        --n-samples 1000 --quantile 0.05 --quantile 0.5 --quantile 0.95 \\
        --output damage.zarr
"""

import json
import logging
import warnings

import click
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr
from snail.damages import expected_annual_damage, summarise_samples

from damages.calculate import (
    INDEPENDENT,
    RETURN_PERIOD,
    SYSTEMIC_DRAWS,
    asset_keys,
    simulate_asset_type,
    stack_cases,
    systemic_key,
)
from damages.config import read_damage_config
from damages.output import ASSET, ASSET_TYPE, LAYER, SPLIT, VARIABLES, DamageStore
from damages.plan import CALCULATED, NOT_CONFIGURED, NOT_VULNERABLE, AssetTypePlan, plan_asset_type
from damages.quantity import split_quantity

# Snakemake writes a .snakemake_timestamp file in directory outputs, which zarr reports when opening the store
warnings.filterwarnings("ignore", message=r"Object at \.snakemake_timestamp is not recognized")

# snail metadata that varies by case, left out of the per-asset-type record
PER_CASE_METADATA = ("n_computed_splits", "n_computed_assets")


@click.command()
@click.option("--splits", "splits_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--intensity", "intensity_path", required=True, type=click.Path(exists=True))
@click.option("--damage-config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--hazard", required=True, help="Hazard key, as listed on the 'hazard' sheet of the damage config")
@click.option("--n-samples", default=10000, show_default=True, type=int)
@click.option("--quantile", "quantiles", multiple=True, type=float, default=(0.05, 0.5, 0.95), show_default=True)
@click.option("--seed", default=None, type=int, help="Random seed in [0, 2**64); by default a random one, recorded")
@click.option("--hazard-cv", default=0.0, show_default=True, type=float)
@click.option("--eta-sd", default=0.0, show_default=True, type=float)
@click.option(
    "--systemic-draws",
    type=click.Choice(SYSTEMIC_DRAWS),
    default="shared",
    show_default=True,
    help="Curve choice and systemic cost draws shared by all asset types, or independent for each",
)
@click.option("--strict/--no-strict", default=False, help="Fail on asset types with incomplete configuration")
@click.option("--output", "output_path", required=True, type=click.Path())
def main(
    splits_path,
    intensity_path,
    config_path,
    hazard,
    n_samples,
    quantiles,
    seed,
    hazard_cv,
    eta_sd,
    systemic_draws,
    strict,
    output_path,
):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    quantiles = sorted(quantiles)
    if seed is None:
        seed = int(np.random.SeedSequence().generate_state(1, np.uint64)[0])
        logging.info("No seed given, using %d", seed)
    config = read_damage_config(config_path)
    metric = config.intensity_metric(hazard)

    columns = [c for c in pq.read_schema(splits_path).names if c != "geometry"]
    splits = pd.read_parquet(splits_path, columns=columns)
    intensity_ds = xr.open_zarr(intensity_path, consolidated=False)
    intensity = intensity_ds["intensity"]
    if (
        intensity.sizes["split"] != len(splits)
        or not (intensity.asset_id.values == splits.asset_id.astype(str).to_numpy()).all()
    ):
        raise click.ClickException(f"{intensity_path} is not aligned with {splits_path}")

    stacked, case_dims = stack_cases(intensity.drop_vars(["asset_id", "split_id", "asset_type"]))
    # (split, case, return_period), float32 as stored: the only input array of the size of the layer
    values = stacked.values
    return_periods = stacked[RETURN_PERIOD].values.astype(float)
    case_index = stacked.indexes["case"]
    n_case, n_rp = len(case_index), len(return_periods)
    # Cases (combinations of scenario dimensions) with no hazard data at all are not computed, and are NaN
    if "has_data" in intensity_ds:
        has_data_da = intensity_ds["has_data"].load().any(RETURN_PERIOD)
        if case_dims:
            has_data = np.asarray([bool(has_data_da.sel(dict(zip(case_dims, key)))) for key in case_index])
        else:
            has_data = np.asarray([bool(has_data_da)])
    else:
        has_data = np.isfinite(values).any(axis=(0, 2))

    asset_type_of_split = splits.asset_type.astype(str).to_numpy()
    asset_ids, asset_index = np.unique(splits.asset_id.astype(str).to_numpy(), return_inverse=True)
    asset_types = sorted(set(asset_type_of_split))
    asset_type_of_asset = pd.Series(asset_type_of_split).groupby(asset_index).first()
    n_splits_of_asset = np.bincount(asset_index, minlength=len(asset_ids))

    # Plan every asset type first, so that --strict fails before any sampling
    plans, quantities, split_positions, asset_positions = {}, {}, {}, {}
    for asset_type in asset_types:
        split_pos = np.flatnonzero(asset_type_of_split == asset_type)
        split_positions[asset_type] = split_pos
        asset_positions[asset_type] = np.unique(asset_index[split_pos])
        plan = plan_asset_type(config, asset_type, metric)
        if plan.status == CALCULATED:
            try:
                quantities[asset_type] = split_quantity(splits.iloc[split_pos], plan.cost.unit)
            except (KeyError, ValueError) as err:
                plan = AssetTypePlan(asset_type, metric, NOT_CONFIGURED, str(err))
        if plan.status == NOT_CONFIGURED:
            if strict:
                raise click.ClickException(f"{asset_type}: {plan.reason}")
            logging.warning("Skipping %s, damage will be NaN: %s", asset_type, plan.reason)
        plans[asset_type] = plan

    status = {t: plans[t].status for t in asset_types}
    currency = {t: plans[t].cost.unit.currency if plans[t].status == CALCULATED else "" for t in asset_types}
    missing_types = [t for t in asset_types if status[t] == NOT_CONFIGURED]
    calculated_types = [t for t in asset_types if status[t] == CALCULATED]
    layer_currencies = sorted({currency[t] for t in calculated_types})
    layer_reason = ""
    if missing_types:
        layer_reason = f"asset types not configured: {', '.join(missing_types)}"
    elif len(layer_currencies) > 1:
        layer_reason = f"asset types are costed in different currencies: {', '.join(layer_currencies)}"
    layer_status = NOT_CONFIGURED if layer_reason else CALCULATED if calculated_types else NOT_VULNERABLE
    layer_attrs = {
        "description": "Totals over every asset of the layer: total damage samples summed over asset types",
        "status": layer_status,
        "reason": layer_reason,
        "currency": layer_currencies[0] if len(layer_currencies) == 1 else "",
        "n_assets": len(asset_ids),
        "asset_types": asset_types,
        "asset_types_calculated": calculated_types,
        "asset_types_not_vulnerable": [t for t in asset_types if status[t] == NOT_VULNERABLE],
        "asset_types_not_configured": missing_types,
    }

    store = DamageStore(
        output_path,
        case_index,
        case_dims,
        {d: intensity[d].values for d in case_dims},
        return_periods,
        quantiles,
        split_coords={
            "asset_id": splits.asset_id.astype(str).to_numpy(),
            "split_id": splits.split_id.to_numpy(dtype=np.int64),
            "asset_type": asset_type_of_split,
        },
        asset_coords={
            "asset_id": asset_ids,
            "asset_type": asset_type_of_asset.to_numpy(dtype=str),
            "n_splits": n_splits_of_asset,
        },
        type_coords={
            "asset_type": np.asarray(asset_types, dtype=str),
            "n_assets": np.asarray([len(asset_positions[t]) for t in asset_types]),
            "status": np.asarray([status[t] for t in asset_types], dtype=str),
            "currency": np.asarray([currency[t] for t in asset_types], dtype=str),
        },
        attrs={
            "title": "Direct damages",
            "hazard": hazard,
            "intensity_metric": metric,
            "hazard_path": intensity.attrs.get("hazard_path", ""),
            "hazard_variable": intensity.attrs.get("hazard_variable", ""),
            "scenario_dims": list(case_dims),
            "n_samples": n_samples,
            "seed": seed,
            "systemic_draws": systemic_draws,
            "damage_config": str(config_path),
        },
        layer_attrs=layer_attrs,
    )

    # Layer total samples for each case, summed over asset types (not vulnerable types add zero)
    layer_samples = np.zeros((n_case, n_samples, n_rp)) if not layer_reason else None
    run_metadata = {}
    for t, asset_type in enumerate(asset_types):
        plan = plans[asset_type]
        split_pos, assets_of_type = split_positions[asset_type], asset_positions[asset_type]
        if plan.status == NOT_CONFIGURED:
            continue
        if plan.status == NOT_VULNERABLE:
            logging.info("%s is not vulnerable to %s, damage is zero", asset_type, metric)
            for case in np.flatnonzero(has_data):
                for group, positions in ((SPLIT, split_pos), (ASSET, assets_of_type), (ASSET_TYPE, [t])):
                    for name, with_rp in VARIABLES[group].items():
                        shape = (len(positions), *([n_rp] if with_rp else []), len(quantiles))
                        store.write(group, name, positions, case, np.zeros(shape))
            continue

        cost, curves, weights = plan.cost, plan.curves, plan.weights
        logging.info(
            "%s: %d splits of %d assets, curves %s, cost %g %s",
            asset_type,
            len(split_pos),
            len(assets_of_type),
            weights,
            cost.mean,
            cost.unit,
        )
        n_computed = 0
        results = simulate_asset_type(
            values[split_pos],
            np.searchsorted(assets_of_type, asset_index[split_pos]),
            quantities[asset_type],
            return_periods,
            curves,
            weights,
            cost,
            keys=asset_keys(asset_ids[assets_of_type]),
            has_data=has_data,
            n_samples=n_samples,
            quantiles=quantiles,
            seed=seed,
            hazard_cv=hazard_cv,
            eta_sd=eta_sd,
            systemic_key=systemic_key(asset_type) if systemic_draws == INDEPENDENT else None,
        )
        for case, result in results:
            if result is None:
                continue  # no hazard data: left NaN
            store.write(SPLIT, "damage_fraction", split_pos, case, result.split_fraction)
            store.write(SPLIT, "damage_cost", split_pos, case, result.split_cost)
            store.write(SPLIT, "ead", split_pos, case, result.split_ead)
            store.write(ASSET, "damage_cost", assets_of_type, case, result.asset_cost)
            store.write(ASSET, "ead", assets_of_type, case, result.asset_ead)
            store.write(ASSET_TYPE, "damage_cost", [t], case, result.total_cost[None])
            store.write(ASSET_TYPE, "ead", [t], case, result.total_ead[None])
            if layer_samples is not None:
                layer_samples[case] += result.total_samples
            n_computed += result.metadata["n_computed_splits"]
            run_metadata[asset_type] = {k: v for k, v in result.metadata.items() if k not in PER_CASE_METADATA}
        logging.info("%s: %d exposed splits over %d cases", asset_type, n_computed, int(has_data.sum()))
        if asset_type in run_metadata:
            run_metadata[asset_type]["n_computed_split_cases"] = n_computed

    if layer_samples is not None:
        for case in np.flatnonzero(has_data):
            samples = layer_samples[case]  # (sample, return_period)
            store.write(LAYER, "damage_cost", None, case, summarise_samples(samples.T, quantiles))
            store.write(
                LAYER, "ead", None, case, summarise_samples(expected_annual_damage(samples, return_periods), quantiles)
            )
    else:
        logging.warning("Layer totals are NaN: %s", layer_reason)

    store.update_attrs({"snail_metadata": json.dumps(run_metadata, default=str)})
    logging.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
