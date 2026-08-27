# Energy

The energy workflow builds three explicit, provenance-preserving network
products through a single dispatch, `energy.network_source.build_network`:

- **`base-mauritius`** (`source="base"`) derives the transmission topology from
  the provided CEB routes, substations and generation records. It is the
  canonical, reviewed network. Methodology: `ceb-routed-topology-v3`.
- **`inferred-osm-mauritius-rodrigues`** (`source="inferred-osm"`) uses OSM
  substations, plants and generators as known power terminals and retains the
  OSM road subnetwork supported by VIIRS nightlight targets. Methodology:
  `nightlight-roads-osm-power-v1`.
- **`inferred-data-mauritius-rodrigues`** (`source="inferred-data"`) applies the
  same nightlight road method but roots it on the reviewed input substations and
  generators and preserves the reviewed CEB backbone. Methodology:
  `nightlight-roads-reviewed-power-v1`.

The two inferred products share one routing method: VIIRS nightlights identify
likely electrified targets, which then retain the dense, cyclic drivable OSM
road subnetwork within the configured support distance of a target or a power
asset. The drivable network excludes footpaths, tracks and hiking trails that
the distribution-line proxy should not follow (set `network_type: all` only to
inspect every mapped way). They are topology-only geographic coverage proxies,
not observed distribution infrastructure or operational electrical models. Their
inferred electrical values (11 kV, 5 MVA) are explicit placeholders published as
`model_v_nom_kv` / `model_s_nom_mva`, while the public `v_nom_kv` / `s_nom_mva`
fields are null so they cannot be mistaken for observed ratings.

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
2. builds the canonical `base-mauritius` PyPSA network (`build_base_energy_network`);
3. extracts VIIRS nightlight targets inside the reviewed area of interest
   (`build_energy_nightlight_targets`);
4. builds `inferred-osm-<region>` from OSM power terminals and the
   nightlight-supported roads (`build_inferred_osm_energy_network`); and
5. builds `inferred-data-<region>` from the reviewed substations, generators and
   CEB backbone with the same nightlight road method
   (`build_inferred_data_energy_network`).

Each build also writes checksum-linked EPSG:4326 GeoParquet node and edge views
in a `geoparquet/` subdirectory: NetCDF remains the modelling artifact, while the
GeoParquet files are its GIS and visualisation view. For quick local inspection
while developing, the dev-only notebooks under `notebooks/energy/` load and plot
these outputs; they read the files and are not part of the workflow.

Convenience targets:

```shell
# Build the canonical base network
snakemake -c1 energy_base_network

# Build either inferred product
snakemake -c1 energy_inferred_osm_network
snakemake -c1 energy_inferred_data_network

# Build all three products
snakemake -c1 energy_network

# Publish GeoParquet sidecars and review tables for all three products
snakemake -c1 energy_exports
```

## Inputs

Place the unchanged provided source folders under
`data/incoming/energy/provided/`: `power_demand`, `substation`,
`power_transmission` and `generation_source`.

The nightlight and inferred builds also require a reviewed VIIRS composite, its
area of interest, and the cached OSM extracts:

```text
data/incoming/energy/nightlights/viirs-mauritius-rodrigues-2024.tif
data/incoming/energy/osm/mauritius-rodrigues/aoi.parquet
data/incoming/energy/osm/mauritius-rodrigues/roads.parquet
data/incoming/energy/osm/mauritius-rodrigues/power.parquet
```

The VIIRS file must be a georeferenced composite whose vintage, processing and
licence are suitable for the analysis. It is not downloaded implicitly because
those choices need to be reviewed and cited. The current input is the pixelwise
median of the twelve 2024 VIIRS monthly cloud-free average-radiance layers; its
source and checksums are recorded in the adjacent metadata JSON.

OSM acquisition is opt-in and offline-first. The cached `roads.parquet` (drive
network) and `power.parquet` are read directly by the build. To (re)populate the
cache, call `energy.osm.fetch_osm_roads` / `energy.osm.fetch_osm_power_features`
once with `allow_download=True`, then keep runs offline. The two islands are
fetched independently and retain their island-level `region` labels. Each cached
road keeps its OSM `highway` class, and the build metadata reports the retained
`highway_classes` (supported roads) and `road_envelope_highway_classes` (full
envelope) so footpath/track exclusion stays inspectable. Setting
`network_type: all` instead caches to `roads-all.parquet` and pulls in every
mapped way for a whole-network coverage comparison.

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
    nightlights: incoming/energy/nightlights/viirs-mauritius-rodrigues-2024.tif
    nightlight_threshold: 0.1
    nightlight_support_distance_m: 1000
```

The inferred road-plus-backbone length is checked against CEB's reported
10,492.2 circuit-km total. Geographic road length and electrical circuit-km are
different quantities, so `line_length_tolerance_fraction` is a deliberately
advisory bound: the reviewed inferred builds use `0.10`, while
`build_network`'s own parameter default is the looser `0.35`. A member island
without a known power terminal receives an explicitly labelled provisional root
so it remains a separate, connected component.

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
