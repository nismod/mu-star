# Energy

The energy workflow builds three models of the electricity network of Mauritius
and Rodrigues. We have the 66 kV transmission network of the Central
Electricity Board (CEB) but no data on its distribution network, so that is
estimated from roads and night-time lights.

| Network | Contents |
| --- | --- |
| `base-mauritius` | CEB substations, 66 kV lines and power plants. Mauritius only. |
| `inferred-provided-mauritius-rodrigues` | CEB substations, 66 kV lines and power plants, plus the estimated distribution network. |
| `inferred-osm-mauritius-rodrigues` | Substations and power plants mapped in OpenStreetMap, plus the estimated distribution network. |

## Running the model

Run the commands below from the repository root, with the `mu-star`
environment active (see "Setup and installation" in the README). The rules
read and write `data/processed/energy/<model_data>/` and
`data/out/energy/<model_data>/`, where `<model_data>` is the name of a
model-data pack, set as `model_data` in `config/energy/energy.yaml`. It is now
`20261006-model-data`.

### With access to the project OneDrive

Link the energy folders of the synced OneDrive into `data/`. Each link keeps
the name of the folder it points to.

```shell
# The synced project folder, on a Mac for example
# $HOME/Library/CloudStorage/OneDrive-Nexus365/Shortcuts/Geography - Jim Halls group-Mauritius - Mauritius
SHARED="<synced project folder>"
mkdir -p data/incoming/Infrastructure data/processed/energy data/out/energy
ln -s "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
ln -s "$SHARED/Processed Data/Infrastructure/Energy/20261006-model-data" data/processed/energy/
ln -s "$SHARED/Output Data/Infrastructure/Energy/20261006-model-data" data/out/energy/
```

The first link is the source data, including the CEB data, which is not
public. The second is the model-data pack, so nothing needs building. The
third holds the pack's review tables (generators, lines and validation of
each network), which the notebooks show.

Snakemake writes rebuilt files through these links into the OneDrive. Nothing
is rebuilt while the pack matches the code. Before you change the energy code
or force a rerun (`-F` or `-R`), replace the pack link with a copy:

```shell
rm data/processed/energy/20261006-model-data
cp -Rp "$SHARED/Processed Data/Infrastructure/Energy/20261006-model-data" data/processed/energy/
```

`-p` keeps the file dates, which Snakemake uses to decide what to rebuild.

### Without OneDrive access

If you have a model-data pack, put it in `data/processed/energy/`, keeping its
folder name and file dates (`cp -Rp`). It holds the three networks and the
demand shares, so nothing needs building.

Otherwise, build `inferred-osm-mauritius-rodrigues` from public data. The
workflow downloads OpenStreetMap and the VIIRS night lights into
`data/incoming/Infrastructure/Energy/`. The other two networks and the demand
shares need the CEB data.

```shell
snakemake -c1 data/processed/energy/20261006-model-data/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc
```

### Commands

```shell
snakemake -n -c1 build_energy_networks  # dry run: what would run, and why
snakemake -c1 build_energy_networks     # the three networks
snakemake -c1 data/processed/energy/20261006-model-data/networks/base-mauritius/base-mauritius.nc  # one network
```

With a pack in place, `build_energy_networks` has nothing to do. The demand
shares need the source data, even with a pack:

```shell
snakemake -c1 data/processed/energy/20261006-model-data/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv
```

The outputs are described below. The notebooks in `notebooks/energy/` show
the inputs and the networks.

## Estimating the distribution network

1. **Lit areas.** Twelve monthly VIIRS satellite images of night-time light
   for 2024 are combined by taking the median of each pixel. A filter keeps
   pixels brighter than their surroundings, and those above a threshold
   become *targets*: places the network must reach. Pixels outside the island
   outlines are ignored. The filter and threshold come from
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

## Inputs

Inputs sit in `<data>/incoming/Infrastructure/Energy/`, where `<data>` is the
repository's `data/` folder. The layout is the same as on the project's shared
drive.

- **CEB data**, not public, shared under licence: shapefiles in `Substation`,
  `Power Transmission` and `Generation Source`, and a workbook of monthly peak
  demand and annual demand by sector in `Power Demand`.
- **CEB Annual Report 2023-24**, tables typed in from the report, in
  `CEB Annual Report`:
  - `ceb_plant_capacities_2023_24.csv`: installed and effective capacity of
    each plant in Mauritius (pp. 50-51) and Rodrigues (p. 97).
  - `ceb_plant_sites.csv`: the location of each plant, from a CEB generation
    site, a named OpenStreetMap plant, a geocoded village or unnamed
    OpenStreetMap features.
  - `ceb_demand_levels_2023_24.csv`: peak and average system demand.

  Each plant is assigned to the nearest CEB substation on its own island.
  Rooftop solar and plants with no location yet have an empty `bus_id` and
  are left out of the network for now.
- **OpenStreetMap**, downloaded: drivable roads, power features and island
  outlines, in `OpenStreetMap/<region>/`.
- **VIIRS night lights**, downloaded: twelve monthly images for 2024 in
  `Nighttime Lights/viirs-2024-monthly/`, from an ArcGIS image service
  (NighttimeLightsMDNB) that serves the Earth Observation Group's monthly
  cloud-free average radiance. A zero can mean the month had no cloud-free
  view rather than darkness; the median over twelve months reduces this but
  does not remove it.
- **WorldPop population**, downloaded: the 2020 "constrained" grid of people
  per 100 m cell, adjusted to UN totals, in `Population/`. It covers
  Rodrigues.

The workflow downloads a public input only when it is missing.

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
