# Transport

## Land transport: journey-to-work criticality (draft)

> **Status: draft.** The rules, file locations, wildcards and schemas below
> are the agreed starting point. The scripts are written and run end to end
> on a small synthetic network, but have not been run on real data. The
> [open questions](#open-questions) list the decisions still to make.

The analysis asks: **if one piece of the land transport network fails, how much
longer, further and costlier do people's journeys to work become, and how many
can no longer make the trip at all?**

It has four stages:

1. **Demand.** Estimate a journey-to-work origin–destination (OD) matrix
   between H3 cells. Employed residents come from the census, distributed over
   the island using WorldPop. Jobs come from mu-star's H3 economic activity
   layer, converted with sectoral productivity. A radiation model from
   [transport_flow_model](https://github.com/nismod/transport_flow_model)
   (tfm) links the two.
2. **Network.** Build one directed multimodal network of roads, bus routes and
   the metro line, with zone connectors. Each link carries a generalised cost
   in MUR per trip.
3. **Baseline.** Split trips into walk, car, bus and metro with single
   national shares, then assign peak-period demand with tfm. The assignment is sequential and
   capacity constrained: bus and metro capacity binds, and roads are
   unconstrained.
4. **Criticality.** Fail each road edge, metro segment and metro station on
   its own and reroute the affected trips. Metro trips with no metro path left
   fall back to bus, then car. Report the increases in journey
   time, distance and cost, and count the trips that are isolated.

```text
census table + boundaries ─┬─> census zones ─┬─> WorldPop scaled to census ─> residents per H3 cell ─┐
WorldPop ──────────────────┘                 │                                                      │
economic_activity_h3 (output by sector) ──────┴──────(output per worker)────> jobs per H3 cell ─────┤
                                                                                                    ├─> zones
roads ─> road network ─┬─> bus segments ─┐                                                          │
bus stops/routes/times ┘                  ├─> multimodal network + failure units <──────────────────┘
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
criticality). The jobs layer comes from the rule `economic_activity_h3` in
`workflow/0-preprocess/economy.smk`, which is currently a stub. Parameters are
in `config/transport/transport.yaml`, and the scripts sit next to the rule
files. Helpers are in `src/transport/` (`costs.py`, `fallback.py`, `land.py`).

```bash
# everything, default zoning
snakemake -c8 transport_land_all
# or a single target
snakemake -c8 data/out/transport/land/h3-r8/criticality/asset-criticality.geoparquet
```

The analysis needs `nismod-tfm` and `h3`, both in `environment.yaml`. tfm is
installed from a pinned git commit, which builds its Rust extension, so `rust`
is in the environment too.

### Wildcards

| Wildcard   | Values                         | Meaning                                                                      |
| ---------- | ------------------------------ | ---------------------------------------------------------------------------- |
| `{data}`   | `data`                         | data root                                                                    |
| `{zoning}` | `h3-r8` (`h3-r\d+`)            | zone system for the economic layer, OD, connectors and results. H3 resolution 8 is about 0.7 km² |
| `{batch}`  | `batch-0000` … (`batch-\d{4}`) | one criticality job. The count is `criticality.n_batches` (default 64)       |

The road, bus and metro networks do not depend on `{zoning}`. Everything that
involves zones does.

### File layout

```text
{data}/incoming/                                  provided (schemas below)
  Socio-economic/Census/census-residential-zones.xlsx, .gpkg
  Socio-economic/GDP/output-per-worker-by-sector.csv
  Population/WorldPop/mus_ppp_2020_constrained.tif
  Infrastructure/Transport/road/roads.gpkg
  Infrastructure/Transport/bus/bus_stops.gpkg, bus_routes.gpkg, bus_route_stops.csv
  Infrastructure/Transport/metro/metro_stations.gpkg, metro_line.gpkg, metro_line_stations.csv

{data}/processed/economy/{zoning}/economic-activity.parquet   rule economic_activity_h3 (stub)

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

All provided-input paths are set in `transport_land.inputs` in the config.
Where we provide the data ourselves (roads, bus, metro), the schema below is
what we have chosen. Any CRS is accepted, because everything is reprojected to
`crs_metric` (EPSG:32740).

**Census residential zones** (spreadsheet plus boundaries). These are the
official totals.

| Column (mapped in config) | Type | Notes                                  |
| ------------------------- | ---- | -------------------------------------- |
| `zone_id`                 | str  | join key to the boundary file          |
| `population`              | num  | resident population                    |
| `employed_residents`      | num  | employed persons by place of residence |

The boundaries are polygons with the same zone id, given by
`census_boundaries_zone_id`.

**WorldPop** is a gridded population raster (about 100 m) in persons per
pixel. We use it only for the spatial pattern within each census zone, after
scaling it to the census totals.

**H3 economic activity** is the output of the rule `economic_activity_h3`. It
is wide: one row per H3 cell (`h3_index`, at the `{zoning}` resolution or
finer), and one output column per sector named `gva_<sector>` (the prefix is
set in config).

**Sectoral productivity**, `output-per-worker-by-sector.csv`, has the columns
`sector` and `output_per_worker`, in the same currency and year as the
activity layer. To be built from Statistics Mauritius national accounts and
employment by industry.

**Roads**, `roads.gpkg`, layer `edges`, LineStrings noded at junctions:

| Column                    | Type | Required | Notes                                                              |
| ------------------------- | ---- | -------- | ------------------------------------------------------------------ |
| `edge_id`                 | str  | yes      | unique and stable; criticality results are reported against it     |
| `from_node`, `to_node`    | str  | yes      | node ids at the start and end of the geometry                      |
| `road_class`              | str  | yes      | motorway, trunk, primary, secondary, tertiary, unclassified, residential or service |
| `oneway`                  | bool | yes      | true: traffic only runs from `from_node` to `to_node`              |
| `speed_kph`               | num  | no       | free-flow speed. Defaults by class come from config                |
| `capacity_vph`            | num  | no       | vehicles per hour per direction. Defaults by class                 |
| `name`, `lanes`, `bridge` |      | no       | carried through                                                    |

**Bus**, with frequencies and between-stop timings:

- `bus_stops.gpkg`: `stop_id`, `name`, Point geometry
- `bus_routes.gpkg`: `route_id`, `name`, `operator`, and the peak service
  level as `frequency_per_hour` (or `headway_min`). Geometry is optional and
  used later for map matching.
- `bus_route_stops.csv`: `route_id`, `direction` (0/1), `stop_sequence`,
  `stop_id`, and `time_to_next_min` (scheduled minutes to the next stop,
  including dwell, blank on the last stop)

Every route uses the standard vehicle size `bus.vehicle_capacity`.

**Metro** (one line):

- `metro_stations.gpkg`: `station_id`, `name`, Point geometry
- `metro_line.gpkg`: the line's LineString
- `metro_line_stations.csv`: `station_id`, `seq`, and optionally
  `run_time_min_to_next` and `headway_min`

### Parameters

All parameters live in `config/transport/transport.yaml`. The main ones:

| Parameter                          | Default           | Meaning                                                                    |
| ---------------------------------- | ----------------- | -------------------------------------------------------------------------- |
| `cost.per_minute`                  | 3.0 MUR           | value of travel time per person-minute (perceived)                         |
| `cost.per_km.{car,bus,metro,walk}` | 8 / 0 / 0 / 0 MUR | distance cost per person-km                                                |
| `cost.fare.{bus,metro}`            | 0 / 0 (TBC)       | fare on each boarding                                                      |
| `cost.weight.{wait,walk}`          | 1.5               | multipliers on waiting and walking minutes                                 |
| `cost.weight.transfer_penalty_min` | 5                 | penalty minutes on each boarding                                           |
| `radiation.impedance`              | `time_min`        | rank destinations by free-flow road time (or `length_km`)                  |
| `radiation.max_impedance`          | 120               | no commuting beyond this                                                   |
| `radiation.origin_mass`            | `employed_residents` | see the radiation model below                                           |
| `radiation.normalisation`          | `row`             | each employed resident makes exactly one trip                              |
| `mode_split.shares`                | placeholder       | single national walk / car / bus / metro shares                            |
| `mode_split.metro_fallback`        | `bus`             | where the metro share goes when an end has no station within walking distance |
| `mode_split.pt_overflow`           | `unserved`        | bus or metro demand over capacity is reported, or moved to car             |
| `period.peak_period_share`, `peak_period_hours` | 0.6, 2 h | the share of daily commuting in the modelled period, and its length |
| `road.defaults`                    | by class          | default free-flow speed and capacity                                       |
| `road.capacity_constrained`        | false             | roads are unconstrained. Bus and metro are always constrained              |
| `connectors.walk_max_km` / `metro_walk_max_km` | 1 / 2 km | walking catchment of bus stops and metro stations                    |
| `bus.vehicle_capacity`             | 70                | standard bus size                                                          |
| `metro.train_capacity`, `default_headway_min` | 1500, 6 | metro service                                                        |
| `criticality.fail`                 | `[road, metro, metro_station]` | the asset types to fail                                       |
| `criticality.road_classes`         | null (all)        | can limit which road edges are failed. No limit by default                 |
| `criticality.fallback`             | `metro: [bus, car]` | modes that isolated trips switch to, in order                            |
| `criticality.mode_shift_floor`     | true              | deltas of OD pairs that switch mode are floored at zero                    |
| `criticality.n_batches`            | 64                | the number of criticality jobs                                             |

### Method

**Population.** For each census zone, WorldPop is scaled by
`census_population / worldpop_sum`, so that zone totals match the official
figures exactly. `worldpop-census-check.csv` lists the ratio for each zone
and flags zones outside `check_ratio_tolerance`. Pixels are then aggregated to
H3 cells by their pixel centre. Each pixel also gets employed residents,
using the employed/population ratio of its census zone. Every cell keeps a
population-weighted centroid, which is where its connectors attach.

**Jobs.** For each cell, jobs = Σ over sectors of output / output per worker.
Without the productivity table, total output is used as relative
attractiveness. Either way, jobs are then scaled so that total jobs equal
total employed residents (the island is treated as closed).

**OD: the radiation model.** We use tfm's `radiation_flows`, which was
extended for this work (see [Changes to
transport_flow_model](#changes-to-transport_flow_model)):

$$ T_{ij} = T_i \cdot \frac{m_i\, n_j}{(m_i + s_{ij})(m_i + n_j + s_{ij})} $$

- $T_i$ is the employed residents at $i$ (trip production).
- $n_j$ is the jobs at $j$.
- $s_{ij}$ is the jobs in every zone at least as close to $i$ as $j$ is, by
  free-flow road time, excluding $i$ and $j$.
- $m_i$ is the origin mass.

If we set $m_i$ to the jobs at $i$, cells with no jobs (common at H3
resolution 8) send all their trips to the nearest cell with jobs. So the
default is $m_i$ = employed residents, with jobs scaled to the same total.
Rows are renormalised, so that every employed resident makes one trip within
`max_impedance`. OD pairs below `min_trips` are dropped, and their trips are
spread back over the same origin's other pairs. The road skim comes from tfm's
`RadiationModel.impedance_matrix` (igraph Dijkstra, directed).

**Mode split.** There is no mode-choice data, so every OD pair takes the
same national walk / car / bus / metro shares. Bus and metro are separate
modes:

- bus is cheap and covers the whole island;
- metro is a single, relatively expensive line;
- shares might be derived from ridership statistics (TBC).

The metro share applies only where both ends can walk to a station (within
`metro_walk_max_km`). Elsewhere it goes to `metro_fallback`. The bus share
applies only where both ends can walk to a stop, and otherwise goes to car.
Walk trips are dropped from assignment.

**Multimodal network.** Node and link naming is documented in
`src/transport/land.py`.

- *Road*: one directed link per direction of travel.
- *Car connectors*: each `zone/<id>/car` centroid is linked to its `car_k`
  nearest road nodes, in both directions.
- *Bus*: one link per route, direction and consecutive stop pair, using the
  scheduled between-stop time. Each bus link stores the `road_edge_ids` it
  runs over (draft: the fastest road path between the snapped stops), so that
  a road failure removes the bus links on that road.
- *Metro*: station-to-station links in each direction.
- *Boarding*: boarding links carry the weighted half-headway wait, the
  transfer penalty and the fare. Each stop and station is split into a
  departure side (walk in, then board) and an arrival side (alight, then walk
  out), joined from arrival to departure for changing routes. A trip
  therefore cannot walk through a stop without riding.
- *Walking*: `zone/<id>/bus` centroids walk to bus stops within
  `walk_max_km`, and `zone/<id>/metro` centroids walk to stations within
  `metro_walk_max_km`.
- *No feeder links*: bus and metro are not connected, so each mode's trips
  stay on its own network.

Each link's cost is `per_minute × time_min + per_km[mode] × length_km + fare`.
Capacity is in persons per modelled period. For bus and metro it is
`(60 / headway) × hours × vehicle capacity`. Roads are unconstrained.

**Failure units.** Each physical asset is one failure unit, listing every link
it removes:

- a road edge removes both of its directions, plus every bus link whose
  `road_edge_ids` include it;
- a metro segment removes both of its directions;
- a metro station closure removes every boarding and alighting link at the
  station. Trains still run through it.

Road junctions are not failed.

**Baseline.** We run `tfm.assign(..., "sequential", capacity_constrained=True,
directed=True, include_paths=True)`. Demand that cannot be routed is recorded
in `unassigned.parquet`. With `pt_overflow: car`, bus and metro demand over
capacity goes to the car centroids in a second pass. For each OD pair,
`od-journeys.parquet` holds the trips and the mean time, distance and cost per
trip, recomputed from the edges along each path.

**Criticality.** Only failure units that carry at least `min_baseline_flow`
are evaluated. They are dealt round-robin into batches in descending order of
flow. In each batch, `tfm.disrupt(network, scenarios, base=baseline,
capacity_constrained=True)` reroutes only the baseline paths that use a
removed link, keeping all other flows in place. tfm reports cost only, so for
each affected OD pair we compare the time, distance and cost of the rerouted
paths with those of the same flow on its baseline paths
(`transport.costs.disruption_deltas`).

**Mode fallback.** Trips left with no path on their own mode switch to the
modes in `criticality.fallback`, in order (`transport.fallback.mode_fallback`).
The default sends metro trips to bus, then car. Each fallback mode is
assigned on the disrupted network, between that mode's zone centroids, on top
of the flows already there. So bus capacity still binds, and anything that
doesn't fit moves on to car. What is left is isolated. Bus trips cut off by a
road failure do not fall back (that could be added as `bus: [car]`).

With fixed mode shares, switching mode can look like an improvement. For
example, car is often cheaper than metro in generalised cost, but people were
assigned to metro anyway. So with `mode_shift_floor` (the default), the
time / distance / cost deltas of OD pairs that switch mode are floored at
zero: the baseline mode is taken as preferred. As a result, metro failures
show up mainly as `shifted_to_bus` / `shifted_to_car` trips, rather than as
time or cost.

The outputs for each asset are:

- `affected_flow`, `rerouted_flow` and `isolated_flow` (trips in the period).
  Isolated trips are counted, not costed.
- `delta_time_min_total` (extra person-minutes);
- `delta_length_km_total` (extra person-km);
- `delta_cost_total` (MUR);
- `shifted_to_bus`, `shifted_to_car`: trips that switched mode;
- the same quantities split by original mode (`_car`, `_bus`, `_metro`);
- ranks, and shares of the baseline totals.

`asset-criticality.geoparquet` joins all of this to the road edge or metro
segment geometry. Metro station geometry is still to do.

Rerouted car traffic is not capacity constrained. A speed-flow (congestion)
response to rerouting would need tfm support (see below).

### Scale and performance

Mauritius main island is about 1,860 km², or about 2,500 H3 resolution-8
cells.

- *Road skim*: tfm `impedance_matrix` for 2,500 zones on a synthetic network
  with 90,000 nodes and 180,000 links takes about 40 s. It runs twice, once
  for time and once for distance.
- *Radiation*: tfm `radiation_flows` takes about 1 s for 2,500 zones.
- *OD pairs*: there could be a few million before pruning. `min_trips` and
  `max_impedance` keep this down, and the path table (`edge_path` lists) is
  the main memory cost.
- *Criticality*: there are tens of thousands of road edges with flow, and
  there is no class restriction. tfm only reroutes affected paths, and the
  batches run in parallel. Runtime is still to be measured. If it is too
  slow, merging degree-2 road nodes would cut the number of edges without
  dropping any roads.

### Status of each rule

| Rule                            | Status                                                                                    |
| ------------------------------- | ----------------------------------------------------------------------------------------- |
| `economic_activity_h3`          | **stub**: to be wired to the economics workflow                                           |
| `transport_census_zones`        | written; needs the real column names and handling of total rows                           |
| `transport_scale_worldpop`      | written                                                                                   |
| `transport_residents_h3`        | written                                                                                   |
| `transport_jobs_h3`             | written (wide schema, output per worker)                                                  |
| `transport_zones`               | written; study-area clip TODO                                                             |
| `transport_road_network`        | written; connected-component cleaning TODO; run on toy data                               |
| `transport_bus_network`         | draft; timetable times; road mapping uses the fastest path, not map matching; run on toy data |
| `transport_metro_network`       | draft; run on toy data                                                                    |
| `transport_multimodal_network`  | draft; run on toy data                                                                    |
| `transport_road_skim`           | written (tfm); run on toy data                                                            |
| `transport_radiation_od`        | written (tfm); run on toy data                                                            |
| `transport_mode_split`          | written; national shares are placeholders; run on toy data                                |
| `transport_baseline_assignment` | written; run on toy data                                                                  |
| `transport_failure_sets`        | written; run on toy data                                                                  |
| `transport_criticality_batch`   | written, with mode fallback; run on toy data                                              |
| `transport_criticality_summary` | written; run on toy data (station geometry TODO)                                          |

"Run on toy data" means every script from the road network to the summary ran
on a synthetic 5×5 grid with one bus route and a three-station metro line. The
population and jobs scripts have not been run, because they need rasters and
H3 inputs. The DAG has been checked with `snakemake -n` against placeholder
inputs.

### Decisions so far

- **OD estimation**: tfm's radiation model, extended for separate masses and
  correct intervening opportunities.
- **Zones**: H3 resolution 8.
- **Modes**: car, bus and metro as separate modes, each with its own centroid,
  in one multimodal network.
- **Mode shares**: single national shares, with bus and metro given
  separately. The values still need research (e.g. from ridership
  statistics).
- **Metro access**: walking only for now (no feeder bus or park and ride).
- **Metro disruption**: metro trips fall back to bus, then car. Deltas for
  mode switches are floored at zero.
- **Bus data**: route frequencies and between-stop timings, with a standard
  vehicle size.
- **Roads**: unconstrained, including under disruption.
- **Failures**: road edges (all classes), metro segments and metro stations.
  Not road junctions.
- **Isolated trips**: counted, not costed.
- **tfm dependency**: a git install pinned to a commit.

### Open questions

1. **Mode share values.** Which ridership statistics (metro daily boardings,
   bus passengers per day) and which commuting fraction should set the
   national shares?
2. **Bus fallback.** Should bus trips cut off by a road failure fall back to
   car (`fallback: {bus: [car]}`), or stay isolated?
3. **Mode-shift floor.** Is flooring the deltas of mode-switching trips at
   zero the right way to value a forced mode switch? An alternative would be
   to report the raw (possibly negative) deltas separately.
4. **Economic layer.** Who owns `economic_activity_h3`, and is
   `processed/economy/{zoning}/economic-activity.parquet` the right output
   path? Is the column naming (`h3_index`, `gva_<ISIC section>`) right?
5. **Census details.** Which census year is it, and what are the zone codes
   and column names? Are there total/subtotal rows to drop? Does employment
   cover informal work?
6. **Intra-zonal trips.** At resolution 8 these are short walks. Is
   `intrazonal_share = 0` acceptable?
7. **Metro data.** We need Metro Express headways, train capacity, run times
   and fares.
8. **Costs.** We need agreed values of time and vehicle operating costs, and
   bus fares.
9. **Rodrigues.** Should it be excluded, or run as a separate study area?
10. **tfm release.** When the tfm branch is merged, should we publish a
    release? Publishing needs manylinux / macOS / Windows wheels, for example
    via maturin-action. The current publish workflow (`python -m build`)
    would produce a plain `linux_x86_64` wheel, which PyPI rejects.

### Changes to transport_flow_model

These are on the tfm branch `claude/land-transport-workflow-draft-j0njkf`,
which mu-star pins in `environment.yaml`.

- *Packaging*: the build backend is now maturin, so installs contain the
  package and the Rust extension. Previously the published 0.2.0rc1 wheel
  contained only `_version.py`. The version now comes from `Cargo.toml`
  (`0.2.0-rc.2`).

- `RadiationModel.generate(..., destination_relevance_column=...)` takes
  separate origin and destination masses. It also has a `normalisation`
  option (`finite`, the previous default; `row`; or `none`).
- Intervening opportunities $s_{ij}$ are now the zones at least as close to
  the origin as the destination (Simini et al.). Previously they were all
  zones within the threshold. This changes `generate` results.
- `RadiationModel.flows` returns Demand-ready flows, with `min_flow` pruning.
- `RadiationModel.impedance_matrix` uses igraph Dijkstra, optionally directed.
- `radiation_probabilities` and `radiation_flows` are vectorised matrix
  functions. They replace a per-pair Python loop that was O(Z³).

Possible further tfm work:

- *Path attributes*: `assign` and `disrupt` could report several path
  attributes (for example time and length), not only cost. We currently
  recompute them in mu-star.
- *Congestion*: the `congestion` method is GB-specific (miles and mph, GBP
  fuel price, GB road labels) and only available in `assign`, not `disrupt`.
  A congestion response to rerouting needs a general speed-flow method that
  `disrupt` can use.
- *Capacity*: capacity has to be finite. We use 1e15 for unconstrained
  links.
