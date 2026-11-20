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

For each asset layer and each hazard:

1. **Split** each asset on the hazard grid, so each split (piece of a line or
   polygon, or a point) falls within one grid cell. Splits keep their parent
   asset's attributes, and their length (lines) or area (polygons) is measured
   in a projected CRS.
2. **Look up hazard intensity** for each split, from the grid cell it falls in,
   for every return period and every other hazard dimension (for example
   climate scenario, epoch or climate model). Splits outside the grid, or in
   cells with no data, are taken to have zero intensity.
3. **Calculate damages** for each asset type in the layer. The damage
   configuration workbook gives whether the asset type is vulnerable to the
   hazard's intensity metric, the damage curves to use (with weights) and the
   rehabilitation cost.

### Uncertainty

Damages are sampled from specified distributions (see
`snail.damages.calculate_direct_damages` for details) and summarised by
quantiles. In each sample:

- one damage curve is chosen for each asset type, with probability in
  proportion to its weight in the workbook
- a systemic cost multiplier is drawn, shared by all assets of a type, with
  coefficient of variation `cost_systemic_cv`
- each asset draws its own cost factor, with coefficient of variation
  `cost_asset_cv`
- optionally (`hazard_cv`, `eta_sd` in `config/config.yaml`) hazard
  intensity and damage fraction are perturbed for each asset

Curve choice and systemic cost draws are shared across all hazard scenarios, so
differences between scenarios (e.g. epochs) are not confounded by them.

Quantiles of damage are reported for each split, each asset, and for the total
over all assets of each type. Quantiles do not add up: the median damage to an
asset is not the sum of median damages to its splits, and the median total
damage to a layer can't be found by adding medians of each asset type.

EAD assumes damage varies linearly with the log of annual exceedance
probability between return periods, takes damage at the rarest return period
for rarer events, and takes no damage for events more frequent than the most
frequent return period available.

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
  checks it against the `units` attribute of each hazard variable, treating
  common spellings as equal (`m`, `metre`, `meters`; `m/s`, `m s-1`,
  `metres per second`), and reports a mismatch, or a variable without `units`
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

For each layer and hazard, under
`results/damage/layer-{layer}/rp/peril-{peril}/subperil-{subperil}/`:

- `split.gpq`: split geometries (GeoParquet), stored once, with parent asset
  attributes, `split_id`, grid indices `index_i`/`index_j`, `geom_kind`
  (`point`, `line`, `polygon` or `other`), `length_m`, `area_m2` and `share`.
  `share` is the split's fraction of the whole, unsplit asset. Polygons are
  clipped to the hazard grid, so the shares of a polygon partly outside the
  grid add up to less than one, and only the part inside can be damaged.
- `intensity.zarr`: `intensity` with dimensions `(split, ...hazard dims)`,
  aligned with the rows of `split.gpq`, and `has_data`, with the hazard
  dimensions only, which records whether the hazard has any data on its grid
  for each combination
- `damage.zarr`, with groups:
  - `split`: `damage_fraction`, `damage_cost` and `ead`, with dimensions
    `(split, ...scenario dims, return_period, quantile)` (no `return_period`
    for `ead`), and `asset_id`, `split_id` and `asset_type` coordinates
  - `asset`: `damage_cost` and `ead`, with dimensions `(asset, ...)`
  - `asset_type`: totals over all assets of each type, with `n_assets`,
    `status` (`calculated`, `not_vulnerable` or `not_configured`) and
    `currency` coordinates

Splits with zero length or area (slivers) have quantity, and so cost, zero;
an asset type is only an error if its geometries are of the wrong kind for its
cost unit. After splitting, `damage_split.py` compares each asset's total split
length or area with the unsplit asset's (in the same CRS) and logs a warning
if any differ by more than 0.1%.

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
the `asset_type` totals.

## Running

```shell
# Report available layers and hazards, what will be calculated and what is missing
snakemake -c1 damage_report
# Check the damage configuration workbook
snakemake -c1 data/results/damage/damage-config-report.txt
# Damage to one layer from one hazard
snakemake -c1 data/results/damage/layer-road/rp/peril-flood/subperil-pluvial/damage.zarr
# All layers and hazards, and the summary
snakemake -c4 data/results/damage/summary.csv
```

The damage report (`workflow/1-damage/damage_report.py`, written to
`results/damage/report.md` under the data root) checks each layer file
(required columns, asset types known to the workbook, geometry), each hazard
store (variable, `return_period` dimension, GeoZarr grid attributes, hazard key
in the workbook), and, for each layer, hazard and asset type, whether damage
will be calculated (with which costs and curves), is zero because the asset
type is not vulnerable, or is missing configuration, and why. It also lists
which damage outputs exist already. It reads hazard metadata only, so it runs
quickly, and reports a missing workbook or empty tables rather than failing;
`snakemake --dry-run` shows exactly which jobs will run. Set
`data_root` in the config to report on data elsewhere than `data/`.

With no layers or hazards, `summary.csv` is a header-only table (without
scenario or quantile columns).

Each rule runs a script in `workflow/1-damage/`, which can also be run directly
(see `--help`). Reusable functions are in the `damages` package, `src/damages`.

Configuration options are in the `damage` section of `config/config.yaml`,
including the number of samples (`n_samples`, default 10,000), quantiles
(default 5%, 50% and 95%) and random seed.

### Example

The `example/` directory has a small set of open data around Port Louis, with
an example damage workbook with illustrative costs. See `example/README.md`.

```shell
snakemake -c2 example/results/damage/summary.csv --configfile example/config.yaml
```

## Limitations and next steps

- Splits are recalculated for each hazard, even if hazards share a grid.
- Hazard intensity is the value of the cell a split falls in; there is no
  sub-grid information.
- Per-split damages are calculated by a separate snail call from per-asset
  damages (marginal quantiles of each). Per-split sampling is slow for very
  large layers; splits with zero intensity in all return periods are skipped
  when curves have zero damage at zero intensity.
- Layer totals across asset types need snail to sample several asset types
  (with their own curves and costs) in one call, or to return samples.
- Impairment curves (loss of function) are not yet used.
