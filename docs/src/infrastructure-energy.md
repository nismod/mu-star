# Energy

The energy workflow builds three network products, each from a different data
source, using one function: `energy.network_source.build_network`. Each product
records the source data and method it was built from:

- **`base-mauritius`** (`source="base"`) derives the transmission topology from
  the provided CEB routes, substations and generation records. It is the network
  built directly from the provided CEB data. Methodology: `ceb-routed-topology-v3`.
- **`inferred-osm-mauritius-rodrigues`** (`source="inferred-osm"`) uses OSM
  substations, plants and generators as known power terminals and retains the
  OSM road subnetwork supported by VIIRS nightlight targets. Methodology:
  `nightlight-roads-osm-power-v1`.
- **`inferred-provided-mauritius-rodrigues`** (`source="inferred-provided"`) applies the
  same nightlight road method but roots it on the provided input substations and
  generators and preserves the provided CEB backbone. Methodology:
  `nightlight-roads-provided-power-v1`.

The two inferred products share one routing method: VIIRS nightlights identify
likely electrified targets, which then retain the dense, cyclic drivable OSM
road subnetwork within the configured support distance of a target or a power
asset. The drivable network excludes footpaths, tracks and hiking trails that
the distribution-line proxy should not follow (set `network_type: all` only to
inspect every mapped way). They map plausible network coverage — connectivity
only — not the real distribution lines and not a working electrical model. Their
inferred electrical values (11 kV, 5 MVA) are placeholders, written to
`model_v_nom_kv` / `model_s_nom_mva`; the public `v_nom_kv` / `s_nom_mva`
fields are left null so they cannot be mistaken for observed ratings.

## Nightlight targets

The nightlight step replaces the previous GridFinder least-cost (Dijkstra / MST)
raster search. The high-pass filter and threshold are adapted from
[GridFinder](https://github.com/carderne/gridfinder) by Chris Arderne (MIT
licence); only the nightlight **target** step is kept. The inferred builds no
longer route a least-cost tree over roads. Instead the VIIRS targets are used
downstream to retain the OSM road subnetwork they support, preserving road
cycles rather than collapsing to a sparse connector tree.

`energy.nightlight_targets.build_nightlight_targets` writes only:

```text
data/processed/energy/nightlight/<region>/targets.tif
data/processed/energy/nightlight/<region>/targets.geoparquet
data/processed/energy/nightlight/<region>/metadata.json
```

The former `costs.tif`, `distances.tif`, `grid.tif` and
`connector-lines.geoparquet` raster/vector products are removed.

## Pipeline

The Snakemake workflow (`workflow/0-preprocess/energy.smk`):

1. cleans the provided demand, substation, transmission and generation data
   (`prepare_energy_assets`);
2. builds the `base-mauritius` PyPSA network from the provided data (`build_base_energy_network`);
3. extracts VIIRS nightlight targets inside the reviewed area of interest
   (`build_energy_nightlight_targets`);
4. builds `inferred-osm-<region>` from OSM power terminals and the
   nightlight-supported roads (`build_inferred_osm_energy_network`); and
5. builds `inferred-provided-<region>` from the provided substations, generators and
   CEB backbone with the same nightlight road method
   (`build_inferred_provided_energy_network`).

Each build also writes checksum-linked EPSG:4326 GeoParquet node and edge views
in a `geoparquet/` subdirectory: NetCDF remains the modelling artifact, while the
GeoParquet files are its GIS and visualisation view. For quick local inspection
while developing, the dev-only notebooks under `notebooks/energy/` load and plot
these outputs; they read the files and are not part of the workflow.

Convenience targets:

```shell
# Build the base network (from the provided CEB data)
snakemake -c1 energy_base_network

# Build either inferred product
snakemake -c1 energy_inferred_osm_network
snakemake -c1 energy_inferred_provided_network

# Build all three products (also writes the GeoParquet views and review tables)
snakemake -c1 build_energy_networks
```

## Inputs

Place the unchanged provided source folders under
`data/incoming/energy/provided/`: `power_demand`, `substation`,
`power_transmission` and `generation_source`.

The nightlight and inferred builds also read cached monthly VIIRS tiles, an area
of interest, and the cached OSM extracts:

```text
data/incoming/energy/nightlights/viirs-2024-monthly/*.tif
data/incoming/energy/osm/mauritius-rodrigues/aoi.parquet
data/incoming/energy/osm/mauritius-rodrigues/roads.parquet
data/incoming/energy/osm/mauritius-rodrigues/power.parquet
```

**The radiance composite is built for you.** `build_energy_nightlight_composite`
reduces the cached monthly tiles to a single composite — their pixelwise median —
at `data/processed/energy/nightlight/<region>/viirs-composite.tif`, which the
target step reads. You never hand-make or hand-place it. (To use a pre-made
composite instead, point `energy.nightlight.nightlights` at it and this step is
skipped.)

The monthly tiles are cached, opt-in downloads — the same offline-first pattern
as OSM. A build never contacts the image service on its own; if the tiles are
missing it stops until you set `energy.nightlight.source.allow_download: true`
and run `snakemake -c1 fetch_energy_nightlights` (or simply build), after which
runs stay offline. Everything project-specific — the image service, which
monthly rasters to pull, the bounding box and the resolution — lives in
`energy.nightlight.source`, so the same code reproduces a composite for any
region or year.

For mu-star, these are the 2024 VIIRS monthly cloud-free radiance layers over
Mauritius and Rodrigues, from the Earth Observation Group ArcGIS service.

**OSM data is cached, not fetched during a build either.** A build never
contacts OpenStreetMap; it reads the `roads.parquet` and `power.parquet` files
above. You fill that cache once, explicitly, by calling
`energy.osm.fetch_osm_roads` and `energy.osm.fetch_osm_power_features` with
`allow_download=True` (for example from a Python shell or a notebook). After
that, every build runs offline from the cache. Mauritius and Rodrigues are
fetched separately and keep their island-level `region` labels. The
`allow_osm_download` keys in the config only *record* how the cache was made —
the Snakemake rules themselves never download.

Each cached road keeps its OSM `highway` class, and the build reports two
breakdowns in its metadata — `highway_classes` for the roads it kept and
`road_envelope_highway_classes` for the full envelope — so you can see which road
types were included or dropped (footpaths, tracks and the like). The default
`network_type: drive` keeps the drivable network; `network_type: all` instead
caches `roads-all.parquet` with every mapped way, for a whole-network coverage
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

The inferred road-plus-backbone length is checked against CEB's reported
10,492.2 circuit-km total. Geographic road length and electrical circuit-km are
different quantities, so `line_length_tolerance_fraction` is a deliberately
advisory bound: the reviewed inferred builds use `0.10`, while
`build_network`'s own parameter default is the looser `0.35`. If a member island
has no known power asset, the build gives it a clearly labelled placeholder
("provisional") root, so that island still forms its own connected part of the
network instead of being dropped.

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
