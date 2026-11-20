"""Calculate direct damages to splits and assets of one layer, for one hazard.

For each asset type in the layer, look up vulnerability, damage curves and
costs from the damage configuration workbook, then sample damages with snail
across all hazard scenarios and return periods. Writes a Zarr store with
``split``, ``asset`` and ``asset_type`` groups (see ``damages.output``).

Asset types that are not vulnerable to the hazard's intensity metric get zero
damage. Asset types missing from the workbook, or vulnerable but missing
curves or costs, get NaN damage and a warning (or an error with ``--strict``).

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

import click
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr
import zarr

from damages.calculate import RETURN_PERIOD, sample_direct_damages, stack_cases
from damages.config import read_damage_config
from damages.output import build_datasets, empty_arrays, insert_arrays
from damages.plan import CALCULATED, NOT_CONFIGURED, NOT_VULNERABLE, AssetTypePlan, plan_asset_type
from damages.quantity import split_quantity


@click.command()
@click.option("--splits", "splits_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--intensity", "intensity_path", required=True, type=click.Path(exists=True))
@click.option("--damage-config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--hazard", required=True, help="Hazard key, as listed on the 'hazard' sheet of the damage config")
@click.option("--n-samples", default=10000, show_default=True, type=int)
@click.option("--quantile", "quantiles", multiple=True, type=float, default=(0.05, 0.5, 0.95), show_default=True)
@click.option("--seed", default=None, type=int)
@click.option("--hazard-cv", default=0.0, show_default=True, type=float)
@click.option("--eta-sd", default=0.0, show_default=True, type=float)
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
    strict,
    output_path,
):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    quantiles = sorted(quantiles)
    config = read_damage_config(config_path)
    metric = config.intensity_metric(hazard)

    columns = [c for c in pq.read_schema(splits_path).names if c != "geometry"]
    splits = pd.read_parquet(splits_path, columns=columns)
    intensity = xr.open_zarr(intensity_path, consolidated=False)["intensity"].load()
    if (
        intensity.sizes["split"] != len(splits)
        or not (intensity.asset_id.values == splits.asset_id.astype(str).to_numpy()).all()
    ):
        raise click.ClickException(f"{intensity_path} is not aligned with {splits_path}")

    stacked, case_dims = stack_cases(intensity.drop_vars(["asset_id", "split_id", "asset_type"]))
    values = stacked.values
    return_periods = stacked[RETURN_PERIOD].values.astype(float)
    case_index = stacked.indexes["case"]

    asset_ids, asset_index = np.unique(splits.asset_id.astype(str).to_numpy(), return_inverse=True)
    asset_types = sorted(splits.asset_type.astype(str).unique())
    asset_type_of_asset = pd.Series(splits.asset_type.astype(str).to_numpy()).groupby(asset_index).first()
    n_splits_of_asset = np.bincount(asset_index, minlength=len(asset_ids))

    arrays = empty_arrays(
        len(splits), len(asset_ids), len(asset_types), len(case_index), len(return_periods), len(quantiles)
    )
    status, cost_units, n_assets, run_metadata = {}, {}, {}, {}

    for t, asset_type in enumerate(asset_types):
        split_pos = np.flatnonzero(splits.asset_type.astype(str).to_numpy() == asset_type)
        assets_of_type = np.unique(asset_index[split_pos])
        n_assets[asset_type] = len(assets_of_type)
        local_asset = np.searchsorted(assets_of_type, asset_index[split_pos])

        plan = plan_asset_type(config, asset_type, metric)
        status[asset_type] = plan.status
        cost_units[asset_type] = plan.cost.unit.currency if plan.cost else ""
        if plan.status == CALCULATED:
            try:
                quantity = split_quantity(splits.iloc[split_pos], plan.cost.unit)
            except (KeyError, ValueError) as err:
                plan = AssetTypePlan(asset_type, metric, NOT_CONFIGURED, str(err))
                status[asset_type] = plan.status
                cost_units[asset_type] = ""

        if plan.status == NOT_CONFIGURED:
            if strict:
                raise click.ClickException(f"{asset_type}: {plan.reason}")
            logging.warning("Skipping %s, damage will be NaN: %s", asset_type, plan.reason)
            continue

        if plan.status == NOT_VULNERABLE:
            logging.info("%s is not vulnerable to %s, damage is zero", asset_type, metric)
            for key in arrays:
                group = key.split("/")[0]
                if group == "split":
                    arrays[key][split_pos] = 0.0
                elif group == "asset":
                    arrays[key][assets_of_type] = 0.0
                else:
                    arrays[key][t] = 0.0
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
        result = sample_direct_damages(
            values[split_pos],
            local_asset,
            quantity,
            return_periods,
            curves,
            weights,
            cost,
            n_assets=len(assets_of_type),
            n_samples=n_samples,
            quantiles=quantiles,
            seed=seed,
            hazard_cv=hazard_cv,
            eta_sd=eta_sd,
        )
        insert_arrays(arrays, result, split_pos, assets_of_type, t)
        run_metadata[asset_type] = result.metadata

    datasets = build_datasets(
        arrays,
        case_index,
        case_dims,
        return_periods,
        quantiles,
        split_coords={
            "asset_id": splits.asset_id.astype(str).to_numpy(),
            "split_id": splits.split_id.to_numpy(dtype=np.int64),
            "asset_type": splits.asset_type.astype(str).to_numpy(),
        },
        asset_coords={
            "asset_id": asset_ids,
            "asset_type": asset_type_of_asset.to_numpy(dtype=str),
            "n_splits": n_splits_of_asset,
        },
        type_coords={
            "asset_type": np.asarray(asset_types, dtype=str),
            "n_assets": np.asarray([n_assets[t] for t in asset_types]),
            "status": np.asarray([status[t] for t in asset_types], dtype=str),
            "currency": np.asarray([cost_units[t] for t in asset_types], dtype=str),
        },
    )

    root = zarr.open_group(output_path, mode="w")
    root.attrs.update(
        {
            "title": "Direct damages",
            "hazard": hazard,
            "intensity_metric": metric,
            "hazard_path": intensity.attrs.get("hazard_path", ""),
            "hazard_variable": intensity.attrs.get("hazard_variable", ""),
            "scenario_dims": list(case_dims),
            "n_samples": n_samples,
            "seed": seed,
            "damage_config": str(config_path),
            "snail_metadata": json.dumps(run_metadata, default=str),
        }
    )
    for group, ds in datasets.items():
        ds.to_zarr(output_path, group=group, mode="w", consolidated=False)
    logging.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
