# Energy

The energy workflow models the electricity network of Mauritius and Rodrigues.
The Central Electricity Board (CEB) provided its 66 kV transmission network
but no data on its distribution network, so the workflow estimates the
distribution network from roads and night-time lights. It then splits each
island's demand between its substations, and measures the unmet demand when
each substation, transmission line, power plant or distribution line fails.

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
     asset, and these roads form the estimated distribution network: one line
     along each road, standing for the medium- and low-voltage lines together.
   - `inferred-provided-<region>`: the CEB network with the estimated
     distribution network added.

   The same file sets the default voltages and capacities of the networks.
4. **Demand shares** (`demand/`). Each substation supplies its service area:
   the nodes of the distribution network nearer to it along the distribution
   lines than to any other substation, and the land nearer to those nodes than
   to any other node. On Rodrigues, which has no substations, the power
   stations take their place. Nodes that no distribution line joins to a
   substation, such as the bends of 66 kV lines, get no demand. An area's
   share of its island's demand is 0.6 times its share of the island's
   night-light radiance plus 0.4 times its share of the population, each grid
   cell counting towards the nearest node. This is PyPSA-Earth's method, with
   night lights in place of GDP, whose grids are too coarse for these islands.
   The same weights split each area's demand between the nodes of its
   distribution network. Each island's demand comes from the annual report:
   525.7 MW at peak and 346.8 MW on average on Mauritius, and 8.52 MW and
   5.75 MW on Rodrigues.

### Criticality

The criticality analysis measures how much each component of
`inferred-provided-<region>` matters for supply: the unmet demand when that
component fails on its own (an N-1 analysis). It involves no hazard and runs
once. Whether a hazard event damages a component depends on the hazard
intensity at the component and on the component's vulnerability curve; this
analysis gives the consequence for supply if it does.

One rule, `compute_energy_criticality`, computes it, with a method for each
part of the network:

- a linear power flow for each failure of a substation, transmission line or
  power plant, on the lines above `max_radial_line_voltage_kv`, so that line
  ratings count;
- the connectivity of the network for each failure of a distribution line, at
  or below that voltage.

A power flow would add nothing for the failure of a distribution line, without
switching: the line cuts off the nodes beyond it, and the rest of the network
only loses load. It would also take more than 100,000 core-hours (see the run
times below). The rule writes one table, with a row for each component and the
method that gave it.

Lines above `max_radial_line_voltage_kv` in `config/energy/energy.yaml` are
transmission lines, run meshed: in `inferred-provided`, the CEB 66 kV network.
Lines at or below it are distribution lines, run radially, each fed from one
substation. No single voltage separates transmission from distribution:
132 kV lines are transmission in Scotland but distribution in England and
Wales. The default of 35 kV follows IEC 60038, the international standard of
system voltages, which lists the systems up to 35 kV (such as 11, 22 and
33 kV) apart from those above it. CEB runs its transmission at 66 kV and its
distribution at 22 and 6.6 kV.

Keep the setting between the voltage of the distribution lines and that of
the transmission lines. Lowered below the distribution lines (11 kV in the
estimated network), it would move them into the power flow, which would run
them meshed, with every line in service instead of radially, and solve each
voltage as a separate network, since it leaves out the transformers between
voltages. The rule therefore stops with an error, before it builds the model,
when lines of the estimated distribution network or lines of more than one
voltage would enter the power flow. It warns when no line is above the
setting, and `metadata.json` keeps the warning. The demand step, which finds
the service areas along the lines at or below the setting, warns when they
join fewer than 90% of the nodes to a supply point.

#### Power flow

For each failure, a linear optimal power flow (PyPSA) on the transmission
lines supplies as much demand as the remaining network allows. The
distribution lines are left out: the demand of each substation's service area,
from the demand shares above, sits on the substation's 66 kV bus, as do the
plants assigned to the substation. Rodrigues, which has no CEB lines, is a
single bus with all its plants and demand. The demand the power flow cannot
supply is the unmet demand: demand minus supply, at each substation. On
Rodrigues, the unmet demand of the single bus is split between the power
stations' areas in proportion to their demand.

A failed substation takes its bus out of the network, with the lines and
plants connected to it, so the demand of its whole area is unmet. Lines and
plants carry no demand, so the unmet demand when one of them fails lies in the
areas of the substations that the remaining network cannot fully supply. A
transmission line made of several segments fails as a whole. A part of the
network cut off from the rest keeps supply only if it has a thermal or hydro
plant, because solar and wind farms disconnect when they lose the grid.

A line's rating is its number of circuits, read from CEB's map, times the
rating of one circuit of a standard PyPSA line type at the voltage the line
runs at in the network, 66 kV: 53.7 MVA for lines built for 66 kV and 73.7 MVA
for lines built for 132 kV.

The two tables read from CEB's map are optional, and are named under
`criticality` in `config/energy/energy.yaml`. Without the line circuits table,
each run of segments between two substations or junctions is one line, with
one circuit built for the voltage it runs at. Without the names table,
substations keep their ids.

#### Connectivity

The rule fails each distribution line with the distribution network run
radially, as distribution networks are in normal operation: each substation
feeds its service area along the shortest routes through the area's lines, so
that each node has one route to its substation, and the area's other lines and
the lines between areas are open. On Mauritius, 4,116 km of the estimated
network's 5,942 km are in service. A failed line cuts off the nodes beyond it
until it is repaired, and the demand the power flow supplies to them with
nothing failed is unmet, less what a thermal or hydro plant among them can
supply. Switching, by which an operator can restore some of those nodes
through another route before the repair, is left out. The connections that the
build adds between a substation or plant and the nearest road do not fail on
their own: they fail with the substation or plant. Nor do the nodes of the
distribution network: each node is fed by one line in service, and the node's
failure would cut off the same nodes as that line's failure.

#### Results

Each failure is solved at peak and at average demand, which gives two
quantities, each the rise in unmet demand over that with nothing failed:

- `unmet_peak_mw`: the unmet demand in the worst hour (MW);
- `unmet_mwh_per_day`: the unmet energy per day of outage (MWh), which is the
  unmet demand at average demand over 24 hours. Multiplied by a restoration
  time in days, it gives the energy not supplied.

The per-day value is exact when whole areas lose supply. It is too low for
shortfalls that occur only near peak: on Rodrigues, losing a power station
leaves demand unmet at peak but not at average demand.

The run times show why the power flow stops at the transmission network. On
one core of an Apple M1 Max laptop, with the networks of Mauritius and
Rodrigues, the rule takes about 70 s:

| Method | Components failed | Run time |
| --- | --- | --- |
| power flow | 85 substations, transmission lines and plants | about 62 s |
| connectivity | 50,358 distribution lines | under 1 s |

One power flow on a part of the Mauritius distribution network with 10,000
buses took 203 s, so a power flow for each failure of a distribution line, at
peak and at average demand, would take more than 100,000 core-hours. By
connectivity, all the radial failures of a road-like network of 1,000,000
buses take 1.4 s.

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

Snakemake runs the rules needed to make what a command asks for: a file, or a
target rule, which only lists files. A rule can be named in a command only if
its paths have no wildcards. The energy rules that do the work keep the data
folder in their paths as a wildcard, `{data}`, like the other rules in mu-star,
so the usual runs have target rules instead. These take the model-data pack
and the region from `config/energy/energy.yaml`, and the data folder from
`data_root` in `config/config.yaml` (`data` by default):

```shell
# the three networks
snakemake -c1 build_energy_networks
# the criticality of the combined network, in a results folder with the pack's date
snakemake -c1 build_energy_criticality
```

To make one file, ask for it by its path, as the [Developer's
README](./dev-introduction.md) describes:

```shell
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
# the network from public data alone; downloads the public data it needs if missing
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc"
# the CEB network alone
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/base-mauritius/base-mauritius.nc"
# the combined network, then its demand shares
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc"
snakemake -c1 "data/processed/energy/$MODEL_DATA/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv"
```

Add `-n` to a command to list the jobs it would run without running them.
With the pack in place, the network commands have nothing to do, while the
demand and criticality commands also need the incoming folder.

Snakemake does not notice a change to `src/energy/`. To rerun a rule after
one, add `-R` and the name of the rule that does the work after the target,
since `-R` takes every word after it as a rule name. The criticality rule
writes only to `data/results/`, so the links to the shared folders can stay:

```shell
snakemake -c1 build_energy_criticality -R compute_energy_criticality
```

### Criticality settings

The criticality rule reads its settings from `criticality` in
`config/energy/energy.yaml`:

| Setting | Default | Effect |
| --- | --- | --- |
| `max_radial_line_voltage_kv` | 35 | Lines above it are transmission lines, run meshed and solved by power flow; lines at or below it are distribution lines, run radially and failed by connectivity. The demand shares use it too. Keep it between the two voltages (see "Criticality" above). |
| `island_carriers` | thermal, hydro | Plants that keep supply going in a part of the network cut off from the rest. |
| `line_circuits` | `Power Transmission/ceb_line_circuits.csv` | Each CEB line's route, design voltage and circuits, for the power flow. Optional. |
| `substation_names` | `Substation/ceb_substation_names.csv` | The substations' names. Optional. |
| `line_types` | 149-AL1/24-ST1A for lines built for 66 kV, 243-AL1/39-ST1A for 132 kV | The PyPSA line type of one circuit, which sets a line's rating in the power flow. |
| `s_max_pu` | 1.0 | The share of a line's rating that the power flow can use after a failure. |

`criticality.csv` has a row for every component, distribution lines
included, so it is the table to map. `line_limits_peak_mw` gives the unmet
demand that line ratings add to a failure of the power flow; it is zero for
every failure with the current ratings.

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
  - `criticality.csv`: one row per substation, transmission line, plant and
    distribution line, with `unmet_mwh_per_day` and `unmet_peak_mw` when that
    component fails; `line_limits_peak_mw`, the part of `unmet_peak_mw` due to
    line ratings, empty for a distribution line; `in_service`, false for a
    distribution line that radial operation leaves open; and `method`,
    `power_flow` or `connectivity`. A distribution line's `asset_id` is its
    `line_id` in the network's edges layer;
  - `criticality_by_area.csv`: one row for each failure and each service
    area left with unmet demand, with the same two quantities; a distribution
    line has one row, for its own area. `area_bus_id` names the area by the
    bus of its substation (or power station, on
    Rodrigues), which `service_weights_nodes.csv` links to the nodes of the
    distribution network. When demand is unmet with nothing failed, a failure
    can move that shortfall from one area to another, so a failure's areas
    can add up to more than its value in `criticality.csv`;
  - `line_ratings.csv`: the rating of each transmission line segment, with
    its line (`asset_id`), the voltage it was built for, its circuits and the
    voltage it runs at;
  - `metadata.json`: the inputs and settings, the unmet demand with nothing
    failed, counts of components, and any warning about
    `max_radial_line_voltage_kv`.

The notebooks in `notebooks/energy/` show the inputs, the networks and the
criticality results.

## Limitations

- The estimated distribution lines show where lines probably run, not where
  they are. On Mauritius they total 5,942 km, 4,618 km (44%) less than the
  10,560 km of lines at 22 kV and below in the annual report (p. 60): 4,100 km
  at 22 and 6.6 kV and 6,460 km at 400 and 230 V. CEB's total counts a road
  that carries both voltages twice, where the estimate has one line, and lines
  that do not follow roads are missing from the estimate.
- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Rooftop solar and plants with no location yet are left out.
- The voltages and capacities in the networks are defaults, which data from
  CEB would replace. The power flow, the only part that rates lines, rates
  them by standard line types until CEB provides its ratings.
- The rule does not model switching: a failed substation cuts off its whole
  service area, and a failed distribution line the nodes beyond it, until the
  repair, even where an operator could restore supply sooner through another
  route. In 2024, an interruption to CEB's customers lasted 1.8 to 3.2 hours
  on average, by region (annual report, p. 66).
- The service areas and the radial routes follow the roads: each node is
  supplied from the substation nearest along them. Where roads are sparse, in
  less populated places, that substation is less likely to be the one that
  supplies the node; the rule is the same everywhere, so the error is of the
  same kind across the islands.
- The build joins each substation to the roads at one point (Champagne at
  two, one through the power station next to it), so the routes from a
  substation start on one or two road segments, as if it had one or two
  feeders. At each of the 18 substations on Mauritius, the distribution line
  whose failure leaves the most demand unmet is one of these first segments,
  with 55–100% of the area's peak demand; a real substation divides its area
  between several feeders.
- The criticality analysis fails one component at a time. The unmet demand
  when a hazard damages several components at once is not the sum of the
  single-failure values: a substation fed by two lines keeps its supply when
  either line fails, but loses it all when both do.
- Every plant, solar and wind included, can produce up to its installed
  capacity, so the criticality analysis may underestimate shortages of
  generation.
- The CTSAV plant is connected to Combo, the nearest substation; CEB's map
  connects it to Union Vale.
