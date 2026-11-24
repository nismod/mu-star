# Design: using a single-call snail interface for direct damages

Status: implemented. Sections 1 to 4 are implemented, with snail pinned to
commit `90f46f3` of its `feat/direct-damage-interface` branch until
`simulate_direct_damages` is released. Deviations and additions are noted in
each section, and in [section 6](#6-as-built).

This responds to review on [#42](https://github.com/nismod/mu-star/pull/42) of
the three `calculate_direct_damages` calls in `src/damages/calculate.py`. The
snail side is proposed in `docs/design/direct-damages-arrays.md` on the snail
branch `feat/direct-damage-interface`: a `simulate_direct_damages` function
that takes arrays for one scenario combination and returns split, asset and
total quantiles, plus optional total samples, all from one set of draws keyed
by asset.

This note covers what changes in mu-star: splitting each layer once on all
hazard grids, and making one snail call per asset type and scenario
combination.

## 1. Split once per layer, on all grids (implemented)

Previously `split_layer` ran once per layer and hazard, so a layer was split
again for every hazard. An earlier proposal was an optional `grid` column in the
hazard table, so hazards declared to share a grid would share splits. The
maintainer's requirement is stronger: split each layer once on all hazard grids,
so that a split is a consistent unit exposed to a single value of each hazard
intensity, whatever the grid of that hazard. An asset that would be split in
half on one grid, and at a different place on another, is split twice, into
three pieces, and these final splits are used for attribution on every hazard.
There is no `grid` column: grids are read from the hazard stores, and identical
grids are detected rather than declared.

What was built:

- `damages.split.split_on_grids(features, grids)` (a pure function). It
  deduplicates grids (same CRS and shape, transform within a small tolerance),
  then splits successively on each distinct grid with snail's `split_features`,
  so the pieces are the common refinement of the grids. It then computes cell
  indices on every distinct grid for every final piece, into columns
  `index_i_g0`, `index_j_g0`, `index_i_g1`, ..., following snail's convention
  (-1 outside the grid), keeps parent attributes and measures the splits with
  `measure_splits`. It returns the splits and a list of grid definitions.
- Grids differ in extent. Snail clips polygons to the grid and keeps the outside
  part of lines (index -1, cut at gridlines beyond the grid), so splitting
  successively on several grids would drop parts of polygons that lie outside
  one grid but inside another. For each grid, only the part of each geometry
  inside the grid's extent is split, and the part outside is kept whole (one
  piece per connected part) for all geometry kinds. Nothing is ever dropped, and
  shares of an asset's splits always sum to one. Points are never split.
- One `results/damage/layer-{layer}/split.gpq` per layer, with all hazards in
  the hazard table as inputs of `split_layer`. `intensity.zarr` and `damage.zarr`
  stay per hazard, under `rp/peril-{peril}/subperil-{subperil}/`, and use the
  layer's split file.
- `damage_split.py` takes `--hazard` several times and records the distinct
  grids in the GeoParquet schema metadata, under `mu_star:grids`: a JSON list of
  `{"id", "crs" (WKT2), "shape" ([height, width]), "transform" ([a, b, c, d, e, f])}`.
  `damage_intensity.py` finds the recorded grid matching the hazard's own grid
  and looks up intensity with that grid's index columns. It fails with a clear
  message if none matches, so a hazard that was not among those the layer was
  split on cannot silently mis-attribute intensities.
- `damage_report.py` reports the number of distinct grids and which hazards share
  each.

Consequences. Adding or changing a hazard changes the inputs of `split_layer`,
so the layer is re-split and all its downstream intensities and damages re-run.
Misaligned grids make more splits (a line crossing a cell edge of each of `k`
grids has up to `k` times as many pieces as on one), which costs time in the
damage calculation but is what makes hazards consistent on one split.

## 2. One snail call per asset type and case (implemented)

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

## 3. Layer totals and the summary (implemented)

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

## 4. Memory: write as we go (implemented)

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

## 5. Order of work (done, apart from a snail release)

1. snail: implement `simulate_direct_damages`, keyed draws, helpers and tests
   on `feat/direct-damage-interface`; agree the open questions first.
2. mu-star: switch `src/damages/calculate.py` to the new call, in a loop over
   asset types and cases; layer totals; incremental zarr writes. Pin snail to
   the branch commit while it is unreleased.
3. mu-star: split once on all grids (done; independent of 1 and 2).
4. Re-run the benchmark at realistic sizes and update the measured numbers in
   `docs/src/damages.md`.

## 6. As built

Section 2. `damages.calculate.simulate_asset_type` is a generator over cases
for one asset type, yielding snail's `DirectDamageArrays` for each case (None
for cases with no hazard data, from `has_data` in `intensity.zarr`, which are
not computed and stay NaN at every level). Within a case with data, NaN or
negative intensity is zero. Asset keys are `pandas.util.hash_array` of the
asset id as a string (`damages.calculate.asset_keys`), so draws are stable
across cases, runs and the order of assets or layers. Common random numbers
across cases were accepted. `--seed` defaults to a random seed, which is
recorded.

Addition: `damage.systemic_draws` in `config/config.yaml` (`"shared"`, the
default, or `"independent"`), passed as `--systemic-draws` and recorded in the
root attributes of `damage.zarr`. `"independent"` passes snail a
`systemic_key` hashed from the asset type name, so each asset type gets its
own curve choice and systemic cost draws.

Section 3. The `layer` group has `damage_cost (scenario dims..., return_period,
quantile)` and `ead (scenario dims..., quantile)`, with attributes listing the
asset types calculated, not vulnerable (adding zero) and not configured.
Additions: the layer total is NaN, with a `reason` attribute, if any asset type
is not configured or if asset types are costed in different currencies. The
summary table has a `level` column, `asset_type` or `layer`, with an empty
`asset_type` for layer rows, and the layer's `status`, `currency` and total
`n_assets`.

Section 4. `damages.output.DamageStore` creates every group up front, float32
and NaN-filled, chunked by 50,000 splits or assets and whole along scenario,
return period and quantile dimensions, then writes each (asset type, case)
result into place (slices for contiguous positions, orthogonal selection
otherwise). String coordinates are variable-length strings (object arrays in
xarray), which zarr v3 stores as `string` without a warning. Deviation: hazard
intensity is read once for the layer, as float32 (`splits × cases × return
periods`, 43 MB for 100,000 splits and 108 cases), not one case at a time,
because the loop is over asset types, then cases. Because chunks are whole
along the scenario dimensions, each case's write re-reads and re-writes the
chunks holding that asset type's splits. This was about 10 s of a 110 s run
and a few hundred MB of peak memory in the benchmark below; chunks of one case
each would avoid it, at the cost of more, smaller chunks when reading all
scenarios of a split.

Measured, on a 4-core cloud container, 10,000 samples, cost uncertainty, 12
cases × 9 return periods, two road asset types with two weighted curves each
(synthetic inputs):

| Splits | Exposed | Time | Peak RSS |
|---|---|---|---|
| 100,000 | 5% | 110 s | 1.0 GB |
| 500,000 | 2% | 261 s | 2.4 GB |

Before: 29 minutes and 2 GB for 101,795 real road splits at 5%, and more than
8 GB for 500,000 splits (the `chunked_calculate.py` prototype: 458 s and 1.9 GB
at 2%). About 90% of the time is in snail, mostly sorting
samples for quantiles.

Not done: `pixi.lock` still pins snail `v0.6.0`, as `pixi lock` could not reach
the conda-PyPI name mapping from the environment it was run in. Re-lock with
network access.
