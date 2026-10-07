# Energy

The energy workflow models the electricity network of Mauritius and Rodrigues.
The Central Electricity Board (CEB) provided its 66 kV transmission network
but no data on its distribution network, so that is estimated from roads and
night-time lights.

## Data

A `*-star` repository can be run on public data alone, using the inferred
method. For working with official provided files, alone or combined with the
public data, those files are needed as well. All inputs sit in
`data/incoming/Infrastructure/Energy/`:

| Data | Source | Folder |
| --- | --- | --- |
| Roads, power features, island outlines | OpenStreetMap, downloaded | `OpenStreetMap/<region>/` |
| Night lights, 12 monthly images for 2024 | VIIRS (Earth Observation Group), downloaded | `Nighttime Lights/` |
| Population, 100 m grid for 2020 | WorldPop, downloaded | `Population/` |
| Substations and 66 kV lines (Mauritius) | CEB | `Substation/`, `Power Transmission/` |
| Generation sites | CEB | `Generation Source/` |
| Monthly peak demand, annual demand by sector | CEB | `Power Demand/` |
| Distribution network | pending | |
| Power plants and installed capacity | extracted from the CEB Annual Report 2023-24, pp. 50-51 and 97 | `CEB Annual Report/` |
| Plant locations | CEB sites, OpenStreetMap, geocoded villages | `CEB Annual Report/` |
| System peak and average demand | extracted from the annual report, pp. 45 and 51 | `CEB Annual Report/` |

The CEB files are shared under licence and are not public.

## Methods

The rules turn the inputs into processed files in
`data/processed/energy/<model_data>/`:

1. **CEB tables** (`provided/`): substations moved onto the nearest 66 kV
   route, and plants located and, on Mauritius, assigned the nearest
   substation.
2. **Lit areas** (`nightlight/`): the median of the monthly images, filtered
   as in [GridFinder](https://github.com/carderne/gridfinder) into *targets*,
   lit places the network must reach.
3. **Networks** (`networks/<network>/`). `base-mauritius` is the provided
   network alone, which covers Mauritius only. `inferred-osm-<region>` (public
   data) connects the OpenStreetMap power assets through the drivable roads
   within 1 km of a target or an asset: the estimated distribution network.
   `inferred-provided-<region>` (combination) adds that distribution network to
   the provided network. `<region>` is `region` in `config/energy/energy.yaml`,
   which also sets the placeholder voltages and capacities.
4. **Demand shares** (`demand/`), with PyPSA-Earth's method: each substation
   supplies the area closer to it than to any other, and an area's share of
   system demand is 0.6 times its share of night-light radiance plus 0.4 times
   its share of population (GDP grids are too coarse here). The same weights
   split it between the area's road nodes. System demand, from the annual
   report: 525.7 MW at peak and 346.8 MW on average.

### Interruption model (in development)

From the assets that fail in a hazard scenario, it will estimate how much
demand loses supply, at peak and average demand.

## Running the model

Set up the `mu-star` conda environment as in the README. The rules read
`data/incoming/Infrastructure/Energy/` and write
`data/processed/energy/<model_data>/` and `data/out/energy/<model_data>/`,
with `model_data` set in `config/energy/energy.yaml`. The workflow downloads
the public data. Project members link or copy the provided data from the
project OneDrive, which uses the same folder names. Instead of copying
individual files, they can use a `yyyymmdd-model-data` pack of processed
files, so nothing needs to run:

```shell
SHARED="<synced OneDrive project folder>"  # holds Incoming Data, Processed Data and Output Data
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
mkdir -p data/incoming/Infrastructure data/processed/energy data/out/energy
ln -s "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
ln -s "$SHARED/Processed Data/Infrastructure/Energy/$MODEL_DATA" data/processed/energy/  # the pack
ln -s "$SHARED/Output Data/Infrastructure/Energy/$MODEL_DATA" data/out/energy/  # its review tables
```

Snakemake writes through these links into the OneDrive. Before changing the
energy code or forcing a rerun (`-F` or `-R`), copy the pack instead with
`cp -Rp`, which keeps the file dates that Snakemake compares. Then run, from
the repository root:

```shell
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
# public data
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc"
# provided data
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/base-mauritius/base-mauritius.nc"
# combination, then its demand shares
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc"
snakemake -c1 "data/processed/energy/$MODEL_DATA/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv"
# all three networks
snakemake -c1 build_energy_networks
```

Add `-n` to list what would run. With a pack in place, the network commands
have nothing to do; the demand command still needs the provided data.

## Outputs

- `data/processed/energy/<model_data>/networks/<network>/`: the
  [PyPSA](https://pypsa.org/) network (`<network>.nc`), its metadata (inputs,
  settings, checksums), the nodes and edges for GIS (`geoparquet/`) and, for
  the inferred networks, the road network as CSV tables
  (`inferred_distribution/`).
- `data/processed/energy/<model_data>/demand/inferred-provided-<region>/`: the
  service areas, the shares by substation and by node, and the peak and
  average demand.
- `data/out/energy/<model_data>/<network>/`: `generators.csv`, `lines.csv` and
  `validation.json`, which compares the model with CEB's published line length
  (479 km of 66 kV lines; 10,492 km of all lines) and installed capacity.

The notebooks in `notebooks/energy/` show the inputs and the networks.

## Limitations

- The estimated distribution lines show where lines probably run, not where
  they are; lines that do not follow roads are missing.
- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Rooftop solar and plants with no location yet are left out.
- Voltages and capacities are placeholders, and no power flow is run.
