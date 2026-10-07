# Energy

The energy workflow builds models of the electricity network of Mauritius and
Rodrigues. The Central Electricity Board (CEB) provided its 66 kV transmission
network but no data on its distribution network, so that is estimated from
roads and night-time lights.

## Data and running the model

A `*-star` repository can be run on public data alone, using the inferred
method. For working with official files provided by the utility, or with a
combination of the two, those files are needed as well:

| Data | Network | Contents |
| --- | --- | --- |
| Public | `inferred-osm-mauritius-rodrigues` | Substations and power plants mapped in OpenStreetMap, plus the estimated distribution network. |
| Provided | `base-mauritius` | CEB substations, 66 kV lines and power plants. Mauritius only. |
| Combination | `inferred-provided-mauritius-rodrigues` | CEB substations, 66 kV lines and power plants, plus the estimated distribution network. |

The workflow downloads the public data when it is missing:

- OpenStreetMap: drivable roads, power features and island outlines. The
  power features also place some of the CEB plants.
- VIIRS night lights: twelve monthly images for 2024, from an ArcGIS image
  service (NighttimeLightsMDNB) that serves the Earth Observation Group's
  monthly cloud-free average radiance.
- WorldPop: the 2020 "constrained" grid of people per 100 m cell, adjusted to
  UN totals, used for the demand shares.

For Mauritius, the provided data are:

| Data | Status | Folder or file |
| --- | --- | --- |
| Substations (Mauritius) | provided by CEB | `Substation/` |
| 66 kV transmission lines (Mauritius) | provided by CEB | `Power Transmission/` |
| Distribution network | pending | |
| Generation sites | provided by CEB | `Generation Source/` |
| Monthly peak demand, annual demand by sector | provided by CEB | `Power Demand/` |
| Power plants and installed capacity | extracted from the CEB Annual Report 2023-24, pp. 50-51 and 97 | `CEB Annual Report/ceb_plant_capacities_2023_24.csv` |
| Plant locations | from CEB generation sites, named OpenStreetMap plants, geocoded villages or unnamed OpenStreetMap features | `CEB Annual Report/ceb_plant_sites.csv` |
| System peak and average demand | extracted from the annual report, pp. 45 and 51 | `CEB Annual Report/ceb_demand_levels_2023_24.csv` |

The CEB files are shared under licence and are not public. Each plant is
assigned to the nearest CEB substation on its own island; rooftop solar and
plants with no location yet are left out of the network for now.

All data goes in `data/incoming/Infrastructure/Energy/`, laid out as
`Incoming Data/Infrastructure/Energy` on the project OneDrive. The workflow
downloads the public data there. Project members copy or link the provided
data from the OneDrive; a link keeps the name of the folder it points to:

```shell
SHARED="<synced OneDrive project folder>"  # holds Incoming Data, Processed Data and Output Data
mkdir -p data/incoming/Infrastructure
ln -s "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
```

Instead of building, project members can use a model-data pack: the processed
files of one build, from `Processed Data/Infrastructure/Energy` on the
OneDrive, with its review tables in `Output Data/Infrastructure/Energy`. The
rules read and write the pack named by `model_data` in
`config/energy/energy.yaml`:

```shell
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
mkdir -p data/processed/energy data/out/energy
ln -s "$SHARED/Processed Data/Infrastructure/Energy/$MODEL_DATA" data/processed/energy/
ln -s "$SHARED/Output Data/Infrastructure/Energy/$MODEL_DATA" data/out/energy/
```

Through these links, Snakemake writes rebuilt files into the OneDrive. Before
you change the energy code or force a rerun (`-F` or `-R`), copy the pack
instead with `cp -Rp`, which keeps the file dates that Snakemake uses to
decide what to rebuild.

Having set up the `mu-star` conda environment as in the README ("Setup and
installation"), and the data for the chosen mode, run the model from the
repository root:

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

Add `-n` to list what would run without running it. With a pack in place, the
network commands have nothing to do; the demand command still needs the
provided data. The outputs are described below, and the notebooks in
`notebooks/energy/` show the inputs and the networks.

## Estimating the distribution network

1. **Lit areas.** Twelve monthly VIIRS satellite images of night-time light for
   2024 are combined by taking the median of each pixel. A zero can mean the
   month had no cloud-free view rather than darkness; the median reduces this
   but does not remove it. A filter keeps pixels brighter than their
   surroundings, and those above a threshold become *targets*: places the
   network must reach. Pixels outside the island outlines are ignored. The
   filter and threshold come from
   [GridFinder](https://github.com/carderne/gridfinder) (Chris Arderne, MIT
   licence).
2. **Roads.** Distribution lines mostly follow roads, so the OpenStreetMap
   network of drivable roads gives the possible routes. A two-way street is
   one line.
3. **Roads kept.** Roads within 1 km of a target or a power asset are kept. If
   dropping a road cuts a lit area off, the shortest road path back to the
   rest of the network is put back.
4. **Connections.** Each substation and power plant connects to the nearest
   point on a kept road, if that point is within 1 km; the road is split
   there. In `inferred-provided`, each asset also connects to the nearest CEB
   66 kV line, and a point where 66 kV and 11 kV lines meet becomes a
   transformer.
5. **Parallel roads.** Two roads between the same junctions stay two lines.

The two 1 km distances are `nightlight_support_distance_m` and
`max_anchor_distance_m` in `config/energy/energy.yaml`.

The result shows where distribution lines probably run, not where they are.
Voltages and capacities are placeholders, set in `energy.yaml`:

- road segments: 11 kV, 5 MVA (a typical 11 kV feeder);
- CEB 66 kV lines: 50 MVA (a typical single 66 kV circuit);
- the connection from each substation or plant to the road: 137 MVA (2,595
  MVA of transformers over 19 major substations, annual report p. 71);
- every line of `base-mauritius`: 10,000 MVA, so that no line limits flow.

These values are in the `model_v_nom_kv` and `model_s_nom_mva` columns;
`v_nom_kv` and `s_nom_mva` stay empty until CEB values are available.

## Demand

The interruption analysis needs to know how much of the system demand is
behind each substation and at each network node. There is no metered data for
this, so it is estimated with PyPSA-Earth's method:

1. Each substation supplies the area closer to it than to any other
   substation, cut to the island outline. Rodrigues has no CEB substation in
   the data, so its area is split between its power plants.
2. Each area gets a score: 0.6 times its share of the total night-light
   radiance plus 0.4 times its share of the population. PyPSA-Earth uses the
   same weights with GDP in place of night lights; GDP grids are too coarse
   for an island 50 km across.
3. Within an area, the same score for each road node, from the raster cells
   nearest to it, splits the area's demand between its nodes.

Shares are rescaled to add to one. System demand, from the annual report: a
peak of 525.7 MW (16 February 2024, p. 45) and an average of 346.8 MW (units
sent out in 2023-24 divided by 8,784 hours, p. 51). The shares, areas and
demand levels are written to
`<data>/processed/energy/<model_data>/demand/<network>/`.

## Outputs

For each network, in `<data>/processed/energy/<model_data>/networks/<network>/`:

- `<network>.nc`: the [PyPSA](https://pypsa.org/) network.
- `<network>_metadata.json`: inputs, settings and file checksums of the build.
- `geoparquet/`: nodes and edges for GIS, with a manifest that links them to
  the `.nc` file.
- `inferred_distribution/` (inferred networks only): the road network as CSV
  tables of nodes and edges.

In `<data>/out/energy/<model_data>/<network>/`: `generators.csv`, `lines.csv`
and `validation.json`. The validation file compares the model with CEB's
published totals: line length (479 km of 66 kV lines for `base-mauritius`;
10,492 km of all lines for the inferred networks) and installed generation
capacity. A difference beyond the tolerance is a warning, which is also logged.

## Limitations

- Rodrigues has no CEB network data. Each of its power plants supplies the
  island from the road point it connects to; there is no substation or 66 kV
  line between them. An island with no power asset at all gets a placeholder
  substation on its roads.
- Distribution lines that do not follow roads are missing.
- Voltages and capacities are placeholders, and no power flow is run.

## Disruption analysis (not built yet)

Each infrastructure model should answer: if these assets fail, how much supply
is lost? The energy version of this is not built yet.
