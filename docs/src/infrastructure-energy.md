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

1. **CEB tables** (`provided/`). Substations are moved onto the nearest 66 kV
   route. Each plant in the annual report is located from the sites table and,
   on Mauritius, assigned the nearest CEB substation. Rooftop solar and plants
   with no location yet are left out for now.
2. **Lit areas** (`nightlight/`). The median of the twelve monthly images (a
   zero can mean no cloud-free view). A filter from
   [GridFinder](https://github.com/carderne/gridfinder) (Chris Arderne, MIT
   licence) keeps pixels brighter than their surroundings; those above a
   threshold are *targets* the network must reach.
3. **Networks** (`networks/<network>/`), named after how they are made and the
   area they cover. `base-mauritius` uses the provided data alone, which covers
   Mauritius only: the substations joined along the CEB routes, with gaps under
   75 m closed, and the plants attached. `inferred-osm-<region>` (public data)
   and `inferred-provided-<region>` (combination) take their power assets from
   OpenStreetMap or from the provided data, for `region` in
   `config/energy/energy.yaml` (here `mauritius-rodrigues`). They keep the
   drivable roads within 1 km of a target or a power asset, plus the shortest
   road path back to any lit area this cuts off; a two-way street is one line,
   and parallel roads stay separate. Each asset connects to the nearest kept
   road within 1 km and, in `inferred-provided`, also to the nearest 66 kV
   line, through a transformer.
4. **Demand shares** (`demand/`), with PyPSA-Earth's method. Each substation
   supplies the area closer to it than to any other; on Rodrigues the plants
   share the island. An area's share of system demand is 0.6 times its share
   of night-light radiance plus 0.4 times its share of population, and the
   same weights split it between the area's road nodes. PyPSA-Earth uses GDP
   instead of night lights, but GDP grids are too coarse here. System demand,
   from the annual report: 525.7 MW at peak (16 February 2024, p. 45) and
   346.8 MW on average (p. 51).

The estimated lines show where distribution lines probably run, not where they
are. Voltages and capacities are placeholders, in `model_v_nom_kv` and
`model_s_nom_mva` (`v_nom_kv` and `s_nom_mva` stay empty until CEB values
exist): roads 11 kV and 5 MVA, 66 kV lines 50 MVA, connections 137 MVA (2,595
MVA of transformers over 19 substations, report p. 71), and 10,000 MVA on
every line of `base-mauritius`, so that no line limits flow. They and the
distances are set in `config/energy/energy.yaml`.

### Interruption model (in development)

Given the assets that fail in a hazard scenario, it will estimate how much
demand loses supply, at peak and average demand, from the networks and demand
shares above.

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

- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Distribution lines that do not follow roads are missing.
- Voltages and capacities are placeholders, and no power flow is run.
