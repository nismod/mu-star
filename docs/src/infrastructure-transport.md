# Transport

## Land transport: journey-to-work criticality (draft)

> **Status: draft.** The rules, file locations, wildcards and schemas below
> are the agreed starting point. The scripts are written and run end to end
> on a small synthetic network, but have not been run on real data. The
> [open questions](#open-questions) list the decisions still to make.

The analysis asks: **if one piece of the land transport network fails, how much
longer, further and costlier do people's journeys to work become, and how many
can no longer make the trip at all?**

Mauritius and Rodrigues are separate study areas (the `{island}` wildcard),
with no commuting between them. Air and sea links between the islands are
estimated separately (see [Inter-island links](#inter-island-links-draft)).

For each island there are four stages:

1. **Demand.** Estimate a journey-to-work origin–destination (OD) matrix
   between H3 cells:
   - employed residents come from the 2022 census, distributed using WorldPop;
   - jobs come from mu-star's H3 economic activity layer, converted with
     sectoral productivity;
   - a radiation model from
     [transport_flow_model](https://github.com/nismod/transport_flow_model)
     (tfm) links the two.

   On Mauritius the radiation settings are calibrated against the census
   district-to-district travel-to-work table (CA27).
2. **Network.** Build one directed multimodal network of roads, bus routes and
   the metro, with zone connectors. Each link carries a generalised cost in MUR
   per trip.
3. **Baseline.** Split trips into walk, car, bus and metro with national
   shares (bus and metro derived from reported daily boardings). Then assign
   peak-period demand with tfm. The assignment is sequential and capacity
   constrained: bus and metro capacity binds, and roads are unconstrained.
4. **Criticality.** Fail each road edge, metro segment and metro station on
   its own and reroute the affected trips. Metro trips with no metro path left
   fall back to bus, then car. Report the increases in journey time, distance
   and cost, and count the trips that are isolated.

```text
census CA27 (district OD) ───────────────────────────────────────────────────────┐ calibration + validation
census zones + Appendix II districts + boundaries ─┬─> WorldPop scaled ─> residents per H3 cell ─┐
WorldPop ──────────────────────────────────────────┘                                            │
economic_activity_h3 (output by sector) ──(output per worker)──> jobs per H3 cell ──────────────┤
                                                                                     zones {island}
roads ─> road network ─┬─> bus segments ─┐                                                      │
bus stops/routes/times ┘                  ├─> multimodal network + failure units <──────────────┘
metro stations/lines ─> metro segments ───┘          │
                                                     ├─> road skim ─> radiation OD ─> demand by mode
                                                     └──────────> baseline assignment <────┘
                                                                        │
                                                       failure sets ─> criticality batches ─> asset criticality
```

### Running

| What                                  | Where                                                  |
| ------------------------------------- | ------------------------------------------------------ |
| Demand and network rules              | `workflow/0-preprocess/transport_land.smk`             |
| Assignment and criticality rules      | `workflow/2-losses/transport_land.smk`                 |
| Inter-island rules                    | `workflow/0-preprocess/transport_interisland.smk`      |
| Jobs layer (stub)                     | `economic_activity_h3` in `workflow/0-preprocess/economy.smk` |
| Parameters                            | `config/transport/transport.yaml`                      |
| Scripts                               | next to the rule files                                 |
| Helpers                               | `src/transport/` (`costs.py`, `fallback.py`, `land.py`) |

```bash
# everything: both islands, default zoning
snakemake -c8 transport_land_all
# one island
snakemake -c8 data/out/transport/land/mauritius/h3-r8/criticality/asset-criticality.geoparquet
# inter-island
snakemake -c1 data/out/transport/interisland/criticality.csv
```

The analysis needs `nismod-tfm` and `h3`, both in `environment.yaml`. tfm is
installed from a pinned git commit, which builds its Rust extension, so
`rust` is in the environment too. A proper tfm release is planned.

### Wildcards

| Wildcard   | Values                         | Meaning                                                                    |
| ---------- | ------------------------------ | -------------------------------------------------------------------------- |
| `{data}`   | `data`                         | data root                                                                  |
| `{island}` | `mauritius`, `rodrigues`       | separate study areas, with no commuting between them                       |
| `{zoning}` | `h3-r8` (`h3-r\d+`)            | zone system for the economic layer, OD, connectors and results. H3 resolution 8 is about 0.7 km² |
| `{batch}`  | `batch-0000` … (`batch-\d{4}`) | one criticality job. The count is `criticality.n_batches` (default 64)     |

### File layout

```text
{data}/incoming/                                   provided (schemas below)
  Socio-economic/Census/2022/census-residential-zones.xlsx, .gpkg
  Socio-economic/Census/2022/appendix-ii-zone-districts.csv
  Socio-economic/Census/2022/CA27-residence-by-workplace-district.csv
  Socio-economic/GDP/output-per-worker-by-sector.csv
  Population/WorldPop/mus_ppp_2022_constrained.tif
  Infrastructure/Transport/{island}/road/roads.gpkg
  Infrastructure/Transport/{island}/bus/bus_stops.gpkg, bus_routes.gpkg, bus_route_stops.csv
  Infrastructure/Transport/mauritius/metro/metro_stations.gpkg, metro_lines.gpkg, metro_line_stations.csv
  Infrastructure/Transport/interisland/links.csv, nodes.gpkg, traffic.csv

{data}/processed/economy/{zoning}/economic-activity.parquet   rule economic_activity_h3 (stub)

{data}/processed/transport/national/                        both islands
  population/census-zones.geoparquet
  population/census-district-od.parquet, census-district-od-summary.json
  population/worldpop-scaled.tif, worldpop-census-check.csv
  zones/{zoning}/residents.parquet, jobs.parquet

{data}/processed/transport/{island}/
  zones/{zoning}/zones.geoparquet
  networks/road/edges.geoparquet, nodes.geoparquet
  networks/bus/stops.geoparquet, segments.geoparquet
  networks/metro/stations.geoparquet, segments.geoparquet      (empty on Rodrigues)
  networks/multimodal/{zoning}/links.parquet, nodes.geoparquet, failure-units.parquet
  demand/{zoning}/road-skim.parquet
  demand/{zoning}/commute-od.parquet, commute-od-summary.json
  demand/{zoning}/radiation-calibration.csv, district-od-comparison.csv
  demand/{zoning}/demand-by-mode.parquet, demand-by-mode-summary.json

{data}/out/transport/land/{island}/{zoning}/
  baseline/link-flows.parquet, paths.parquet, od-journeys.parquet, unassigned.parquet, summary.json
  criticality/failure-sets.parquet
  criticality/batches/{batch}/scenario-impacts.parquet, od-impacts.parquet
  criticality/asset-criticality.csv, asset-criticality.geoparquet

{data}/out/transport/interisland/
  link-flows.csv, criticality.csv
```

### Data inputs

All input paths are set in the config: national inputs under
`transport_land.inputs`, and network inputs per island under
`transport_land.islands.<island>.inputs`. Where we provide the data ourselves
(roads, bus, metro), the schema below is what we have chosen. Any CRS is
accepted, because everything is reprojected to `crs_metric` (EPSG:32740).

**2022 Housing and Population Census.** The reference date is 3 July 2022,
and the employment reference period 27 June – 3 July 2022. The economic
tables are in Volume VII: Economic Characteristics (December 2024).
Employment covers anyone aged 16+ who worked at least one hour for pay,
profit or family gain. That includes informal work, though it may be
under-reported.

- *Census geography* (Methodology Report §3.4) uses hierarchical codes:
  island (1 digit; 1 = Mauritius), district (+1; 11 = Port Louis), municipal
  ward or village council area (+2; 1112 = Port Louis Ward 2), and
  enumeration area (+2). Codes are kept as strings, so leading zeros survive.
- *Zone table* (`census_table`, columns mapped in config): `zone_id`
  (4-digit code), `population`, and `employed_residents`. Aggregate rows
  (Republic, island, district, urban/rural) are dropped by code length.
  Optionally, only the `Both sexes` rows are kept.
- *Zone → district lookup* (`census_zone_districts`): transcribed from
  Volume VII Appendix II (pp. 474–476), with columns `zone_id`, `zone_name`
  and `district_code`. Without it, the district is the first two digits of
  the zone code.

  | Code | District           | Code | District         |
  | ---- | ------------------ | ---- | ---------------- |
  | 11   | Port Louis         | 16   | Savanne          |
  | 12   | Pamplemousses      | 17   | Plaines Wilhems  |
  | 13   | Rivière du Rempart | 18   | Moka             |
  | 14   | Flacq              | 19   | Black River      |
  | 15   | Grand Port         |      |                  |

- *Boundaries*: polygons of the census zones, with the same code.
- *Table CA27* (Volume VII, p. 347; district breakdown from p. 349) gives
  the employed population by residence district (rows) and workplace
  district (columns), with sex and age breakdowns.
  - The workplace columns are the nine districts plus *Not stated*,
    *Rodrigues and Outer Islands*, and *Outside Mauritius* (which includes
    work abroad).
  - It is transcribed from the PDF to a CSV with one row per residence
    district × sex × age group: `residence_district`, `sex`, `age_group`, one
    column per workplace, and `Total`.
  - We use only the *Both sexes*, all-ages rows for the nine residence
    districts, and the nine workplace districts.
  - The three other workplace columns are kept in the summary so that the
    excluded counts stay auditable, and each row is checked against its
    published total.

**WorldPop** is a gridded population raster (about 100 m) in persons per
pixel. We use it only for the spatial pattern within each census zone, after
scaling it to the census totals. Ideally the 2022 product would be used.

**H3 economic activity** is the output of the rule `economic_activity_h3`
(a stub). It is wide: one row per H3 cell (`h3_index`, at the `{zoning}`
resolution or finer), and one output column per sector named `gva_<sector>`.
**Sectoral productivity**, `output-per-worker-by-sector.csv`, has the columns
`sector` and `output_per_worker`. It is to be built from Statistics Mauritius
national accounts and employment by industry.

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

**Bus**, with route frequencies and between-stop timings:

- `bus_stops.gpkg`: `stop_id`, `name`, Point geometry
- `bus_routes.gpkg`: `route_id`, `name`, `operator`, and the peak
  `frequency_per_hour` (or `headway_min`). Geometry is optional and used
  later for map matching.
- `bus_route_stops.csv`: `route_id`, `direction` (0/1), `stop_sequence`,
  `stop_id`, and `time_to_next_min` (scheduled minutes to the next stop)

Every route uses the standard vehicle size `bus.vehicle_capacity`.

**Metro** (Mauritius only):

- `metro_stations.gpkg`: `station_id`, `name`, Point geometry
- `metro_lines.gpkg`: `line_id` (`main` for Port Louis – Curepipe, `branch`
  for Rose Hill – Réduit) and the line's geometry
- `metro_line_stations.csv`: `line_id`, `station_id`, `seq`, and optionally
  `run_time_min_to_next`

A station on both lines is an interchange.

### Parameters and their sources

All parameters live in `config/transport/transport.yaml`. All money is in
MUR at 2026 prices. "Assumption" means a model choice, not an agreed or
published value.

**Ridership and mode shares**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Bus boardings per day | 412,000 | Reported: ~412,000–425,000 (June 2026 parliamentary estimate) |
| Metro boardings per day | 36,000 | Reported: 35,000–36,000 (July 2026); 42,000 on a recorded weekday (FY 2024/25) |
| Commute fraction of boardings | 0.5 bus, 0.5 metro | **Unknown**: no survey. An uncalibrated parameter for sensitivity |
| Boardings per one-way journey | 1.2 bus, 1.1 metro | Assumption (transfers count twice) |
| Journeys per commuter per day | 2 | Out and back |
| Walk share (between cells) | 0.05 | Assumption |

**Metro service**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Main line headway | 10 min | Published nominal (Metro Express). Sensitivity 10–25 |
| Branch headway | 15 min | Assumption (published 10–15 Mon–Thu, 20–25 Fri–Sun) |
| Vehicle capacity | 422 | Published maximum, incl. standing (CAF Urbos 100, 18 vehicles) |
| Peak capacity used by non-work trips | 25% | Assumption: students are 15–18% of metro passengers; other purposes unknown |
| Journey-time checks | Port Louis – Curepipe 52, – Rose Hill 23, – Quatre Bornes 29 min | Published itinerary; compared with modelled run times when the network is built |
| Speed where no run times are given | 30 km/h | Main line ~26 km in ~52 min |

**Bus service**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Vehicle capacity | 70 | Assumption (standard vehicle) |
| Peak capacity used by non-work trips | 30% | Assumption (school and other purposes also load the weekday peak) |

**Fares**

Fares are approximated as a boarding charge plus a charge per km.

| Parameter | Value | Source / status |
| --- | --- | --- |
| Bus | Rs 30 + Rs 0.5/km | Approximates the statutory stage fares (Rs 17–47; Port Louis – Rose Hill Rs 39, – Curepipe Rs 44, – Mahébourg Rs 47) |
| Metro | Rs 32 + Rs 0.8/km | Approximates MECard stage fares (Rs 32–52 for stages 1–5; full line ~Rs 52) |

**Costs**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Value of time | Rs 85/h (Rs 1.42/min), all modes | Assumption: commuting, range Rs 55–130/h, based on 2025 mean employee earnings (Rs 37,200/month, ~Rs 215/h) |
| Car cost | Rs 6.2 per person-km | Perceived fuel + maintenance ~Rs 8/vehicle-km (petrol Rs 77.70/l at 6–9 l/100 km), ÷ occupancy 1.3 |
| Wait and walk time weight, boarding penalty | 1.5, 1.5, 5 min | Assumption |

**Model settings**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Peak period | 60% of daily commuting, 2 h | Assumption |
| Radiation settings | calibrated against CA27 | See the method |

Further notes on these sources:

- *Ridership*: these are boardings from different periods and day types, not
  door-to-door journeys. The commute fractions are the weakest numbers. A
  2021 operator survey said most metro respondents travelled to work, but
  gave no share. Students are 15–18% of metro passengers.
- *Value of time*: World Bank Mauritius road appraisals (2008 prices: Rs 50/h
  for working passengers, Rs 20–25/h otherwise) are the local precedent, but
  are out of date.
- *Car costs*: the full car operating cost (~Rs 12/vehicle-km) is a resource
  cost, and is not used for routing. Bus operating costs (~Rs 35–70/km) fall
  on the operator.

### Method

**Population.** For each census zone, WorldPop is scaled by
`census_population / worldpop_sum`, so that zone totals match the official
figures exactly. `worldpop-census-check.csv` lists the ratio for each zone
and flags zones outside `check_ratio_tolerance`. Pixels are aggregated to H3
cells by their pixel centre. Employed residents per pixel use the
employed/population ratio of the pixel's census zone. Every cell keeps a
population-weighted centroid, where its connectors attach.

**Zones.** Each H3 cell gets the census zone that contains its centre, which
gives it a district and an island. Only one island's cells are kept. Its jobs
are scaled to its employed residents, so the island is closed and jobs and
workers are in the same units.

**Jobs.** For each cell, jobs = Σ over sectors of output / output per worker.
Without the productivity table, total output is used as relative
attractiveness.

**OD: the radiation model.** We use tfm's `radiation_flows`, which was
extended for this work (see [Changes to
transport_flow_model](#changes-to-transport_flow_model)):

$$ T_{ij} = T_i \cdot \frac{m_i\, n_j}{(m_i + s_{ij})(m_i + n_j + s_{ij})} $$

- $T_i$ is the employed residents at $i$.
- $n_j$ is the jobs at $j$.
- $s_{ij}$ is the jobs in every other zone at least as close to $i$ as $j$ is.
- $m_i$ is the origin mass: employed residents or jobs.

Rows are normalised, so every employed resident makes one trip within
`max_impedance`.

*Trips within a cell.* With `include_self`, a cell also competes as its own
destination, at the mean travel time within the cell (0.83 × the H3 cell
circumradius at the connector speed: about 1.3 min, or 0.44 km, at
resolution 8). The model decides how many trips stay in the cell. If no
other cell is closer than that, the share that stays is $n_i / (m_i + n_i)$,
whatever the cell size. That is about half where jobs and workers balance,
which may be too many at resolution 8. Calibration compares this with not
allowing trips within a cell, using CA27's within-district share. Trips
within a cell are not assigned to the network.

**Calibration (Mauritius).** The radiation model has no free parameter, and
it depends only on the ranking of destinations by impedance. The choices
that remain are:

- the impedance: free-flow road time or distance;
- the cut-off (`max_impedance`);
- the origin mass;
- whether trips can stay within a cell.

tfm's `calibrate_radiation` runs every combination in
`radiation.calibration`. Each run is aggregated to districts, trips within a
cell included, and scored against CA27. The best run is used. The default
score is `cpc_shares`: the common part of commuters after scaling CA27 to the
modelled total, so it rates the pattern rather than the total. Every
candidate's score is in `radiation-calibration.csv`, and the settings used
are in the summary.

Calibration has little to work with. If it fits poorly, the next step is a
parameterised model, such as the extended radiation model of Yang et al.
(2014) with a scale parameter α. That would need checking against the
original paper before implementing.

**Validation and constraint.** The chosen run is compared with CA27 by
district pair. We report CPC, R², RMSE and the share of trips within each
district (`district-od-comparison.csv` and the summary). Scaling the cell OD
to the CA27 totals is kept as a heavier-handed fallback
(`radiation.district_od: constrain`). It uses IPF in tfm's
`scale_to_aggregate`, which also keeps each cell's production.

Rodrigues has no district table. It uses the configured settings and is not
validated.

**Mode split.** There is no mode-choice data, so every OD pair between cells
takes the same national shares:

- with `method: ridership`, bus (or metro) commuters = boardings × commute
  fraction ÷ boardings per journey ÷ journeys per commuter, divided by the
  island's between-cell commuters;
- walk is `walk_share`, and car takes the rest;
- the metro share applies only where both ends can walk to a station
  (otherwise it goes to bus), and bus only where both ends reach a stop
  (otherwise car).

On Rodrigues there is no metro, so its metro share becomes bus. Applying
national boardings there is wrong: Rodrigues needs its own figures (see the
open questions).

**Multimodal network.** Node and link naming is documented in
`src/transport/land.py`.

- *Road*: one directed link per direction of travel.
- *Car connectors*: each `zone/<id>/car` centroid is linked to its `car_k`
  nearest road nodes.
- *Bus*: one link per route, direction and consecutive stop pair, using the
  scheduled time. Each bus link stores the `road_edge_ids` it runs over (the
  fastest road path between snapped stops, for now), so that a road failure
  removes it.
- *Metro*: station-to-station links per line and direction.
- *Boarding*: boarding links carry the weighted half-headway wait, the
  boarding penalty and the boarding fare. In-vehicle links carry the per-km
  fare.
- *Stops and stations* are split into a departure side and an arrival side,
  joined arrival → departure for interchange. A trip therefore cannot walk
  through a stop without riding.
- *Walking*: `zone/<id>/bus` centroids walk to bus stops, and
  `zone/<id>/metro` centroids walk to stations. Bus and metro are not
  connected.

Each link's cost is `per_minute × time_min + per_km[mode] × length_km + fare`.
Capacity is in persons per modelled period. For bus and metro it is
`(60 / headway) × hours × vehicle capacity × (1 − other_purpose_load_share)`.
The metro main line's full theoretical capacity is 2,532 per hour per
direction. The `other_purpose_load_share` factor reserves the part used by
school and other non-work trips, because only journeys to work are modelled.
Roads are unconstrained.

**Failure units.** Each physical asset is one failure unit, listing every link
it removes:

- a road edge removes both of its directions, plus every bus link that runs
  over it;
- a metro segment removes both of its directions;
- a metro station closure removes every boarding and alighting link at the
  station.

Road junctions are not failed.

**Baseline.** We run `tfm.assign(..., "sequential", capacity_constrained=True,
directed=True, include_paths=True)`. Unroutable demand is recorded in
`unassigned.parquet`. `od-journeys.parquet` holds the trips and the mean
time, distance and cost per trip for each OD pair.

**Criticality.** Failure units with at least `min_baseline_flow` are dealt
into batches. In each batch, `tfm.disrupt` reroutes only the baseline paths
that use a removed link, on top of all other flows. Time, distance and cost
are recomputed from the edges along each path
(`transport.costs.disruption_deltas`).

**Mode fallback.** Trips left with no path on their own mode switch to the
modes in `criticality.fallback`, in order, on the disrupted network
(`transport.fallback.mode_fallback`). By default, metro falls back to bus,
then car, and bus capacity still binds. What is left is isolated.

With fixed mode shares, switching mode can look like an improvement,
because car is often cheaper in generalised cost. So by default
(`mode_shift_floor`), the deltas of OD pairs that switch mode are floored at
zero: the baseline mode is taken as preferred. Metro failures therefore
show up mainly as `shifted_to_bus` / `shifted_to_car`.

The outputs for each asset are:

- `affected_flow`, `rerouted_flow` and `isolated_flow` (trips in the period).
  Isolated trips are counted, not costed.
- `delta_time_min_total` (extra person-minutes);
- `delta_length_km_total` (extra person-km);
- `delta_cost_total` (MUR);
- `shifted_to_bus`, `shifted_to_car`;
- the same quantities split by original mode;
- ranks, and shares of the baseline totals.

The output is joined to the road edge, metro segment or station geometry.
Rerouted car traffic is not capacity constrained.

### Inter-island links (draft)

This is a separate estimate of passengers and freight between Mauritius,
Rodrigues and the outer islands, by air and sea.

- *Inputs*:
  - `links.csv`: one row per service, with mode, from/to node,
    services per week, and passenger and freight capacity per service;
  - `nodes.gpkg`: airports and ports, with their island;
  - `traffic.csv`: annual passengers and freight per link and direction.
- *Flows* (`link-flows.csv`): annual and daily traffic, capacity and
  utilisation per link and direction.
- *Criticality* (`criticality.csv`): fail each link, airport and port in
  turn. For each island pair we report the affected traffic, the part the
  other links can absorb in their spare capacity (e.g. sea instead of air),
  and what is stranded. Freight cannot always switch to air, which still
  needs a switchability factor.

### Scale and performance

Mauritius is about 1,860 km², or about 2,500 H3 resolution-8 cells.

- *Road skim*: tfm `impedance_matrix` takes about 40 s on a synthetic
  network with 90,000 nodes. It runs twice, for time and for distance.
- *Radiation*: about 1 s per run for 2,500 zones. The default calibration
  grid has 40 runs.
- *Paths*: the path table (`edge_path` lists) is the main memory cost.
- *Criticality*: tens of thousands of road edges carry flow. Runtime is
  still to be measured. Merging degree-2 road nodes would cut the number of
  edges without dropping any roads.

### Status of each rule

| Rule                                | Status                                                                                |
| ----------------------------------- | ------------------------------------------------------------------------------------- |
| `economic_activity_h3`              | **stub**: to be wired to the economics workflow                                       |
| `transport_census_zones`            | written (codes, Appendix II lookup, aggregate rows dropped); not yet run              |
| `transport_census_district_od`      | written for the CA27 transcription layout; run on a mock CA27 CSV                     |
| `transport_scale_worldpop`          | written; not yet run                                                                  |
| `transport_residents_h3`            | written; not yet run                                                                  |
| `transport_jobs_h3`                 | written; not yet run                                                                  |
| `transport_zones`                   | written (island filter, job scaling); study-area clip not needed (island codes)       |
| `transport_road_network`            | written; connected-component cleaning TODO; run on toy data                           |
| `transport_bus_network`             | draft; road mapping uses the fastest path, not map matching; run on toy data          |
| `transport_metro_network`           | draft (lines, branch); run on toy data, including the no-metro case                   |
| `transport_multimodal_network`      | draft; run on toy data                                                                |
| `transport_road_skim`               | written (tfm); run on toy data                                                        |
| `transport_radiation_od`            | written (tfm), calibration + validation; run on toy data, with and without CA27       |
| `transport_mode_split`              | written (ridership-derived shares); run on toy data                                   |
| `transport_baseline_assignment`     | written; run on toy data                                                              |
| `transport_failure_sets`            | written; run on toy data                                                              |
| `transport_criticality_batch`       | written, with mode fallback; run on toy data                                          |
| `transport_criticality_summary`     | written (road, metro and station geometry); run on toy data                           |
| `transport_interisland_flows`       | draft; not yet run                                                                    |
| `transport_interisland_criticality` | draft; not yet run                                                                    |

"Run on toy data" means the script ran on a synthetic 5×5 grid with one bus
route and a three-station metro line, with a mock CA27. The DAG has been
checked with `snakemake -n` against placeholder inputs for both islands and
the inter-island rules.

### Decisions so far

- **OD estimation**: tfm's radiation model, extended for separate masses,
  correct intervening opportunities and trips within a cell. Calibrated
  against CA27; scaling to CA27 is only a fallback.
- **Zones**: H3 resolution 8. Districts come from census codes and the
  Appendix II lookup.
- **Islands**: Mauritius and Rodrigues are separate study areas with no
  commuting between them. Inter-island air and sea flows are estimated
  separately.
- **Modes**: car, bus and metro are separate modes, each with its own
  centroid, in one multimodal network. National shares, with bus and metro
  derived from ridership.
- **Metro**: main line and branch. Walking access only.
- **Disruption**: roads unconstrained. Metro trips fall back to bus, then
  car, with mode-switch deltas floored at zero.
- **Failures**: road edges (all classes), metro segments and metro stations.
- **Isolated trips**: counted, not costed.
- **Costs**: provisional values from the sources above, to be confirmed.
- **tfm**: git install for now. A proper release is planned (merging and
  tagging done by the maintainer).

### Open questions

1. **Bus fallback.** Should bus trips cut off by a road failure fall back to
   car (`fallback: {bus: [car]}`), or stay isolated?
2. **Mode-shift floor.** Is flooring the deltas of mode-switching trips at
   zero acceptable, or should the raw (possibly negative) deltas be reported
   alongside?
3. **Economic layer.** Who wires up `economic_activity_h3`? Is the path
   `processed/economy/{zoning}/economic-activity.parquet` right, and are the
   column names (`h3_index`, `gva_<ISIC section>`) right?
4. **Census zone table.** Which file and column headers? Is the census
   island code for Rodrigues 2?
5. **Commute fractions and other purposes.** The bus and metro commute
   fractions, and the share of peak capacity taken by school and other
   trips, are unknown. Should we run a sensitivity range (e.g. 0.3 / 0.5 /
   0.7), for example as a scenario wildcard, along with the value of time
   (Rs 55–130/h) and metro headways (10–25 min)? Or model school trips as a
   second purpose class?
6. **Rodrigues ridership.** National boardings don't apply to Rodrigues.
   Which bus ridership figures exist for it, or should we use fixed shares
   there?
7. **Same-cell trips.** If calibration prefers allowing trips within a cell
   but over-predicts them, should we weight the cell's own destination mass?
   Or move to the extended radiation model? arxiv.org would need to be
   reachable to check the Yang et al. (2014) formula.
8. **Exact fares.** Should we compute stage-based fares on the assigned paths
   for reporting (this needs fare stages per stop and the metro stage
   matrix), keeping the linear approximation for routing?
9. **Inter-island data.** Which sources for services, capacities and traffic
   (airport and port statistics, the Mauritius Shipping Corporation)? Do we
   need freight by commodity, to tell whether freight can switch mode?

### Changes to transport_flow_model

These are on the tfm branch `claude/land-transport-workflow-draft-j0njkf`,
which mu-star pins in `environment.yaml`.

- *Packaging*: the build backend is now maturin, so installs contain the
  package and the Rust extension. The version comes from `Cargo.toml`
  (`0.2.0-rc.2`).
- *Radiation inputs*: `RadiationModel.generate(...,
  destination_relevance_column=...)` takes separate origin and destination
  masses. There is a `normalisation` option (`finite`, `row` or `none`).
- *Intervening opportunities*: these are now the zones at least as close to
  the origin as the destination (Simini et al.). This changes `generate`
  results.
- *Trips within a zone*: `include_self` makes a zone its own destination, at
  the impedance on the diagonal.
- *New functions*:
  - `RadiationModel.flows` returns Demand-ready flows;
  - `RadiationModel.impedance_matrix` runs igraph Dijkstra;
  - `radiation_probabilities` and `radiation_flows` are vectorised matrix
    functions;
  - `aggregate_od`, `scale_to_aggregate` (block or IPF),
    `common_part_of_commuters` and `od_fit` constrain and validate an OD
    against aggregate data;
  - `calibrate_radiation` grid-searches the radiation settings against
    aggregate data.

Possible further tfm work:

- *Release*: publishing needs per-platform wheels, for example via
  maturin-action. The current workflow would produce a plain `linux_x86_64`
  wheel, which PyPI rejects.
- *Path attributes*: `assign` and `disrupt` could report several path
  attributes, not only cost.
- *Congestion*: the `congestion` method is GB-specific and only available in
  `assign`.
- *Capacity*: capacity has to be finite; we use 1e15 for unconstrained
  links.
