# Direct Damages

Direct damages are the costs of repairing or replacing infrastructure assets
damaged by a hazard event. We estimate them for each asset by overlaying asset
geometries on hazard intensity grids, applying damage curves (hazard intensity
to damage fraction) and multiplying by rehabilitation costs. Expected annual
damages (EAD) are estimated by integrating damages over the return periods
available for each hazard.

The calculation uses [snail](https://github.com/nismod/snail) to split
assets on hazard grids and sample damages under uncertainty.

## Method

For each asset layer:

1. **Split** each asset once, on the grids of all hazards. Each asset is split
   on each distinct grid in turn, so the splits (pieces of a line or polygon,
   or a point) are the common refinement of all the grids: each lies within one
   cell of every grid, and so is exposed to a single value of each hazard
   intensity. An asset that would be split in half at one place on one grid and
   at another place on another grid is split twice, into three pieces. Hazards
   whose grids are identical (same CRS, shape and transform) share one grid,
   and do not split assets further. Splits keep their parent asset's
   attributes, and their length (lines) or area (polygons) is measured in
   metres: planar if the assets are in a projected CRS with metre units,
   otherwise geodesically, on the WGS84 ellipsoid.

   Grids differ in extent. Only the part of an asset inside a grid is split on
   that grid; the part outside is kept as one piece (for each connected part),
   with cell index -1 on that grid, and is split on any other grid that covers
   it. Nothing is dropped, for lines, polygons or points, so the shares of an
   asset's splits always add up to one.

Then for each hazard:

2. **Look up hazard intensity** for each split, from the grid cell it falls in
   on the hazard's own grid, for every return period and every other hazard
   dimension (for example climate scenario, epoch or climate model). Splits
   outside the hazard's grid, or in cells with no data, are taken to have zero
   intensity.
3. **Calculate damages** for each asset type in the layer. The damage
   configuration workbook gives whether the asset type is vulnerable to the
   hazard's intensity metric, the damage curves to use (with weights) and the
   rehabilitation cost.

### Uncertainty

Damages are sampled from specified distributions (see
`snail.damages.simulate_direct_damages` for details) and summarised by
quantiles. For each asset type and each combination of scenario dimensions
(a "case", for example one climate scenario and epoch), one snail call samples
damages for every return period, and summarises them per split, per asset and
in total over the asset type. In each sample:

- one damage curve is chosen for each asset type, with probability in
  proportion to its weight in the workbook
- a systemic cost multiplier is drawn, shared by all assets of a type, with
  coefficient of variation `cost_systemic_cv`
- each asset draws its own cost factor, with coefficient of variation
  `cost_asset_cv`
- optionally (`hazard_cv`, `eta_sd` in `config/config.yaml`) hazard
  intensity and damage fraction are perturbed for each asset

Split, asset, asset type and layer results come from the same samples: in
each sample, the damages to an asset's splits add up to the damage to the
asset, assets add up to the asset type total, and asset types add up to the
layer total. Quantiles are then taken at each level. Quantiles do not add up:
the median damage to an asset is not the sum of median damages to its splits,
and the median layer total is not the sum of the medians of each asset type.

Random draws are keyed, not drawn in sequence. One random seed
(`damage.seed` in `config/config.yaml`) is used for every snail call, across
all asset types, layers and hazards, so results are reproducible. Then:

- each asset's own draws (hazard, damage noise and cost factors) are keyed by
  a 64-bit hash of its `asset_id` (`pandas.util.hash_array` of the id as a
  string). They are independent between assets, and do not depend on the
  order of assets, on which other assets are in the layer, or on the order of
  layers
- the same asset gets the same draws in every case (common random numbers
  across scenarios), so differences between scenarios (e.g. epochs) reflect
  the hazard, not sampling noise
- curve choice and systemic cost draws are set by `damage.systemic_draws`.
  With `"shared"` (the default) they are the same for every asset type, layer
  and hazard: systemic cost uncertainty is economy-wide and perfectly
  correlated between asset types. With `"independent"` each asset type gets
  its own draws, keyed by a hash of the asset type name, independent of other
  asset types (but still the same across cases, layers and hazards). Layer
  totals are usually wider (more uncertain) with shared draws than with
  independent ones. Which is
  right is a modelling choice; zero-uncertainty results are the same either
  way

These random streams differ from earlier versions of this workflow (three
calls to `snail.damages.calculate_direct_damages` per asset type, with draws
in sequence), so sampled results differ from earlier runs. They are
statistically equivalent; results with no uncertainty are unchanged.

With two or more weighted curves for an asset type and no other uncertainty,
quantiles of damage fraction are just values from one curve or the other. When
weights are near equal (e.g. 50/50), the median can switch between curves as the
seed changes, so compare the 5% and 95% quantiles, not only the median.

Layer totals add up, sample by sample, the total damage of each asset type in
the layer, then take quantiles and EAD. Asset types that are not vulnerable add
zero. If any asset type of the layer is missing configuration, or if asset
types are costed in different currencies, the layer total is NaN, and the
`layer` group's attributes say why.

EAD assumes damage varies linearly with the log of annual exceedance
probability between return periods, takes damage at the rarest return period
for rarer events, and takes no damage for events more frequent than the most
frequent return period available. EAD is integrated per sample, then
summarised by quantiles.

### Costs and quantities

Costs are given per unit, with a `cost_unit` such as:

| `cost_unit`             | quantity of each split                                   |
| ----------------------- | -------------------------------------------------------- |
| `mur`                   | share of the whole asset (by length or area; 1 for a point) |
| `mur_per_m`, `mur_per_km` | length (lines only)                                    |
| `mur_per_m2`, `mur_per_km2` | area (polygons only)                                 |
| `mur_per_<attribute>`   | asset attribute value × share, e.g. `mur_per_capacity_mw` reads the `capacity_mw` column |

## Inputs

### Asset layers

The layer table (`config["metadata"]["layer"]`, by default `config/layer.csv`)
lists the asset files to analyse, one row per file:

| column        | description                                                       |
| ------------- | ----------------------------------------------------------------- |
| `layer`       | short name, used in output paths as `layer-{layer}`               |
| `path`        | GeoParquet file, relative to the data root, e.g. `processed/asset/layer-road.gpq` |
| `sector`      | transport, energy, water or buildings                             |
| `description` | free text                                                         |

Layer names must be words (letters, digits and underscores) and unique, and
`layer` and `path` must not be blank. A table with only a header is valid: the
summary is then empty. Each file must have a unique `asset_id` column and an `asset_type` column
whose values are listed in the damage configuration workbook. A file may hold
several asset types and a mix of points, lines and polygons.

### Hazards

The hazard table (`config["metadata"]["hazard"]`, by default
`config/hazard.csv`) lists hazard data cubes, one row per peril and subperil:

| column        | description                                                       |
| ------------- | ----------------------------------------------------------------- |
| `peril`, `subperil` | used in output paths as `peril-{peril}/subperil-{subperil}` |
| `path`        | Zarr store, relative to the data root                             |
| `variable`    | name of the intensity variable in the store                       |
| `hazard`      | key of the hazard on the `hazard` sheet of the damage workbook, which gives its intensity metric |
| `description` | free text                                                         |

`peril` and `subperil` may be any words (letters, digits and underscores),
for example `flood`/`coastal` or `drought`/`meteorological`. Each
(`peril`, `subperil`) pair must be unique, and `peril`, `subperil`, `path`,
`variable` and `hazard` must not be blank. Problems in either table are
reported together, and snakemake stops with the list before running anything.

Each hazard is a separate Zarr store with explicit `y` and `x` coordinates, a
`return_period` dimension (in years) and any number of other dimensions, which
are treated as scenarios. The grid is defined by attributes on the store's root
group, following the GeoZarr
[`spatial:`](https://github.com/zarr-conventions/spatial) and
[`proj:`](https://github.com/zarr-conventions/proj) conventions:

```json
{
  "spatial:dimensions": ["y", "x"],
  "spatial:shape": [51, 54],
  "spatial:transform": [0.00833, 0.0, 57.2667, 0.0, -0.00833, -19.95],
  "spatial:registration": "pixel",
  "proj:code": "EPSG:4326",
  "proj:wkt2": "GEOGCRS[\"WGS 84\", ...]"
}
```

`spatial:transform` is in rasterio/Affine order, `[a, b, c, d, e, f]`, mapping
(column, row) to (x, y). Use `damages.geozarr.with_geozarr_attrs` to add these
attributes to an xarray Dataset with a CRS before writing it.

### Damage configuration workbook

The workbook (`config["damage"]["config"]`, by default
`config/damage/asset-type-cost-curves.xlsx`, which is git-ignored) relates asset types, hazards,
vulnerability, costs and damage curves. Its README sheet describes each column.
The sheets used are:

- `asset-type`: all asset types, matching `asset_type` in the asset data
- `hazard-metric`: hazard intensity metrics, with an optional `unit` column
  (e.g. `m` for `depth_m`, `m/s` for `ws_10min`). If given, the damage report
  checks it against the `units` attribute of each hazard variable, accepting
  known spellings (`m`, `metre`, `meters`; `m/s`, `m s-1`, `metres per second`;
  case-sensitive), and reports a mismatch, an unknown unit, or a variable
  without `units`
- `hazard`: hazard datasets and their intensity metric
- `asset-type-hazard-vulnerability`: whether each asset type is vulnerable to
  each intensity metric
- `asset-type-cost`: rehabilitation cost, unit and coefficients of variation
- `damage-curves` and `damage-curve-data`: piecewise linear damage curves
- `asset-type-damage-curve`: curves to use for each asset type and metric,
  with relative weights

Damage curves from the
[snail damage curve library](https://nismod.github.io/snail/) (Nirandjan et
al.) can be copied into the workbook, as `example/prepare_example_data.py`
does. Key cells (asset types, metrics, hazards, curve identifiers, `vulnerable`
and `curve_weight`) must not be blank. Formula cells read as blank if the file
was saved without calculated values, so open and save the workbook in a
spreadsheet application first. `vulnerable` may be 1/0, true/false or yes/no.
Duplicate asset types, curve identifiers, or conflicting vulnerability rows are
reported. Check the workbook with:

```shell
snakemake -c1 data/results/damage/damage-config-report.txt
```

## Outputs

For each layer, once for all hazards, under `results/damage/layer-{layer}/`:

- `split.gpq`: split geometries (GeoParquet), with parent asset attributes,
  `split_id`, `geom_kind` (`point`, `line`, `polygon` or `other`), `length_m`,
  `area_m2`, `share` and, for each distinct hazard grid `g0`, `g1`, ...,
  columns `index_i_g0`, `index_j_g0`, `index_i_g1`, ... giving the grid column
  and row of the split (-1 if it is outside that grid; a split lies in cell
  `i`, `j` if its midpoint or centroid does, with cells open at their
  right and bottom edges, as in snail). `share` is the split's fraction of
  the whole, unsplit asset, and adds up to one for each asset. A split outside
  a hazard's grid has zero intensity for it, so whole-asset costs (`mur`)
  only damage the share of an asset inside the grid.

  The grids are recorded in the file's schema metadata, under the key
  `mu_star:grids`, as a JSON list with one object per distinct grid:
  `{"id": "g0", "crs": "<WKT2>", "shape": [height, width], "transform":
  [a, b, c, d, e, f]}`. The transform is in the same order as GeoZarr's
  `spatial:transform`. `damage_intensity.py` finds the grid that matches a
  hazard's own, and stops with an error if none does.

Then for each hazard, under
`results/damage/layer-{layer}/rp/peril-{peril}/subperil-{subperil}/`:

- `intensity.zarr`: `intensity` with dimensions `(split, ...hazard dims)`,
  aligned with the rows of `split.gpq`, and `has_data`, with the hazard
  dimensions only, which records whether the hazard has any data on its grid
  for each combination
- `damage.zarr`, with groups:
  - `split`: `damage_fraction`, `damage_cost` and `ead`, with dimensions
    `(split, ...scenario dims, return_period, quantile)` (no `return_period`
    for `ead`), and `asset_id`, `split_id` and `asset_type` coordinates
  - `asset`: `damage_cost` and `ead`, with dimensions `(asset, ...)`, and
    `asset_id`, `asset_type` and `n_splits` coordinates
  - `asset_type`: totals over all assets of each type, with `n_assets`,
    `status` (`calculated`, `not_vulnerable` or `not_configured`) and
    `currency` coordinates
  - `layer`: totals over all assets of the layer, with dimensions
    `(...scenario dims, return_period, quantile)`, and attributes `status`,
    `reason` (why the totals are NaN, if they are), `currency`, `n_assets`,
    `asset_types`, `asset_types_calculated`, `asset_types_not_vulnerable` and
    `asset_types_not_configured`

  The root group's attributes record the hazard, the scenario dimensions,
  `n_samples`, `seed` (drawn at random, and recorded, if none is given),
  `systemic_draws` and snail's metadata for each asset type (including its
  draw scheme). Damage variables are float32. They are chunked in pieces of
  50,000 splits or assets, whole along the other dimensions. String
  coordinates are stored as variable-length strings.

Splits with zero length or area (slivers) have quantity, and so cost, zero;
an asset type is only an error if its geometries are of the wrong kind for its
cost unit. After splitting, `damage_split.py` compares each asset's total split
length or area with the unsplit asset's (in the same CRS) and logs a warning
if any differ by more than 0.1%, which should not happen since nothing is dropped.

Asset types not vulnerable to a hazard have zero damage. Asset types missing
from the workbook, or vulnerable but missing curves or costs, have NaN damage
(or stop the workflow if `config["damage"]["strict"]` is true). Combinations
of scenario dimensions with no hazard data anywhere on the grid (e.g. a future
scenario in the baseline epoch) are NaN for every asset type, including those
not vulnerable. Assets in cells with no data, or outside the grid, in a
combination that does have data elsewhere have zero damage.

Read the outputs with xarray, for example:

```python
import xarray as xr

damage = "data/results/damage/layer-road/rp/peril-flood/subperil-coastal/damage.zarr"
assets = xr.open_zarr(damage, group="asset", consolidated=False)
assets.ead.sel(quantile=0.5, rcp="rcp8p5", epoch=2050)
```

Across all layers and hazards, `results/damage/summary.csv` is a long table of
the `asset_type` and `layer` totals. Its `level` column is `asset_type` for
the total over the assets of one type, named in the `asset_type` column, and
`layer` for the total over every asset of the layer, with an empty
`asset_type`. Don't add up rows of both levels.

## Running

```shell
# Report available layers and hazards, what will be calculated and what is missing
snakemake -c1 damage_report
# Check the damage configuration workbook
snakemake -c1 data/results/damage/damage-config-report.txt
# Split one layer on the grids of all hazards
snakemake -c1 data/results/damage/layer-road/split.gpq
# Damage to one layer from one hazard
snakemake -c1 data/results/damage/layer-road/rp/peril-flood/subperil-pluvial/damage.zarr
# All layers and hazards, and the summary
snakemake -c4 data/results/damage/summary.csv
```

The damage report (`workflow/1-damage/damage_report.py`, written to
`results/damage/report.md` under the data root) checks each layer file
(required columns, asset types known to the workbook, geometry), each hazard
store (variable, `return_period` dimension, GeoZarr grid attributes, hazard key
in the workbook), which hazards share a grid (the number of distinct grids is the
number each layer is split on), and, for each layer, hazard and asset type, whether damage
will be calculated (with which costs and curves), is zero because the asset
type is not vulnerable, or is missing configuration, and why. It also lists
which damage outputs exist already. It reads hazard metadata only, so it runs
quickly, and reports a missing workbook or empty tables rather than failing;
`snakemake --dry-run` shows exactly which jobs will run. Set
`data_root` in the config to report on data elsewhere than `data/`.

With no layers or hazards, `summary.csv` is a header-only table (without
scenario or quantile columns).

Because each layer is split on the grids of all hazards, adding a hazard to the
hazard table, or changing a hazard store, re-splits every layer, and so re-runs
the intensity and damage calculations for every hazard of those layers, not only
the new one. Hazards on a grid already in use do not make splits any finer, but
still change the inputs, so still re-split the layer (to the same result).

Each rule runs a script in `workflow/1-damage/`, which can also be run directly
(see `--help`). Reusable functions are in the `damages` package, `src/damages`.

Configuration options are in the `damage` section of `config/config.yaml`,
including the number of samples (`n_samples`, default 10,000), quantiles
(default 5%, 50% and 95%), random seed and `systemic_draws` (`"shared"` or
`"independent"`, see [Uncertainty](#uncertainty)).

### Example

The `example/` directory has a small set of open data around Port Louis, with
an example damage workbook with illustrative costs. See `example/README.md`.

```shell
snakemake -c2 example/results/damage/summary.csv --configfile example/config.yaml
```

## Limitations and next steps

- Splitting on all grids multiplies the number of splits when hazard grids are
  misaligned: a line crossing a cell edge of each of several grids is cut at all
  of them, so layers split on many different grids have more splits (and so
  slower damage calculations) than on any one. Hazards that share a grid add
  nothing. The splitting itself is also repeated on every layer when any hazard
  changes.
- Hazard intensity is the value of the cell a split falls in; there is no
  sub-grid information.
- Splits with zero intensity in all return periods of a case are skipped by
  snail, when curves have zero damage at zero intensity, so time grows with the
  number of exposed splits × scenario combinations. Measured on a 4-core cloud
  container, at 10,000 samples with cost uncertainty, 12 scenario combinations
  × 9 return periods and two road asset types (two weighted curves each):
  - 100,000 synthetic line splits (20,000 assets), 5% flooded: about 110 s and 1.0 GB
    peak memory (previously about 30 minutes and 2 GB for 101,795 real road
    splits at 5%)
  - 500,000 synthetic line splits (100,000 assets), 2% flooded: about 260 s
    and 2.4 GB (previously more than 8 GB)
  - memory no longer grows with the size of the outputs, which are written to
    the Zarr store as each asset type and scenario combination is done. It
    grows with the hazard intensity, held in memory for the layer as float32
    (splits × scenario combinations × return periods), and with snail's
    results for one asset type and case. Writing re-reads and re-writes each
    50,000-split chunk once per asset type and case, which takes a few hundred
    MB of the peak
  - hazards that affect every split (e.g. wind) are far slower than floods:
    their time is mostly in sorting samples for quantiles
- Layer totals are per hazard. Totals across layers or hazards would need the
  layer total samples to be kept (`n_samples` × return periods per case).
- Impairment curves (loss of function) are not yet used.
