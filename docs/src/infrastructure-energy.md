# Energy

The energy model turns Mauritius's power-system data into network models you can
analyse. It produces three versions of the electricity network; a downstream
analysis picks which one to use.

**`base-mauritius`** is the real transmission network, built straight from the
data the national utility (CEB) provided: its routed lines, substations and
generation records. This is the reference network (method
`ceb-routed-topology-v3`).

The other two are *estimates* of where the grid reaches for the parts we don't
have surveyed. They use the same method (see below) and differ only in where
their power assets come from:

- **`inferred-osm-mauritius-rodrigues`** takes substations, plants and generators
  from OpenStreetMap (method `nightlight-roads-osm-power-v1`).
- **`inferred-provided-mauritius-rodrigues`** uses CEB's provided substations and
  generators instead, and keeps CEB's transmission backbone (method
  `nightlight-roads-provided-power-v1`).

One function builds all three, `energy.network_source.build_network`, chosen by a
`source` argument (`base`, `inferred-osm`, `inferred-provided`). Each product
records the data and method it came from in its metadata.

## How the inferred estimate works

We don't have a surveyed map of the whole distribution grid, so the inferred
products estimate where it plausibly reaches. The idea is straightforward:

1. **Find the lit-up places.** Satellites photograph the Earth at night, and
   places that are consistently bright are almost certainly electrified. The
   model reads a year of VIIRS night-lights imagery, keeps the pixels brighter
   than a threshold, and turns them into *target* points — places the grid has
   to reach. (The brightness filter is adapted from
   [GridFinder](https://github.com/carderne/gridfinder) by Chris Arderne, MIT
   licence.)
2. **Take the roads.** Distribution lines tend to follow roads, so the drivable
   road network from OpenStreetMap is the set of possible routes. Footpaths,
   tracks and trails are left out — a power line won't follow a hiking path (set
   `network_type: all` only to inspect every mapped way).
3. **Keep the roads that matter.** The model keeps the roads that run close to a
   target or a known power asset and drops the rest. What remains is its estimate
   of the grid's reach.

An earlier version routed a single least-cost tree over the roads (a Dijkstra /
minimum-spanning-tree search). This method instead *keeps* the connected road
network near the lit areas, so it preserves loops and dense local coverage rather
than collapsing everything to one sparse path. The target step
(`build_energy_nightlight_targets`) writes the target points, their raster mask
and provenance to `data/processed/energy/nightlight/<region>/`.

**What the inferred products are — and aren't.** They are a coverage estimate:
they show where the network plausibly runs, as connectivity. They are not a
survey of the real lines, and not an electrical model you can flow power through.
Their voltages and capacities are placeholders (11 kV, 5 MVA) written to
`model_v_nom_kv` / `model_s_nom_mva`; the measured-rating fields
`v_nom_kv` / `s_nom_mva` are left empty so the placeholders can't be mistaken for
real values.

## Running it

Snakemake drives everything (`workflow/0-preprocess/energy.smk`). In order it
cleans the provided CEB data (`prepare_energy_assets`), builds `base-mauritius`
from it (`build_base_energy_network`), extracts the night-light targets
(`build_energy_nightlight_targets`), then builds the two inferred products on top
of the targets and roads (`build_inferred_osm_energy_network` and
`build_inferred_provided_energy_network`).

```shell
# One product at a time
snakemake -c1 energy_base_network
snakemake -c1 energy_inferred_osm_network
snakemake -c1 energy_inferred_provided_network

# All three
snakemake -c1 build_energy_networks
```

Every build writes the network two ways: a PyPSA NetCDF file (the model itself)
and, next to it in a `geoparquet/` folder, EPSG:4326 GeoParquet node and edge
tables with matching checksums (the GIS / map view of the same topology). While
developing, the notebooks under `notebooks/energy/` load and plot these outputs;
they only read files and aren't part of the workflow.

## Inputs and configuration

The provided CEB data goes under `data/incoming/energy/provided/` as the
unchanged `power_demand`, `substation`, `power_transmission` and
`generation_source` folders. The night-light and inferred builds also read:

```text
data/incoming/energy/nightlights/viirs-2024-monthly/*.tif   # one image per month
data/incoming/energy/osm/mauritius-rodrigues/aoi.parquet    # area of interest
data/incoming/energy/osm/mauritius-rodrigues/roads.parquet  # OSM roads
data/incoming/energy/osm/mauritius-rodrigues/power.parquet  # OSM power assets
```

### Night-lights

VIIRS publishes one night-lights image per month. The pipeline combines a year
of them into a single image, taking the median brightness at each pixel so that
cloudy or noisy months fall away (`build_energy_nightlight_composite`, written to
`.../nightlight/<region>/viirs-composite.tif`). The monthly images are downloaded
once and then cached; a normal build never touches the network. If they're
missing the build stops and asks you to opt in: set
`energy.nightlight.source.allow_download: true`, then run
`snakemake -c1 fetch_energy_nightlights` (or start a normal build). After the one
fetch, runs stay offline. Where the images come from — the service, which months
to pull, the bounding box and the resolution — all lives in
`energy.nightlight.source`, so the same code works for any region or year; for
mu-star it is the 2024 VIIRS monthly series over Mauritius and Rodrigues from the
Earth Observation Group's image service. To skip the download and combine steps,
point `energy.nightlight.nightlights` at an image you already have.

### OpenStreetMap

Roads and power features are cached the same way: fetched once with
`energy.osm.fetch_osm_roads` / `fetch_osm_power_features` (`allow_download=True`,
e.g. from a Python shell), then read offline on every build. Mauritius and
Rodrigues are fetched separately and keep their island `region` labels. Each road
keeps its OSM `highway` class, and the build metadata lists which classes it kept
(`highway_classes`) against the full set (`road_envelope_highway_classes`), so the
footpath/track exclusion stays checkable. `network_type: drive` (the default)
keeps the drivable network; `network_type: all` caches every mapped way for
comparison.

The relevant settings (`config/config.yaml`) are:

```yaml
energy:
  base_network:
    route_gap_tolerance_m: 75
    default_voltage_kv: 66
    topology_capacity_mva: 10000
  inferred:
    region: mauritius-rodrigues
    network_type: drive
    allow_osm_download: false
    max_anchor_distance_m: 1000
    topology_voltage_kv: 11
    topology_capacity_mva: 5
    ceb_total_line_length_km: 10492.2
    line_length_tolerance_fraction: 0.10
    generation_capacity_tolerance_fraction: 0.10
  nightlight:
    region: mauritius-rodrigues
    network_type: drive
    allow_osm_download: false
    roads: null
    aoi: null
    # null builds the composite from the monthly tiles; set a path to supply one.
    nightlights: null
    source:
      service: https://di-lawimagery1.img.arcgis.com/arcgis/rest/services/NighttimeLightsMDNB/ImageServer
      rendering_rule: "Average Monthly Radiance (Raw Values)"
      object_ids: [120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131]
      bbox: [57, -21, 64, -19]
      pixel_size_degrees: 0.004166666666666667
      monthly_dir: incoming/energy/nightlights/viirs-2024-monthly
      allow_download: false
    nightlight_threshold: 0.1
    nightlight_support_distance_m: 1000
```

As a sanity check, the total length of the inferred roads-plus-backbone is
compared against CEB's reported 10,492.2 circuit-km. Road length and electrical
circuit-km aren't the same quantity, so this is only an advisory bound
(`line_length_tolerance_fraction`): the reviewed builds use `0.10`, while
`build_network`'s own default is a looser `0.35`. If a member island has no known
power asset, it gets a clearly labelled placeholder ("provisional") root so it
still forms its own connected piece of the network rather than being dropped.

## Outputs

Each product is packaged under `data/processed/energy/networks/<name>/`:

```text
data/processed/energy/networks/base-mauritius/base-mauritius.nc
data/processed/energy/networks/base-mauritius/base-mauritius_metadata.json
data/processed/energy/networks/base-mauritius/geoparquet/base-mauritius-nodes.geoparquet
data/processed/energy/networks/base-mauritius/geoparquet/base-mauritius-edges.geoparquet
data/processed/energy/networks/base-mauritius/geoparquet/base-mauritius-spatial-manifest.json
```

The inferred products add an `inferred_distribution/` directory (graph nodes,
edges, metadata and service weights). Human-readable review tables
(`generators.csv`, `lines.csv`, `validation.json`) are written per product under
`data/out/energy/<name>/`. Incomplete generator records remain in the review CSV
and produce warnings; they do not block the topology.

## Disruption interface (deferred)

Each system model's standard interface is *"given a list of disrupted assets,
output metrics of disruption to supply."* The interruption analysis that would
expose that interface for energy is **not ready and is intentionally out of
scope** for this migration: the former `runner`, `model`, `demand` and
energy-specific `damage` modules, their `2-simulate` rules, and the
`energy.operations` configuration have been removed. Consistent with the
README's note that "some rules are placeholders", the energy disruption
interface is deferred and will be reintroduced separately. The general
cross-system damage stage (`workflow/1-damage/`) is unaffected.
