# Energy

The energy analysis builds network models of Mauritius's electricity system.
Nobody has surveyed the distribution grid (the lines that carry power from
substations to streets and homes), so the analysis estimates where it runs. It
produces three network products:

- **base-mauritius**: the transmission network of the national utility, the
  Central Electricity Board (CEB), built directly from its substations, 66 kV
  routes and generation sites. This is the closest thing to the real grid.
- **inferred-osm-mauritius-rodrigues**: an estimate of the distribution grid
  that starts from the substations, plants and generators mapped in
  OpenStreetMap and joins them across the road network.
- **inferred-provided-mauritius-rodrigues**: the same estimate, starting from
  CEB's own substations and generators and keeping the CEB transmission
  backbone.

The two inferred products share the method below. They differ only in where
their power assets come from.

## How the inferred estimate works

1. **Find lit-up areas.** Somewhere consistently bright at night is almost
   certainly electrified. We take twelve monthly VIIRS night-light images
   (VIIRS is a satellite sensor) for 2024 and take the median of each pixel
   across the year, which smooths out cloudy months. A filter then picks out
   pixels brighter than their surroundings, and those above a brightness
   threshold become *targets*: places the grid has to reach. Only pixels inside
   the island outlines count. (The filter and threshold are adapted from
   [GridFinder](https://github.com/carderne/gridfinder) by Chris Arderne, MIT
   licence.)
2. **Take the roads.** Distribution lines tend to follow roads, so the drivable
   road network from OpenStreetMap gives the candidate routes. A two-way street
   is stored once.
3. **Keep the roads that matter.** We keep every road within 1 km of a target
   or a known power asset and drop the rest. When that strands a lit cluster,
   because the unlit road connecting it was dropped, we restore the shortest
   real-road path back to the main network.
4. **Connect the power assets.** Each substation, plant or generator is joined
   to the nearest point on the nearest kept road, and the road is split there,
   as long as that point is within 1 km. In the provided variant an asset also
   connects to the nearest CEB backbone line, and a junction that carries both
   voltage levels becomes a transformer.
5. **Keep parallel roads.** Two roads running between the same junctions stay
   as two separate lines.

Both 1 km distances are settings in `config/energy/energy.yaml`.

The result is a *coverage estimate*: where the network plausibly runs, not a
survey of the real lines. Its voltages (11 kV for distribution, 66 kV for the
CEB backbone) and line capacities are placeholders. They are published in the
`model_v_nom_kv` and `model_s_nom_mva` columns, while `v_nom_kv` and
`s_nom_mva` stay empty until real values exist.

## Inputs

- **Provided CEB data**: a power demand workbook and shapefiles of substations,
  transmission routes and generation sites, shared under licence. They sit in
  the `Substation`, `Power Transmission`, `Generation Source` and
  `Power Demand` folders of `<data>/incoming/Infrastructure/Energy/`, as on
  the project's shared drive. The generator list is the plant table of the CEB Annual Report 2023-24 (`src/energy/resources/ceb_plant_capacities_2023_24.csv`, installed and effective capacity per plant); each plant is placed by `src/energy/resources/ceb_plant_sites.csv`, using a provided site, a named OpenStreetMap plant or a geocoded village. Rooftop solar and plants with no location yet keep an empty `bus_id` and are spread by demand share.
- **OpenStreetMap**: the drivable road network, mapped power features and the
  outline of each island, downloaded into
  `<data>/incoming/Infrastructure/Energy/OpenStreetMap/<region>/`.
- **VIIRS night lights**: twelve monthly tiles for 2024, downloaded into
  `<data>/incoming/Infrastructure/Energy/Nighttime Lights/viirs-2024-monthly/`
  from an ArcGIS image
  service (NighttimeLightsMDNB ImageServer) that serves the Earth Observation
  Group's VIIRS DNB monthly cloud-free average-radiance composites. One caveat
  from the provider: a zero radiance can mean "no cloud-free observations that
  month" rather than darkness. The median over twelve months reduces this
  problem but does not remove it.
- **WorldPop population**: the 2020 "constrained" grid of people per 100 m
  cell, adjusted to UN totals, downloaded into
  `<data>/incoming/Infrastructure/Energy/Population/`. It covers Rodrigues as
  well.

`<data>` is the data root: `data/` in this repository unless `data_root` is set
in `config/config.yaml`. `<data>/incoming/Infrastructure/Energy/` mirrors the
folder of the same name on the project's shared drive, so one copy of that
folder sets it up. The public inputs are downloaded by the workflow when they
are missing, and a file that is already there is used as it is.

You do not need any of these inputs to use the networks. A ready-made pack of
the processed files (`yyyymmdd-model-data` under
`Processed Data/Infrastructure/Energy` on the shared drive) can be copied to
`<data>/processed/energy`; the README shows both routes.

## Where the demand is

The interruption analysis needs to know how much of the system demand sits
behind each substation and at each node of the road network. Nobody has
metered that for us, so it is estimated the way PyPSA-Earth does it:

1. Each substation serves the area closer to it than to any other substation,
   clipped to the island outline. Rodrigues, which has no CEB substation in the
   provided data, is one area around its stand-in root.
2. Each area is scored by the people who live in it (WorldPop) and by how
   brightly it is lit at night (the VIIRS composite, standing in for economic
   activity; PyPSA-Earth uses a GDP raster, which is far too coarse for a 50 km
   island). The score is 0.6 times the normalised radiance plus 0.4 times the
   normalised population, the weights PyPSA-Earth uses for GDP and population.
3. Inside each area the same score, computed per node from the raster cells
   nearest to it, splits the area's share between the nodes.

The system totals come from the CEB Annual Report 2023-24: a peak of 525.7 MW
(16 February 2024) and an average of 346.8 MW (units sent out over the year).
The shares and the service-area polygons are written under
`<data>/processed/energy/demand/<product>/` for review.

## Outputs

Each product is written under `<data>/processed/energy/networks/<product>/`:

- `<product>.nc`: the network as a [PyPSA](https://pypsa.org/) model (an
  open-source toolkit for power system modelling), with
  `<product>_metadata.json` recording the inputs, settings and checksums of the
  build.
- `geoparquet/`: the nodes and edges as GeoParquet layers (a GIS file format)
  for mapping, with a manifest tying them to the network.
- `inferred_distribution/` (inferred products only): the road graph as plain
  CSV node and edge tables.

Human-readable tables go to `<data>/out/energy/<product>/`: `generators.csv`,
`lines.csv` and `validation.json`. The validation report holds advisory checks:
total line length against CEB's published figures (479 km of 66 kV transmission
for the base network; 10,492 km of transmission plus distribution circuits for
the inferred networks), and modelled generation against CEB's reported
installed capacity. Every warning is also logged when the build runs, so a
problem is never silent.

Known limits:

- Rodrigues has no provided CEB data, so the provided variant gives it a
  stand-in root (a placeholder substation) on its road network.
- The distribution proxy follows roads, so lines that cross open country are
  not represented.
- Voltages and capacities are placeholders, and no power flow is calculated.

## Disruption analysis (deferred)

Each infrastructure model is meant to answer *"given a set of disrupted assets,
what is the loss of supply?"* The simulation that would answer this for energy is
not built yet.
