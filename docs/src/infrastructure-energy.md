# Energy

The energy analysis builds network models of Mauritius's electricity system. It
produces three versions, differing only in the data they are built from, so a
study can pick the one that suits it:

- A network built directly from the national utility's (CEB) transmission
  routes, substations and generation records — the closest thing to the real
  grid.
- Two *inferred* networks that estimate where the grid reaches in the areas we
  have no survey for. They share the method below and differ only in where their
  power assets come from: one uses OpenStreetMap, the other CEB's own assets on
  top of CEB's transmission backbone.

## How the inferred estimate works

Without a survey of the full distribution grid, the inferred networks estimate
its likely reach:

1. **Find the lit-up places.** Somewhere consistently bright at night is almost
   certainly electrified. We read a year of VIIRS night-time-lights imagery and
   keep the bright pixels as *targets* — places the grid has to reach. (The
   brightness filter is adapted from
   [GridFinder](https://github.com/carderne/gridfinder) by Chris Arderne, MIT
   licence.)
2. **Take the roads.** Distribution lines tend to follow roads, so the drivable
   road network from OpenStreetMap gives the candidate routes.
3. **Keep the roads that matter.** We keep the roads running close to a target or
   a known power asset and drop the rest. What is left is the estimated reach of
   the grid.

The result is a *coverage estimate* — where the network plausibly runs — not a
survey of the real lines, and not an electrical model you can flow power through:
its voltages and capacities are placeholders, kept apart from the observed-value
fields so they cannot be mistaken for measurements.

## Inputs

- **Provided CEB data** — power demand, substations, transmission routes and
  generation records.
- **VIIRS night-time lights** — monthly cloud-free radiance imagery (for
  mu-star, the 2024 series over Mauritius and Rodrigues, from the Earth
  Observation Group).
- **OpenStreetMap** — the drivable road network and mapped power features.

## Outputs

Each of the three products is a PyPSA network model, exported with GIS layers for
mapping. Human-readable tables list its generators and lines, and a validation
report records sanity checks — for example, comparing an inferred network's total
line length against CEB's reported circuit-kilometres (advisory only, since road
length and electrical circuit length are different quantities).

## Disruption analysis (deferred)

Each infrastructure model is meant to answer *"given a set of disrupted assets,
what is the loss of supply?"* The simulation that would answer this for energy is
not built yet — intentionally out of scope for now, and to be added separately.
