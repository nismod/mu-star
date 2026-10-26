# Energy

The energy workflow models the electricity network of Mauritius and Rodrigues.
The Central Electricity Board (CEB) provided its 66 kV transmission network
but no data on its distribution network, so the workflow estimates the
distribution network from roads and night-time lights. It then splits each
island's demand between its substations.

## Data

The workflow reads its inputs from `data/incoming/Infrastructure/Energy/` and
writes the processed files to `data/processed/energy/<model_data>/`, a folder
named by the `model_data` setting in `config/energy/energy.yaml`. Project
members can use a model-data pack instead of building the processed files: the
processed folder of one build, kept on the project OneDrive and named
`yyyymmdd-model-data` after the date of the build.

The public data in the table is enough to build a network inferred from
OpenStreetMap and night lights. The other networks and the demand shares also
need CEB's files, which are shared under licence and are not public. The
folders are in `data/incoming/Infrastructure/Energy/`:

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
| Peak and average demand of each island | extracted from the annual report, pp. 45, 51, 96 and 98 | `CEB Annual Report/` |

## Methods

The preparation rules build the processed files in four steps, each writing
its own folder in `data/processed/energy/<model_data>/`:

1. **CEB tables** (`provided/`). The substations are moved onto the nearest
   66 kV line so that they connect to it, and the power plants are located
   and, on Mauritius, assigned to the nearest substation.
2. **Lit areas** (`nightlight/`). The median of the twelve monthly night-light
   images is filtered as in [GridFinder](https://github.com/carderne/gridfinder)
   into *targets*: lit places that the network must reach.
3. **Networks** (`networks/<network>/`). Three networks are built, where
   `<region>` is the `region` setting in `config/energy/energy.yaml`:
   - `base-mauritius`: the CEB network alone, which covers Mauritius only.
   - `inferred-osm-<region>`: public data only. The OpenStreetMap power assets
     are connected through the drivable roads within 1 km of a target or an
     asset, and these roads form the estimated distribution network.
   - `inferred-provided-<region>`: the CEB network with the estimated
     distribution network added.

   The same file sets the placeholder voltages and capacities of the networks.
4. **Demand shares** (`demand/`). Each substation supplies its service area,
   the part of the island closer to it than to any other substation; on
   Rodrigues, which has no substations, the power stations take their place.
   An area's share of its island's demand is 0.6 times its share of the
   island's night-light radiance plus 0.4 times its share of the population.
   This is PyPSA-Earth's method, with night lights in place of GDP, whose grids
   are too coarse for these islands. The same weights split each area's demand
   between the nodes of its distribution network. Each island's demand comes
   from the annual report: 525.7 MW at peak and 346.8 MW on average on
   Mauritius, and 8.52 MW and 5.75 MW on Rodrigues.

### Interruption model (in development)

From the assets that fail in a hazard scenario, it will estimate how much
demand loses supply, at peak and average demand.

## Running the model

Run the commands below from the repository root, in the `mu-star` conda
environment (see the [Developer's README](./dev-introduction.md)).

### Setting up the data

The workflow downloads the public data itself, which is enough to build
`inferred-osm-<region>`. The other networks and the demand shares also need
the CEB files. Project members get these by linking two folders of the
project OneDrive into the repository:

```shell
SHARED="<synced OneDrive project folder>"  # contains Incoming Data and Processed Data
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
mkdir -p data/incoming/Infrastructure data/processed/energy
ln -s "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
ln -s "$SHARED/Processed Data/Infrastructure/Energy/$MODEL_DATA" data/processed/energy/
```

The first link gives the inputs. The second gives the model-data pack named
in `config/energy/energy.yaml`, so the networks do not need to be built.

The links are enough to run the workflow as it is: with an up-to-date pack,
Snakemake has nothing to rebuild in the shared folders. It writes through the
links only when it rebuilds files: after you change the code of an energy
rule, or when you force a rerun with `-F` or `-R`. The rebuilt files then
replace the shared ones for everyone, and `-F` also reruns the downloads of
the public data. Before changing the energy code or forcing a rerun, replace
the links with local copies:

```shell
rm data/incoming/Infrastructure/Energy "data/processed/energy/$MODEL_DATA"  # removes the links, not the shared folders
cp -Rp "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
cp -Rp "$SHARED/Processed Data/Infrastructure/Energy/$MODEL_DATA" data/processed/energy/
```

The `-p` flag keeps each file's modification time. Snakemake compares these
times to decide what is out of date, so without `-p` it would rebuild every
file whose inputs were copied after it.

### Commands

Each command asks Snakemake for one file, or for a group of files, and runs
the rules needed to make it:

```shell
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
# the network from public data alone; downloads the public data it needs if missing
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc"
# the CEB network alone
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/base-mauritius/base-mauritius.nc"
# the combined network, then its demand shares
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc"
snakemake -c1 "data/processed/energy/$MODEL_DATA/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv"
# all three networks
snakemake -c1 build_energy_networks
```

Add `-n` to a command to list the jobs it would run without running them.
With the pack in place, the network commands have nothing to do, while the
demand command also needs the incoming folder.

## Outputs

- `data/processed/energy/<model_data>/networks/<network>/`, one folder per
  network:
  - `<network>.nc`: the [PyPSA](https://pypsa.org/) network, with its
    metadata (inputs, settings, checksums) in `<network>_metadata.json`;
  - `geoparquet/`: the nodes and edges for GIS;
  - `inferred_distribution/`: for the inferred networks, the road network as
    CSV tables;
  - `generators.csv`, `lines.csv` and `validation.json`, for review;
    `validation.json` compares the network with CEB's published line length
    (479 km of 66 kV lines; 10,492 km of all lines) and installed capacity.
- `data/processed/energy/<model_data>/demand/inferred-provided-<region>/`:
  - `service_areas.geoparquet`: the service area of each substation;
  - `service_weights_substations.csv` and `service_weights_nodes.csv`: each
    area's and each node's share of its island's demand;
  - `demand_levels.csv`: each island's peak and average demand, with its
    source.

The notebooks in `notebooks/energy/` show the inputs and the networks.

## Limitations

- The estimated distribution lines show where lines probably run, not where
  they are; lines that do not follow roads are missing.
- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Rooftop solar and plants with no location yet are left out.
- Voltages and capacities are placeholders, and no power flow is run.
