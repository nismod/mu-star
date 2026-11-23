# Design: using a single-call snail interface for direct damages

Status: proposal, for discussion. Nothing here is implemented yet.

This responds to review on [#42](https://github.com/nismod/mu-star/pull/42) of
the three `calculate_direct_damages` calls in `src/damages/calculate.py`. The
snail side is proposed in `docs/design/direct-damages-arrays.md` on the snail
branch `feat/direct-damage-interface`: a `simulate_direct_damages` function
that takes arrays for one scenario combination and returns split, asset and
total quantiles, plus optional total samples, all from one set of draws keyed
by asset.

This note covers what changes in mu-star: computing splits once per grid, and
making one snail call per asset type and scenario combination.

## 1. Split once per layer and grid

Today `split_layer` runs once per layer and hazard, so a layer is split again
for every hazard, even when hazards share a grid (for example several flood
scenarios or models on the same 1/120° grid).

Proposal:

- Add an optional `grid` column to the hazard table. Hazards with the same
  `grid` value share splits. It defaults to `{peril}-{subperil}`, so the
  current behaviour is unchanged unless set.
- Splits go to `results/damage/layer-{layer}/grid-{grid}/split.gpq`.
  `intensity.zarr` and `damage.zarr` stay per hazard and refer to those splits.
- `damage_split.py` records the grid definition (the GeoZarr `spatial:` and
  `proj:` attributes) in the GeoParquet file metadata. `damage_intensity.py`
  checks that each hazard's grid matches it, and fails with a clear message if
  not, so a wrong `grid` value cannot silently mis-attribute intensities.
- `damage_report.py` reports which hazards share each grid, and any mismatch.

The `grid` value is user-declared rather than derived from the zarr
attributes, because snakemake needs it when building the DAG, before the
hazard stores necessarily exist. The check at the intensity step keeps it
honest.

## 2. One snail call per asset type and case

`sample_direct_damages` in `src/damages/calculate.py` becomes a thin loop:

```text
for asset_type in layer:                       # curves and costs differ by type
    plan = plan_asset_type(...)                # unchanged
    for case in scenario combinations:         # e.g. (rcp, epoch)
        result = snail.simulate_direct_damages(
            intensity[splits_of_type, case, :],  # (split, return_period)
            asset_index, quantity,
            curves, weights, cost..., return_periods,
            seed=config seed,
            asset_keys=hash(asset_id),           # stable across cases and runs
            keep_total_samples=True,
        )
        write result arrays into the output zarr at (splits_of_type, case)
        add result.total_samples into the layer total for this case
```

What goes away:

- the three calls, the all-ones quantity and cost call, and the long-frame
  building (`_long_frame`, `_fill`)
- packing every case into snail's `scenario` column, then unstacking
- mu-star's own zero-intensity skipping, which moves into snail
- the comment explaining why per-split and per-asset results differ sample by
  sample, because they no longer do

What changes in results:

- **Random streams change.** Results are statistically equivalent, not
  identical, to the current branch. Re-run the example and update tests that pin
  sampled values. The end-to-end tests use zero uncertainty, so their expected
  values do not change.
- **Common random numbers across cases.** Each asset gets the same local draws
  in every scenario combination, as well as the same curve choice and systemic
  draws (shared today). Scenario differences are then cleaner. This needs your
  agreement (open question 1 in the snail note).
- **Split, asset and total results are consistent sample by sample.**

## 3. Layer totals and the summary

Because total samples from different calls share curve-choice and systemic
draws at each sample index, they can be added:

- per case, sum `total_samples` across asset types to get the layer total
  samples, then take quantiles and EAD with snail's `summarise_samples` and
  `expected_annual_damage`
- write these to a new `layer` group in `damage.zarr`, with
  `damage_cost (scenario dims..., return_period, quantile)` and `ead`
- `damage_summary.py` reports layer totals as well as asset-type totals, and
  the limitation note about layer totals is removed from `docs/src/damages.md`

Totals across layers or hazards could be added the same way later, by keeping
the per-case layer total samples (`n_samples × n_return_period` floats per
case, small).

## 4. Memory: write as we go

With one call per (asset type, case), outputs no longer need to be held as
dense arrays for the whole layer:

- create `damage.zarr` up front with float32 variables, chunked along splits
  (about 50,000) and whole along scenario, return-period and quantile axes
- write each call's arrays by region; never hold the full layer in memory
- read intensity for one case at a time
- store string ids as variable-length strings, or as an integer key with a
  lookup table, to avoid the zarr v3 fixed-length string warning

The benchmark prototype of this layout (`chunked_calculate.py`) ran 500,000
splits at 2% exposure in 458 s and 1.9 GB, where the current code exceeds 8 GB.
Because snail's draws are keyed by asset, the damage step can later be split
into chunks of assets or run in parallel without changing results.

## 5. Order of work

1. snail: implement `simulate_direct_damages`, keyed draws, helpers and tests
   on `feat/direct-damage-interface`; agree the open questions first.
2. mu-star: switch `src/damages/calculate.py` to the new call, in a loop over
   asset types and cases; layer totals; incremental zarr writes. Pin snail to
   the branch commit while it is unreleased.
3. mu-star: split once per grid (independent of 1 and 2, can go first).
4. Re-run the benchmark at realistic sizes and update the measured numbers in
   `docs/src/damages.md`.
