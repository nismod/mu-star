# Example data

A small set of open data around Port Louis, Mauritius, to test and
demonstrate the direct damages workflow. It is laid out like the project data
root, so the workflow runs on it with:

```shell
snakemake -c2 example/results/damage/summary.csv --configfile example/config.yaml
```

Results are written to `example/results/` (not committed).

## Contents

- `config.yaml`: configuration merged over `config/config.yaml`, using the
  tables below and fewer samples
- `config/layer.csv`, `config/hazard.csv`: asset layers and hazards to analyse
- `config/asset-type-cost-curves.xlsx`: example damage configuration
  workbook, with the same structure as the project workbook. **Costs are
  illustrative and must not be used for analysis.** Damage curves are copied
  from the snail damage curve library. The wind damage curves used for power
  plants are for power towers, as a stand-in.
- `processed/asset/layer-road.gpq`: OpenStreetMap trunk, primary, secondary
  and tertiary roads, clipped to the area, with `asset_type` of `road_major`
  or `road_minor`
- `processed/asset/layer-power.gpq`: power plants from the WRI Global Power
  Plant Database, with `asset_type` `power_plant_thermal` and capacity in
  `capacity_mw`
- `processed/hazard/rp/peril-flood/subperil-coastal.zarr`: WRI Aqueduct
  coastal flood depth (m), with dimensions `rcp` × `epoch` × `return_period`
  × `y` × `x`. The `rcp4p5` and `rcp8p5` scenarios have no 2010 epoch.
- `processed/hazard/rp/peril-cyclone/subperil-wind.zarr`: STORM tropical
  cyclone maximum 10-minute sustained wind speed (m/s), with dimensions
  `gcm` × `return_period` × `y` × `x`. `constant` is present climate; the
  other models give 2015-2050 under SSP5-8.5.

The hazard grids are coarse (about 1 km for coastal flooding, 10 km for wind).
Hardly any roads cross the few coastal flood cells, so road damage from coastal
flooding is zero here. Roads are not counted as vulnerable to wind.

## Source and licences

Prepared by `prepare_example_data.py` from:

> Russell, T., Jaramillo, D., Nicholas, C., Thomas, F., Pant, R., & Hall, J.
> W. (2026). Infrastructure Climate Resilience Assessment Data Starter Kit for
> Mauritius (Version 0.2.1) [Data set]. Zenodo.
> https://doi.org/10.5281/zenodo.23271097

which redistributes:

- OpenStreetMap data © OpenStreetMap contributors, under the
  [Open Database License (ODbL-1.0)](https://opendatacommons.org/licenses/odbl/)
- WRI Global Power Plant Database, World Resources Institute, under
  [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
- WRI Aqueduct Floods, Ward, P.J. et al. (2020), under
  [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
- STORM tropical cyclone wind speed return periods, Bloemendaal, N. et al.,
  under [CC0](https://creativecommons.org/share-your-work/public-domain/cc0/)

Damage curves are from the physical vulnerability database of Nirandjan, S. et
al. (2024), under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/),
as packaged with [snail](https://github.com/nismod/snail).

To regenerate, download and unzip `MUS.zip` from Zenodo, then:

```shell
python example/prepare_example_data.py --source-dir path/to/data/MUS --output-dir example
```
