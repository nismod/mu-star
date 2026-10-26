# Energy

The energy workflow models the electricity network of Mauritius and Rodrigues.
The Central Electricity Board (CEB) provided its 66 kV transmission network
but no data on its distribution network, so the workflow estimates the
distribution network from roads and night-time lights. It then splits each
island's demand between its substations, and measures the unmet demand when
each substation, transmission line or power plant fails.

## Data

The workflow reads its inputs from `data/incoming/Infrastructure/Energy/` and
writes the processed files to `data/processed/energy/<model_data>/`, a folder
named by the `model_data` setting in `config/energy/energy.yaml`. Project
members can use a model-data pack instead of building the processed files: the
processed folder of one build, kept on the project OneDrive and named
`yyyymmdd-model-data` after the date of the build.

The public data in the table is enough to build a network inferred from
OpenStreetMap and night lights. The other networks, the demand shares and the
criticality analysis also need CEB's files, which are shared under licence and
are not public. The folders are in `data/incoming/Infrastructure/Energy/`:

| Data | Source | Folder |
| --- | --- | --- |
| Roads, power features, island outlines | OpenStreetMap, downloaded | `OpenStreetMap/<region>/` |
| Night lights, 12 monthly images for 2024 | VIIRS (Earth Observation Group), downloaded | `Nighttime Lights/` |
| Population, 100 m grid for 2020 | WorldPop, downloaded | `Population/` |
| Substations and 66 kV lines (Mauritius) | CEB | `Substation/`, `Power Transmission/` |
| Circuits of each line, the voltage it was built for, substation names | read from CEB's map of power stations and main transmission lines (2025) | `Power Transmission/ceb_line_circuits.csv`, `Substation/ceb_substation_names.csv` |
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

### Criticality

The criticality analysis measures how much each component of
`inferred-provided-<region>` matters for supply: the unmet demand when that
substation, CEB line or power plant fails on its own (an N-1 analysis). It
involves no hazard and runs once. Whether a hazard event damages a component
depends on the hazard intensity at the component and on the component's
vulnerability curve; this analysis gives the consequence for supply if it
does.

For each failure, a linear optimal power flow (PyPSA) on the CEB 66 kV network
supplies as much demand as the remaining network allows. The demand it cannot
supply is the unmet demand: demand minus supply, at each substation. Each
substation's demand sits on its 66 kV bus, and Rodrigues, which has no CEB
lines, is a single bus. A line's rating is its number of circuits, read from
CEB's map, times the rating of one circuit of a standard PyPSA line type at
66 kV: 53.7 MVA for lines built for 66 kV and 73.7 MVA for lines built for
132 kV. A CEB line made of several segments fails as a whole. A part of the
network cut off from the rest keeps supply only if it has a thermal or hydro
plant, because solar and wind farms disconnect when they lose the grid.

Each failure is solved at peak and at average demand, which gives two
quantities:

- `unmet_peak_mw`: the unmet demand in the worst hour (MW);
- `unmet_mwh_per_day`: the unmet energy per day of outage (MWh), which is the
  unmet demand at average demand over 24 hours. Multiplied by a restoration
  time in days, it gives the energy not supplied.

The per-day value is exact when whole areas lose supply. It is too low for
shortfalls that occur only near peak: on Rodrigues, losing a power station
leaves demand unmet at peak but not at average demand.

## Running the model

Run the commands below from the repository root, in the `mu-star` conda
environment (see the [Developer's README](./dev-introduction.md)).

### Setting up the data

The workflow downloads the public data itself, which is enough to build
`inferred-osm-<region>`. The other networks, the demand shares and the
criticality analysis also need the CEB files. Project members get these by
linking two folders of the project OneDrive into the repository:

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
# the criticality of the combined network, in a results folder with the pack's date
snakemake -c1 "data/results/energy/${MODEL_DATA%-model-data}-results/criticality/inferred-provided-mauritius-rodrigues/criticality.csv"
# all three networks
snakemake -c1 build_energy_networks
```

Add `-n` to a command to list the jobs it would run without running them.
With the pack in place, the network commands have nothing to do, while the
demand and criticality commands also need the incoming folder.

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
- `data/results/energy/<date>-results/criticality/inferred-provided-<region>/`,
  where `<date>` is the date of the pack:
  - `criticality.csv`: one row per substation, line and plant, with
    `unmet_mwh_per_day` and `unmet_peak_mw` when that component fails, and
    `line_limits_peak_mw`, the part of `unmet_peak_mw` due to line ratings;
  - `criticality_by_area.csv`: the same two quantities in each substation area
    for each failure, for mapping onto the distribution network through
    `service_weights_nodes.csv`.

The notebooks in `notebooks/energy/` show the inputs, the networks and the
criticality results.

## Limitations

- The estimated distribution lines show where lines probably run, not where
  they are; lines that do not follow roads are missing.
- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Rooftop solar and plants with no location yet are left out.
- The voltages and capacities in the networks are placeholders. The
  criticality analysis, the only part that runs a power flow, rates lines by
  standard line types, which are placeholders too until CEB provides ratings.
- The criticality analysis fails one component at a time. The unmet demand
  when a hazard damages several components at once is not the sum of the
  single-failure values: a substation fed by two lines keeps its supply when
  either line fails, but loses it all when both do.
- Every plant, solar and wind included, can produce up to its installed
  capacity, so the criticality analysis may underestimate shortages of
  generation.
- The CTSAV plant is connected to Combo, the nearest substation; CEB's map
  connects it to Union Vale.
