# Transport

## Land transport: journey-to-work criticality (draft)

> **Status: draft.** The rules, file locations, wildcards and schemas below
> are the agreed starting point. The scripts are written but have not been
> run on real data. The [open questions](#open-questions) list the decisions
> still to make.

The analysis asks: **if one piece of the land transport network fails, how much
longer, further and costlier do people's journeys to work become, and how many
can no longer make the trip at all?**

It has four stages:

1. **Demand.** Estimate a journey-to-work origin–destination (OD) matrix
   between H3 cells. Employed residents come from the census, distributed over
   the island using WorldPop. Jobs come from mu-star's H3 economic activity
   layer. A radiation model links the two.
2. **Network.** Build one directed multimodal network of roads, bus routes and
   the metro line, with zone connectors. Each link carries a generalised cost
   in MUR per trip.
3. **Baseline.** Assign peak-period demand with
   [transport_flow_model](https://github.com/nismod/transport_flow_model)
   (tfm). The assignment is sequential and capacity constrained: bus and metro
   capacity binds, and roads are unconstrained by default.
4. **Criticality.** Fail each road edge and metro segment on its own and
   reroute the affected trips. Report the increases in journey time, distance
   and cost, and the trips that are isolated.

```text
census table + boundaries ─┬─> census zones ─┬─> WorldPop scaled to census ─> residents per H3 cell ─┐
WorldPop ──────────────────┘                 │                                                      │
H3 economic activity (GVA by sector) ─────────┴────────────────────────────> jobs per H3 cell ──────┤
                                                                                                    ├─> zones
roads ─> road network ─┬─> bus segments ─┐                                                          │
bus stops/routes ──────┘                  ├─> multimodal network + failure units <──────────────────┘
metro stations/line ─> metro segments ────┘          │
                                                     ├─> road skim ─> radiation OD ─> demand by mode
                                                     │                                    │
                                                     └──────────> baseline assignment <───┘
                                                                        │
                                                       failure sets ─> criticality batches ─> asset criticality
```

### Running

The rules are in `workflow/0-preprocess/transport_land.smk` (demand and
networks) and `workflow/2-losses/transport_land.smk` (assignment and
criticality). Parameters are in `config/transport/transport.yaml`, and the
scripts sit next to the rule files. Library code that can be tested on its own
is in `src/transport/`: `radiation.py`, `costs.py` and `land.py`.

```bash
# everything, default zoning
snakemake -c8 transport_land_all
# or a single target
snakemake -c8 data/out/transport/land/h3-r8/criticality/asset-criticality.geoparquet
```

The model needs `nismod-tfm` (transport_flow_model) and `h3`. `h3` is in
`environment.yaml`, but `nismod-tfm` is not yet: see the open questions.

### Wildcards

| Wildcard   | Values                     | Meaning                                                                      |
| ---------- | -------------------------- | ---------------------------------------------------------------------------- |
| `{data}`   | `data`                     | data root                                                                    |
| `{zoning}` | `h3-r8` (`h3-r\d+`)        | zone system for OD, connectors and results. H3 resolution 8 is about 0.7 km² |
| `{batch}`  | `batch-0000` … (`batch-\d{4}`) | one criticality job. The count is `criticality.n_batches` (default 64)   |

The road, bus and metro networks do not depend on `{zoning}`. Everything that
involves zones does.

### File layout

```text
{data}/incoming/                                  provided (schemas below)
  Socio-economic/Census/census-residential-zones.xlsx, .gpkg
  Population/WorldPop/mus_ppp_2020_constrained.tif
  Infrastructure/Transport/road/roads.gpkg
  Infrastructure/Transport/bus/bus_stops.gpkg, bus_routes.gpkg, bus_route_stops.csv
  Infrastructure/Transport/metro/metro_stations.gpkg, metro_line.gpkg, metro_line_stations.csv
{data}/processed/economy/h3-economic-activity.parquet   produced by the mu-star economics workflow (path TBC)

{data}/processed/transport/
  population/census-zones.geoparquet
  population/worldpop-scaled.tif
  population/worldpop-census-check.csv
  zones/{zoning}/residents.parquet
  zones/{zoning}/jobs.parquet
  zones/{zoning}/zones.geoparquet
  networks/road/edges.geoparquet, nodes.geoparquet
  networks/bus/stops.geoparquet, segments.geoparquet
  networks/metro/stations.geoparquet, segments.geoparquet
  networks/multimodal/{zoning}/links.parquet, nodes.geoparquet, failure-units.parquet
  demand/{zoning}/road-skim.parquet
  demand/{zoning}/commute-od.parquet, commute-od-summary.json
  demand/{zoning}/demand-by-mode.parquet, demand-by-mode-summary.json

{data}/out/transport/land/{zoning}/
  baseline/link-flows.parquet, paths.parquet, od-journeys.parquet, unassigned.parquet, summary.json
  criticality/failure-sets.parquet
  criticality/batches/{batch}/scenario-impacts.parquet, od-impacts.parquet
  criticality/asset-criticality.csv, asset-criticality.geoparquet
```

### Data inputs

All input paths are set in `transport_land.inputs` in the config. Where we
provide the data ourselves (roads, bus, metro), the schema below is what we
have chosen. Any CRS is accepted, because everything is reprojected to
`crs_metric` (EPSG:32740).

**Census residential zones** (spreadsheet plus boundaries). These are the
official totals.

| Column (mapped in config) | Type | Notes                               |
| ------------------------- | ---- | ----------------------------------- |
| `zone_id`                 | str  | join key to the boundary file       |
| `population`              | num  | usually resident population         |
| `employed_residents`      | num  | employed persons by place of residence |

The boundaries are polygons with the same zone id, given by
`census_boundaries_zone_id`.

**WorldPop** is a gridded population raster (about 100 m) in persons per
pixel. We use it only for the spatial pattern within each census zone, after
scaling it to the census totals.

**H3 economic activity** is produced by mu-star. It is a long table of
`cell` (H3 index, at resolution 8 or finer), `sector` (ISIC section) and
`value` (GVA), with the column names set in config. An optional
`gva-per-worker-by-sector.csv` (`sector`, `gva_per_worker`) turns GVA into
jobs.

**Roads**, `roads.gpkg`, layer `edges`, LineStrings noded at junctions:

| Column                   | Type | Required | Notes                                                               |
| ------------------------ | ---- | -------- | ------------------------------------------------------------------- |
| `edge_id`                | str  | yes      | unique and stable; criticality results are reported against it      |
| `from_node`, `to_node`   | str  | yes      | node ids at the start and end of the geometry                       |
| `road_class`             | str  | yes      | motorway, trunk, primary, secondary, tertiary, unclassified, residential or service |
| `oneway`                 | bool | yes      | true: traffic only runs from `from_node` to `to_node`               |
| `speed_kph`              | num  | no       | free-flow speed. Defaults by class come from config                 |
| `capacity_vph`           | num  | no       | vehicles per hour per direction. Defaults by class                  |
| `name`, `lanes`, `bridge` |     | no       | carried through                                                     |

**Bus.** This is a cut-down GTFS. If a GTFS feed exists, we would convert it
to these three files.

- `bus_stops.gpkg`: `stop_id`, `name`, Point geometry
- `bus_routes.gpkg`: `route_id`, `name`, `operator`, `headway_min` (peak),
  `vehicle_capacity` (passengers). Geometry is optional and used for map
  matching later.
- `bus_route_stops.csv`: `route_id`, `direction` (0/1), `stop_sequence`,
  `stop_id`

**Metro** (one line):

- `metro_stations.gpkg`: `station_id`, `name`, Point geometry
- `metro_line.gpkg`: the line's LineString
- `metro_line_stations.csv`: `station_id`, `seq`, and optionally
  `run_time_min_to_next` and `headway_min`

### Parameters

All parameters live in `config/transport/transport.yaml`. The main ones:

| Parameter                         | Default           | Meaning                                                                     |
| --------------------------------- | ----------------- | --------------------------------------------------------------------------- |
| `cost.per_minute`                 | 3.0 MUR           | value of travel time per person-minute (perceived)                          |
| `cost.per_km.{car,bus,metro,walk}` | 8 / 0 / 0 / 0 MUR | distance cost per person-km                                                |
| `cost.fare.{bus,metro}`           | 0                 | fare on each boarding                                                       |
| `cost.weight.{wait,walk}`         | 1.5               | multipliers on waiting and walking minutes                                  |
| `cost.weight.transfer_penalty_min` | 5                | penalty minutes on each boarding                                            |
| `cost.isolated_trip_cost`         | null              | MUR per trip that cannot be made. With null, only counts are reported       |
| `radiation.impedance`             | `time_min`        | rank destinations by free-flow road time (or `length_km`)                   |
| `radiation.max_impedance`         | 120               | no commuting beyond this                                                    |
| `radiation.origin_mass`           | `employed_residents` | see the radiation model below                                            |
| `radiation.normalisation`         | `row`             | each employed resident makes exactly one trip                               |
| `mode_split.bands`                | placeholder       | walk / car / pt shares by road-distance band                                |
| `mode_split.pt_overflow`          | `unserved`        | PT demand over capacity is reported as unserved, or moved to car            |
| `period.peak_period_share`, `peak_period_hours` | 0.6, 2 h | the share of daily commuting in the modelled period, and its length  |
| `period.car_occupancy`            | 1.3               | persons per car, used only if roads are capacity constrained                |
| `road.defaults`                   | by class          | default free-flow speed and capacity                                        |
| `road.capacity_constrained`       | false             | bus and metro are always constrained                                        |
| `bus.*`, `metro.*`                |                   | default headway and vehicle capacity, bus speed factor and dwell time       |
| `criticality.fail`                | `[road, metro]`   | the asset types to fail                                                     |
| `criticality.road_classes`        | null (all)        | limits which road edges are failed                                          |
| `criticality.n_batches`           | 64                | the number of criticality jobs                                              |

### Method

**Population.** For each census zone, WorldPop is scaled by
`census_population / worldpop_sum`, so that zone totals match the official
figures exactly. `worldpop-census-check.csv` lists the ratio for each zone
and flags zones outside `check_ratio_tolerance`. Pixels are then aggregated to
H3 cells by their pixel centre. Each pixel also gets employed residents,
using the employed/population ratio of its census zone. Every cell keeps a
population-weighted centroid, which is where its connectors attach.

**Jobs.** We turn GVA into jobs with sector GVA per worker. Without that
table, GVA is used directly as relative attractiveness. Either way, jobs are
then scaled so that total jobs equal total employed residents (the island is
treated as closed). Finer H3 cells are rolled up to the zoning resolution.

**OD: the radiation model.** `src/transport/radiation.py` implements:

$$ T_{ij} = T_i \cdot \frac{m_i\, n_j}{(m_i + s_{ij})(m_i + n_j + s_{ij})} $$

- $T_i$ is the employed residents at $i$ (trip production).
- $n_j$ is the jobs at $j$.
- $s_{ij}$ is the jobs in zones closer to $i$ than $j$ is, by free-flow road
  time, excluding $i$ and $j$.
- $m_i$ is the origin mass.

In Simini et al. (2012), $m$ and $s$ are both "opportunities". If we set
$m_i$ to the jobs at $i$, cells with no jobs (purely residential cells, which
are common at H3 resolution 8) send all their trips to the nearest cell with
any jobs. So the default is $m_i$ = employed residents, with jobs scaled to
the same total. Rows are then renormalised, so that every employed resident
makes one trip within `max_impedance`. OD pairs below `min_trips` are dropped,
and their trips are spread back over the same origin's other pairs.

We do not use tfm's `RadiationModel`, for two reasons. It takes one relevance
column for both ends. And its $s_{ij}$ counts all zones within the fixed
distance threshold, rather than the zones closer than $j$. See the notes for
transport_flow_model below.

**Mode split.** There is no mode-choice data, so we apply fixed walk / car /
PT shares by road-distance band, for example from a national travel survey
(TBC). Walk trips are dropped. Car trips run between `zone/<id>/car`
centroids, and PT trips between `zone/<id>/pt` centroids. The car and PT
layers only meet at the zones, so a single assignment keeps the modes
separate. Within PT, the least-cost path decides between bus and metro (or
both), subject to capacity.

**Multimodal network.** Node and link naming is documented in
`src/transport/land.py`.

- *Road*: one directed link per direction of travel.
- *Car connectors*: each zone's car centroid is linked to its `car_k`
  nearest road nodes, in both directions.
- *Bus*: one link per route, direction and consecutive stop pair, at bus
  speed (car free-flow × `speed_factor`) plus dwell time. Each bus link stores
  the `road_edge_ids` it runs over (draft: the fastest road path between the
  snapped stops). Boarding links carry the weighted half-headway wait, the
  transfer penalty and the fare.
- *Metro*: station-to-station links in each direction, with boarding and
  alighting links as for bus.
- *Walking*: walk links join each zone's PT centroid to stops and stations
  within `walk_max_km`. Transfer walk links join bus stops to metro stations.

Each link's cost is `per_minute × time_min + per_km[mode] × length_km + fare`.
Capacity is in persons per modelled period. For bus and metro it is
`(60 / headway) × hours × vehicle capacity`. For roads it is infinite unless
`road.capacity_constrained` is set.

**Failure units.** Each physical asset is one failure unit, listing every link
it removes:

- a road edge removes both of its directions, plus every bus link whose
  `road_edge_ids` include it;
- a metro segment removes both of its directions.

**Baseline.** We run `tfm.assign(..., "sequential", capacity_constrained=True,
directed=True, include_paths=True)`. Demand that cannot be routed, whether PT
over capacity or disconnected, is recorded in `unassigned.parquet`. With
`pt_overflow: car`, that PT demand goes to the car centroids in a second pass.
For each OD pair, `od-journeys.parquet` holds the trips and the mean time,
distance and cost per trip, recomputed from the edges along each path.

**Criticality.** Only failure units that carry at least `min_baseline_flow`
are evaluated. They are dealt round-robin into batches in descending order of
flow. In each batch, `tfm.disrupt(network, scenarios, base=baseline,
capacity_constrained=True)` reroutes only the baseline paths that use a
removed link, keeping all other flows in place. tfm reports cost only, so for
each affected OD pair we compare the time, distance and cost of the rerouted
paths with those of the same flow on its baseline paths
(`transport.costs.disruption_deltas`).

The outputs for each asset are:

- `affected_flow`, `rerouted_flow` and `isolated_flow` (trips in the period);
- `delta_time_min_total` (extra person-minutes);
- `delta_length_km_total` (extra person-km);
- `delta_cost_total` (MUR);
- `isolated_cost`, if a cost per isolated trip is set;
- the same quantities split by mode (`_car`, `_pt`);
- ranks, and shares of the baseline totals.

`asset-criticality.geoparquet` joins all of this to the road edge or metro
segment geometry.

### Scale and performance

Mauritius main island is about 1,860 km², or about 2,500 H3 resolution-8
cells, of which perhaps 1,500–2,000 are populated.

- *Road skim*: this is Dijkstra from each zone over the road graph, run in
  chunks of sources to limit memory. It should take minutes.
- *Radiation*: one sort per origin over a dense Z×Z matrix (about 2,500² ≈
  6 M cells), which takes seconds. tfm's pure-Python `RadiationModel` would
  be O(Z³) here.
- *OD pairs*: there could be a few million before pruning. `min_trips` and
  `max_impedance` keep this down, and the path table (`edge_path` lists) is
  the main memory cost.
- *Criticality*: there are tens of thousands of road edges with flow. tfm
  only reroutes affected paths, and the batches run in parallel. If it is
  still too slow, restrict `criticality.road_classes` (for example to
  tertiary and above), or simplify the road graph by merging degree-2 nodes.

### Status of each rule

| Rule                            | Script                              | Status                                                                 |
| ------------------------------- | ----------------------------------- | ---------------------------------------------------------------------- |
| `transport_census_zones`        | `transport_census_zones.py`         | written; needs the real column names and handling of total rows        |
| `transport_scale_worldpop`      | `transport_scale_worldpop.py`       | written                                                                |
| `transport_residents_h3`        | `transport_residents_h3.py`         | written                                                                |
| `transport_jobs_h3`             | `transport_jobs_h3.py`              | written; the activity layer schema needs confirming                    |
| `transport_zones`               | `transport_zones.py`                | written; study-area clip TODO                                          |
| `transport_road_network`        | `transport_road_network.py`         | written; connected-component cleaning TODO                             |
| `transport_bus_network`         | `transport_bus_network.py`          | draft; uses the fastest road path between stops, not map matching      |
| `transport_metro_network`       | `transport_metro_network.py`        | draft                                                                  |
| `transport_multimodal_network`  | `transport_multimodal_network.py`   | draft                                                                  |
| `transport_road_skim`           | `transport_road_skim.py`            | written; distance is the shortest distance, not the length of the fastest path |
| `transport_radiation_od`        | `transport_radiation_od.py`         | written (library function unit tested)                                 |
| `transport_mode_split`          | `transport_mode_split.py`           | written; shares are placeholders                                       |
| `transport_baseline_assignment` | `transport_baseline_assignment.py`  | written; tfm calls smoke-tested on a toy network                       |
| `transport_failure_sets`        | `transport_failure_sets.py`         | written                                                                |
| `transport_criticality_batch`   | `transport_criticality_batch.py`    | written; tfm calls smoke-tested on a toy network                       |
| `transport_criticality_summary` | `transport_criticality_summary.py`  | written                                                                |

The DAG has been checked with `snakemake -n` against placeholder inputs. No
rule has been run on real data.

### Open questions

1. **Mode shares.** Is there any national source (a household survey,
   transport ministry figures or the census) for walk / car / PT shares, by
   distance or region? Should car availability (household car ownership by
   census zone) limit car demand?
2. **Census details.** Which census year is it, and what are the zone codes
   and column names? Does employment cover informal work? Are there
   total/subtotal rows to drop?
3. **Economic layer.** What is the output path and H3 resolution? Is it long
   or wide? Which GVA year and currency? Is there a source for GVA per worker
   by sector (national accounts plus the labour force survey)? Should any
   sectors be left out as destinations (e.g. agriculture, or home-based
   work)?
4. **Intra-zonal trips.** At resolution 8 these are short walks. Is
   `intrazonal_share = 0` acceptable, or should it depend on each cell's
   jobs-to-workers ratio?
5. **Bus data.** Is there a GTFS feed or route list with frequencies and
   vehicle sizes? Should we map-match route shapes to roads rather than use
   the fastest path between stops?
6. **Metro data.** We need Metro Express headways, train capacity and run
   times.
7. **Costs.** We need agreed values of time and vehicle operating costs, and
   bus fares (bus travel is free for many users).
8. **Roads under disruption.** Roads are uncapacitated in the baseline.
   Should rerouted car traffic be capacity constrained under disruption?
   That would need lane capacities and a capacity-constrained baseline.
9. **What fails.** Should we include metro station closures and road nodes
   (junctions or bridges as nodes)? Should failures be limited to road
   classes, or to bridges and culverts linked to the hazard layers?
10. **Isolated trips.** Should a lost trip have a cost (e.g. a day's wages),
    or be reported as a count only?
11. **Rodrigues.** Should it be excluded, or run as a separate study area?
12. **Environment.** `nismod-tfm` still needs adding to `environment.yaml`.
    The published `nismod-tfm` 0.2.0rc1 wheel appears to contain only
    `_version.py`, so we may need to pin a git install or a fixed release.

### Notes for transport_flow_model

These are possible upstream changes found while drafting.

- `RadiationModel` takes one `relevance` column for $m_i$, $m_j$ and
  $s_{ij}$. Commuting needs a separate origin mass and destination mass.
- `RadiationModel` counts $s_{ij}$ as all zones within `distance_threshold`
  (excluding $i$ and $j$), not the zones with $d_{ik} < d_{ij}$. That is not
  the Simini et al. definition. It is also O(Z³) in Python.
- `disrupt` and `assign` report path cost only. We recompute time and
  distance from `edge_path`. Support for several path attributes (for
  example `sum_columns=["time_min", "length_km"]`) would avoid that.
- Capacity has to be finite (we use 1e15 for unconstrained links).
