# Transport

## Land transport: journey-to-work and school criticality (draft)

> **Status: draft.** The rules, file locations, wildcards and schemas below
> are the agreed starting point. The scripts are written and run end to end
> on a small synthetic network, but have not been run on real data. The
> [open questions](#open-questions) list the decisions still to make.

The analysis asks: **if one piece of the land transport network fails, how much
longer, further and costlier do journeys to work and school become, and how
many can no longer be made at all?**

Mauritius and Rodrigues are separate study areas (the `{island}` wildcard),
with no commuting between them. Air and sea links between the islands are
estimated separately (see [Inter-island links](#inter-island-links-draft)).
Sensitivity runs, such as different commute fractions, are named scenarios
(the `{scenario}` wildcard).

For each island there are four stages:

1. **Demand.** Estimate OD matrices between H3 cells for two purposes:
   - *journeys to work*: employed residents (2022 census, distributed using
     WorldPop) to jobs (mu-star's H3 economic activity layer, converted with
     sectoral productivity);
   - *journeys to school*, per level: school-age children by residence to
     school places.

   Both use the radiation model from
   [transport_flow_model](https://github.com/nismod/transport_flow_model)
   (tfm). On Mauritius, the journey-to-work settings are calibrated against
   the census district-to-district table (CA27).
2. **Network.** Build one directed multimodal network of roads, bus routes and
   the metro, with zone connectors. Each link carries a generalised cost in MUR
   per trip.
3. **Baseline.** Split trips into walk, cycle, car, bus and metro:
   - Mauritius uses national shares, with bus and metro derived from
     reported daily boardings;
   - Rodrigues uses shares by distance band;
   - school trips use shares per level.

   Car, bus and metro trips are assigned together with tfm, so work and
   school trips share bus and metro capacity. The assignment is sequential
   and capacity constrained, with roads unconstrained.
4. **Criticality.** Fail each road edge, metro segment and metro station on
   its own:
   - car trips reroute;
   - buses detour around the failure, or skip stops they cannot reach;
   - metro trips with no metro path left fall back to bus, then car.

   We report the changes in journey time, distance, fare and cost by mode
   and purpose, and count the trips that are isolated.

```text
census CA27 (district OD) ───────────────────────────────────────────────────────────┐ calibration + validation
census CA1 + Appendix II-IV codes + boundaries ─┬─> WorldPop scaled ─> residents per H3 cell ─┐
WorldPop ───────────────────────────────────────┘           └─> children per H3 cell ─────────┤
economic_activity_h3 (output by sector) ──(output per worker)──> jobs per H3 cell ────────────┤
schools ──────────────────────────────────────────────────────────────────────────────────────┤
                                                                                  zones {island}
roads ─> road network ─┬─> bus segments ─┬─> bus detours ─┐                                   │
bus stops/routes/times ┘                 └────────────────┤                                   │
metro stations/lines ─> metro segments ───────────────────┴─> multimodal network {scenario}   │
                                                              + failure units                 │
road network ─> car base network ─> road skim ─> work OD, school OD <─────────────────────────┘
                                                 └─> demand by mode {scenario}
                       multimodal network + demand by mode ─> baseline assignment
                                                                        │
                                                       failure sets ─> criticality batches ─> asset criticality
```

### Running

| What                             | Where                                                                  |
| -------------------------------- | ---------------------------------------------------------------------- |
| Demand and network rules         | `workflow/0-preprocess/transport_land.smk`                             |
| Assignment and criticality rules | `workflow/2-losses/transport_land.smk`                                 |
| Inter-island rules               | `workflow/0-preprocess/transport_interisland.smk`                      |
| Jobs layer (stub)                | `economic_activity_h3` in `workflow/0-preprocess/economy.smk`          |
| Parameters                       | `config/transport/transport.yaml`                                      |
| Scripts                          | next to the rule files                                                 |
| Helpers                          | `src/transport/` (`census.py`, `costs.py`, `detours.py`, `fallback.py`, `fares.py`, `land.py`, `network.py`) |
| Synthetic test network           | `src/transport/testing.py`, used by `src/transport/tests/test_integration.py` |

```bash
# everything: both islands, default zoning, default scenarios
snakemake -c8 transport_land_all
# one island and scenario
snakemake -c8 data/out/transport/land/mauritius/h3-r8/central/criticality/asset-criticality.geoparquet
# inter-island
snakemake -c1 data/out/transport/interisland/criticality.csv
```

The analysis needs `nismod-tfm` and `h3`, both in `environment.yaml`. tfm is
installed from a pinned git commit, which builds its Rust extension, so
`rust` is in the environment too. A proper tfm release is planned.

### Wildcards

| Wildcard     | Values                                        | Meaning                                                                    |
| ------------ | --------------------------------------------- | -------------------------------------------------------------------------- |
| `{data}`     | `data`                                        | data root                                                                  |
| `{island}`   | `mauritius`, `rodrigues`                      | separate study areas, with no commuting between them                       |
| `{zoning}`   | `h3-r8` (`h3-r\d+`)                           | zone system for the economic layer, OD, connectors and results             |
| `{scenario}` | `central`, `commute-low`, `commute-high`, …   | named overrides (`transport_land.scenarios`), from the multimodal network onwards |
| `{batch}`    | `batch-0000` … (`batch-\d{4}`)                | one criticality job (count: `criticality.n_batches`)                       |

**Scenarios** are named sets of config overrides, with dotted keys allowed,
for example `mode_split.ridership.commute_fraction: {bus: 0.3, metro: 0.3}`.

- *What they can change*: mode split, period, cost, bus, metro, school and
  criticality settings. These feed the multimodal network, the mode split,
  assignment and criticality.
- *What they share*: zones, the car base network, the OD estimates, the
  road skim and the bus detours are the same for every scenario. So
  `bus.speed_factor` and `criticality.bus_detour` cannot be set in a
  scenario (`read_config` raises an error if they are).
- *Defaults*: the scenarios run by default are listed in
  `default_scenarios`: commute fractions of 0.5 (`central`), 0.3 and 0.7.

**Per-island settings** go in `islands.<island>.overrides`. For example,
Rodrigues uses distance-band mode shares.

### Data

Every input dataset, its source and status, where it goes under
`{data}/incoming/`, and its schema, plus the location and columns of every
processed and output file, are listed in [Transport data
requirements](./transport-data.md).

In short:

- *Census 2022*: Table CA1 (employed by area), Appendices II–IV (area
  codes), area boundaries, Table CA27 (residence × workplace district), and
  children by area and age band. These are mostly transcribed from Volume
  VII.
- *Population and jobs*: WorldPop, the H3 economic activity layer (mu-star),
  and output per worker by sector.
- *Schools* per island.
- *Networks* per island: roads, bus stops, routes and stop sequences (with
  timings and fare stages), and the metro stations, lines, station order and
  fare stage matrix.
- *Inter-island*: services, airports and ports, and observed traffic.

Processed data are under `{data}/processed/transport/national/` (census and
H3 population, jobs and children) and `{data}/processed/transport/{island}/`
(zones, networks, demand). Results are under
`{data}/out/transport/land/{island}/{zoning}/{scenario}/` and
`{data}/out/transport/interisland/`.

### Parameters and their sources

All parameters live in `config/transport/transport.yaml`. All money is in
MUR at 2026 prices. "Assumption" means a model choice, not an agreed or
published value.

**Ridership and mode shares**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Bus boardings per day | 412,000 | Reported, 410,000–425,000 (June 2026 parliamentary estimate) |
| Metro boardings per day | 36,000 | Reported, 35,000–36,000 (July 2026); 42,000 on a recorded weekday (FY 2024/25) |
| Commute fraction of boardings | 0.5 (scenarios 0.3, 0.7) | **Unknown**: no survey. Sensitivity scenarios |
| Boardings per journey; journeys per commuter | 1.2 bus / 1.1 metro; 2 | Assumption |
| Walk share, Mauritius | 0.05 | Assumption |
| Rodrigues shares by distance band | walk / cycle / car / bus | Assumption (no ridership figures; much walking and cycling on short trips) |
| School shares per level | e.g. secondary: walk 0.15, bus 0.55, metro 0.08 | Assumption |

**Metro service**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Main line headway | 10 min | Published nominal. Sensitivity 10–25 |
| Branch headway | 15 min | Assumption |
| Vehicle capacity | 422 | Published maximum (CAF Urbos 100) |
| Journey-time checks | Port Louis – Curepipe 52, – Rose Hill 23, – Quatre Bornes 29 min | Published itinerary |

**Bus service**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Vehicle capacity | 70 | Assumption |
| Peak capacity used by other purposes | 12% (`period.other_purpose_load_share`, one value for bus and metro) | Placeholder. Covers purposes other than work and school |

**Fares and costs**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Bus fares, routing | Rs 30 + Rs 0.5/km | Approximation |
| Bus fares, reporting | Rs 17 / 24 / 30 / 35 / 39 / 41 / 44 / 47 by number of stages | Road Traffic (Bus Fares) (Amendment) Regulations 2022 |
| Metro fares, routing | Rs 32 + Rs 0.8/km | Approximation |
| Metro fares, reporting | Rs 32 / 38 / 43 / 47 / 52 for stages 1–5 | MECard, 2023 conditions of travel |
| Value of time | Rs 85/h, all modes | Assumption (range Rs 55–130/h, from 2025 earnings) |
| Car cost | Rs 6.2 per person-km | Fuel + maintenance ~Rs 8/vehicle-km ÷ 1.3 occupancy |

**Network and disruption settings**

| Parameter | Value | Source / status |
| --- | --- | --- |
| Wait and walk weights; boarding penalty | 1.5, 1.5; 5 min | Assumption |
| Peak period (work) | 60% of daily trips, 2 h | Assumption |
| Bus detours: max skipped stops, max detour factor | 3; 4× | Assumption |

### Method

**Population.** For each census area, WorldPop is scaled to the area total.
That is total population where CA1 gives it, otherwise employed residents.
Pixels are aggregated to H3 cells by their centre. Each cell gets the census
area containing its centre, which gives it a district and an island. Only
one island's cells are kept, and its jobs are scaled to its employed
residents.

**Jobs.** For each cell, jobs = Σ over sectors of output / output per worker.

**Journeys to work: the radiation model.**

$$ T_{ij} = T_i \cdot \frac{m_i\, n_j}{(m_i + s_{ij})(m_i + n_j + s_{ij})} $$

- $T_i$ is the employed residents at $i$.
- $n_j$ is the jobs at $j$.
- $s_{ij}$ is the jobs in every other zone at least as close to $i$ as $j$ is.
- $m_i$ is the origin mass.

Rows are normalised. With `include_self`, a cell competes as its own
destination, at the mean travel time within a cell (about 1.3 min at
resolution 8), so the model decides how many trips stay local. If no other
cell is closer than that, the share that stays is $n_i/(m_i+n_i)$, which may
be too many. Calibration compares this with not allowing trips within a
cell. Trips within a cell are not assigned.

**Calibration (Mauritius).** tfm's `calibrate_radiation` tries every
combination of:
- the impedance (free-flow road time or distance);
- the cut-off;
- the origin mass;
- whether trips can stay within a cell.

Each run is scored against CA27 by district (by default `cpc_shares`: the
common part of commuters after scaling CA27 to the modelled total). The best
run is used, and every candidate's score is in `radiation-calibration.csv`.
The model only depends on the ranking of destinations, so calibration has
little to adjust. If it fits poorly, a parameterised model, such as the
extended radiation model with a scale parameter (Yang et al. 2014), is the
next step. Scaling the OD to the CA27 totals (`district_od: constrain`, IPF)
is the fallback.

**Journeys to school.**
1. Children by census area and age band are matched to codes and spread by
   the WorldPop pattern. Age bands are grouped into levels (primary,
   secondary), and enrolment rates applied.
2. Per level, the radiation model sends children to school places
   (enrolment), on the same road skim, with a cut-off and trips within a cell
   allowed. Schools outside the island's zone cells are moved to the nearest
   zone.

**Mode split.** For each purpose and OD pair:
- Mauritius journeys to work (`ridership`): bus (or metro) commuters =
  boardings × commute fraction ÷ boardings per journey ÷ journeys per
  commuter, divided by the island's between-cell commuters. Walk is
  `walk_share`, and car takes the rest.
- Rodrigues (`bands`): shares by road distance band, including walking and
  cycling.
- School trips: shares per level.
- Metro applies only where both ends can walk to a station (otherwise it
  goes to bus). Bus applies only where both ends reach a stop (otherwise
  car).
- Walk and cycle trips are counted, not assigned.

**Car base network.** The road skim runs on its own network: the directed
road links and car connectors, with free-flow time and length and no
scenario settings (`transport_car_network`). The multimodal network builds
the same links with the same code (`transport.network`). Zones with no
road node within `connectors.car_max_km` get no car connector, and so no car
trips. They are listed in `zones-without-car-access.csv`.

**Multimodal network.** Node and link naming is documented in
`src/transport/land.py`.

- *Road*: one directed link per direction of travel.
- *Car connectors*: each `zone/<id>/car` centroid is linked to its nearest
  road nodes.
- *Bus*: one link per route, direction and consecutive stop pair, using the
  scheduled time. Each bus link stores the `road_edge_ids` it runs over (the
  fastest road path between snapped stops, for now) and its fare stages.
- *Metro*: station-to-station links per line and direction.
- *Boarding*: boarding links carry the weighted half-headway wait, the
  boarding penalty and the boarding fare. In-vehicle links carry the per-km
  fare.
- *Stops and stations* are split into a departure side and an arrival side,
  joined arrival → departure for interchange.
- *Walking*: bus centroids walk to stops, and metro centroids walk to
  stations. Bus and metro are not connected.

Each link's cost is `per_minute × time_min + per_km[mode] × length_km + fare`.
Bus and metro capacity per period is `(60 / headway) × hours × vehicle
capacity × (1 − other_purpose_load_share)`. Roads are unconstrained.

**Failure units.** Each physical asset is one failure unit:

- a *road edge* removes both of its directions. Buses over it detour, skip
  stops or are cut (below).
- a *metro segment* removes both of its directions.
- a *metro station closure* removes every boarding and alighting link at the
  station.

**Bus detours** (`transport.detours`, rule `transport_bus_detours`). They
are computed once per island, for every road edge that buses use, and
written to `detours.parquet`. When a road edge fails, each bus route over it
is handled stop by stop:

1. *Detour*: take the fastest road path avoiding the failure between the same
   two stops. The in-vehicle time becomes the scheduled time plus (detour road
   time − original road time) ÷ `speed_factor`.
2. *Skip*: if the next stop can't be reached within `max_detour_factor` × the
   original road time, skip it and try the following stop, up to
   `max_skipped_stops` in a row. Skipped stops lose their boarding and
   alighting links for that route. Passengers riding through stay on the bus.
3. *Cut*: if no stop can be reached, the segment is removed and the route is
   cut there.

The multimodal network turns them into `link_changes` on the failure unit
(new time, length and fare, with the scenario's fares). Passengers on them keep their path, with the new journey
time.

**Baseline.** All purposes and modes are assigned together:
`tfm.assign(..., "sequential", capacity_constrained=True, directed=True,
include_paths=True)`. Paths are per node pair, so the purpose split of each
pair is kept separately in `purpose-shares.parquet`. `od-journeys.parquet`
holds time, distance, fare and cost per trip.

**Fares for reporting** (`transport.fares`). Routing uses the linear fare
approximation, because stage fares are not link-additive. Reported fares are
recomputed on the assigned paths:
- each *bus leg* (board, ride one route, alight) is priced by stages
  travelled, `|stage at alighting − stage at boarding| + 1`, using the
  statutory schedule;
- each *metro journey* (first boarding to last alighting, including a change
  between lines) is priced from the station-to-station stage matrix.

Where stage data are missing, the approximation is used. Each new boarding
pays a new fare, because there is no integrated ticketing. The generalised
cost is reported with the exact fares.

**Criticality.** For each failure unit with baseline flow, in batches:
1. `tfm.disrupt` reroutes the baseline paths that use a removed link, on top
   of all other flows.
2. Bus detours (`link_changes`) are applied to the paths that use them.
3. Trips left with no path on their own mode switch to the modes in
   `criticality.fallback`: metro to bus, then car. Bus trips do not switch
   mode: buses detour or skip stops instead. What is left is isolated.
4. Time, distance, fare and cost are compared per OD pair, then split by
   purpose with the baseline shares.

Changes for trips that switch mode are reported **raw**. They can be
negative, for example when car is cheaper than metro. A `shifted` flag lets
later analysis floor them. Setting `criticality.mode_shift_floor: true`
floors them in the outputs instead.

The outputs for each asset are:
- `affected_flow`, `rerouted_flow`, `isolated_flow` (counted, not costed);
- `delta_time_min_total` (person-minutes), `delta_length_km_total`
  (person-km), `delta_fare_total` and `delta_cost_total` (MUR);
- `shifted_to_bus`, `shifted_to_car`;
- the same quantities split by original mode and by purpose (`_work`,
  `_school-primary`, `_school-secondary`).

The output is joined to the road edge, metro segment or station geometry.

### Inter-island links (draft)

This is a separate estimate of passengers and freight between Mauritius,
Rodrigues, Agaléga and St Brandon, by air and sea.

**Baseline passengers** (annual, two-way):

| Connection                | Passengers                        | Treatment                                         |
| ------------------------- | --------------------------------- | ------------------------------------------------- |
| Mauritius – Rodrigues air | ~280,000–315,000                  | Airport statistics and arrivals series            |
| Mauritius – Rodrigues sea | unknown, likely small             | Capacity-constrained scheduled maritime link      |
| Mauritius – Agaléga sea   | unknown                           | Irregular charter service                         |
| Mauritius – Agaléga air   | no scheduled commercial demand    | Exceptional movements                             |
| Mauritius – St Brandon    | unknown                           | Irregular                                         |

Sources: the 2025 Rodrigues Airport development presentation, Mauritius
Shipping Corporation sailing schedules, and the Blue Economy annual report
2025/26.

**Inputs**:
- `links.csv`: per service, with mode, from/to node, services per week,
  passenger and freight capacity, and `service_type` (scheduled, charter or
  irregular);
- `nodes.gpkg`: airports and ports, with their island;
- `traffic.csv` (optional): observed passengers and freight by link,
  direction, year and commodity.

Links without observed traffic use the baseline table: the midpoint, split
over the pair's links of that mode and both directions. Unknown values stay
empty, not zero.

**Flows** (`link-flows.csv`): traffic, capacity and utilisation per link and
direction.

**Criticality** (`criticality.csv`): each link, airport and port fails in
turn. Per direction of travel between two islands, we report the affected
passengers and freight, the part that other *scheduled* services in the same
direction can absorb within spare capacity, and what is stranded. Freight is
limited to specific modes and does not switch (`switchable.freight: false`).

### Scale and performance

Mauritius is about 2,500 H3 resolution-8 cells.

- *Road skim*: about 40 s on a synthetic network with 90,000 nodes, run
  twice.
- *Radiation*: about 1 s per run, with 40 calibration runs.
- *Criticality*: 64 batches per island and scenario, so `transport_land_all`
  is 436 jobs for 2 islands × 3 scenarios.
- *Bus detours*: one Dijkstra (with a limit) per stretch per failed road edge
  that buses use, once per island. The road graph is built once, and each
  failure only swaps the weights of its own arcs (the next fastest parallel
  arc takes over), so a 60 × 60 grid with 232 failed edges takes about
  0.15 s.
- *Still to measure*: the criticality runtime. Merging degree-2 road nodes
  would cut the number of edges.

### Status of each rule

| Rule                                | Status                                                                     |
| ----------------------------------- | -------------------------------------------------------------------------- |
| `economic_activity_h3`              | **stub**: path and columns need checking                                   |
| `transport_census_zones`            | written (CA1 by name to Appendix II–IV codes); not run                     |
| `transport_census_district_od`      | written; run on a mock CA27                                                |
| `transport_scale_worldpop`          | written; not run                                                           |
| `transport_residents_h3`            | written; not run                                                           |
| `transport_jobs_h3`                 | written; not run                                                           |
| `transport_school_children`         | draft (children table source TBC); not run                                 |
| `transport_zones`                   | written; not run                                                           |
| `transport_road_network`            | written; run on toy data                                                   |
| `transport_bus_network`             | draft (fare stages; road mapping without map matching); run on toy data    |
| `transport_metro_network`           | draft (lines, journey-time checks); run on toy data, including no metro    |
| `transport_bus_detours`             | written; run on toy data                                                   |
| `transport_multimodal_network`      | draft; run on toy data                                                     |
| `transport_car_network`             | written (reports zones without car access); run on toy data                |
| `transport_road_skim`               | written; run on toy data                                                   |
| `transport_radiation_od`            | written, calibration; run on toy data                                      |
| `transport_school_od`               | draft; run on toy data                                                     |
| `transport_mode_split`              | written (ridership, bands, school); run on toy data                        |
| `transport_baseline_assignment`     | written (purposes, exact fares); run on toy data                           |
| `transport_failure_sets`            | written; run on toy data                                                   |
| `transport_criticality_batch`       | written (detours, fallback, raw changes, purposes); run on toy data        |
| `transport_criticality_summary`     | written; run on toy data                                                   |
| `transport_interisland_flows`       | draft; run on toy data                                                     |
| `transport_interisland_criticality` | draft; run on toy data                                                     |

"Run on toy data" means the script ran on a synthetic 5×5 grid with one bus
route, a three-station metro line, a mock CA27, three schools and an
inter-island fixture. The DAG has been checked with `snakemake -n` for both
islands, three scenarios and the inter-island rules. Helper modules have
unit tests (`src/transport/tests`), and `test_integration.py` runs every land
transport script on the same toy network (generated by
`src/transport/testing.py`) and checks the main results.

### Decisions so far

- **OD estimation**: tfm's radiation model, calibrated against CA27, with
  scaling to CA27 as the fallback. Same-cell trips are decided by the model
  (wait and see whether they are over-predicted).
- **Purposes**: work, and school as a second purpose (primary, secondary)
  with its own origins and destinations, sharing bus and metro capacity.
- **Zones**: H3 resolution 8, from census areas (Wards / VCAs, Rodrigues
  regions) via CA1 and Appendix II–IV codes.
- **Islands**: Mauritius and Rodrigues are separate, with no land commuting
  between them. Inter-island air and sea flows are estimated separately, and
  freight does not switch mode.
- **Mode shares**: on Mauritius, national shares with bus and metro from
  ridership, and the commute fraction as sensitivity scenarios. On
  Rodrigues, shares by distance band, with walking and cycling.
- **Disruption**:
  - roads are unconstrained;
  - buses detour or skip stops, and bus trips don't switch to car;
  - metro trips fall back to bus, then car;
  - mode-switch changes are reported raw.
- **Failures**: road edges (all classes), metro segments and metro stations.
- **Fares**: exact stage fares for reporting where stage data exist,
  otherwise per km.
- **Isolated trips**: counted, not costed.
- **Costs**: provisional values, to be confirmed.
- **tfm**: git install for now. A proper release is planned (merging and
  tagging done by the maintainer).

### Open questions

1. **Economic layer.** Who wires up `economic_activity_h3`? Are its path and
   columns right?
2. **Census areas.** Where do the area boundaries (Wards / VCAs, Rodrigues
   regions) come from? Does CA1 include Rodrigues, or is there a separate
   table? Which table gives total population and children by age band per
   area?
3. **Schools.** Is there a school list with level, enrolment and location
   (e.g. from the Ministry of Education)? Are primary catchments or
   secondary admission rules worth representing, rather than a radiation
   model? Should tertiary or vocational trips be included?
4. **Other-purpose capacity.** `period.other_purpose_load_share` is a 12%
   placeholder for trips that are neither work nor school. Is there any
   evidence for a better value?
5. **Bus fare stages.** Can `fare_stage` per stop be extracted from the
   timetable and fare data? If not, per-km fares are used. Do the special
   schedules (routes 38, 38A, 79, 215, air-conditioned express) matter?
6. **Inter-island data.** We need service schedules and capacities (Air
   Mauritius, MSC), and freight by commodity and mode (port and airport
   statistics).
7. **Same-cell trips** (wait and see). If the calibrated model over-predicts
   them, the options are to down-weight a cell's own destination mass, or to
   use the extended radiation model. The second needs the formula checked
   (arxiv.org is not reachable from this session).

### Changes to transport_flow_model

These are on the tfm branch `claude/land-transport-workflow-draft-j0njkf`,
which mu-star pins in `environment.yaml`.

- *Packaging*: the build backend is now maturin, so installs contain the
  Rust extension. The version is `0.2.0-rc.2`.
- *Radiation*: separate origin and destination masses; intervening
  opportunities as in Simini et al. (this changes `generate` results);
  `include_self`; a `normalisation` option.
- *New functions*:
  - `RadiationModel.flows` and `RadiationModel.impedance_matrix`;
  - `radiation_probabilities` and `radiation_flows` (vectorised);
  - `aggregate_od`, `scale_to_aggregate`, `common_part_of_commuters` and
    `od_fit` (OD constraints and validation);
  - `calibrate_radiation`.

Possible further tfm work:

- *Release*: per-platform wheels, for example via maturin-action.
- *Disruption*: `disrupt` could support link cost changes, not only removals,
  for the bus detours that mu-star now applies itself. It could also report
  several path attributes, not only cost.
- *Congestion*: the `congestion` method is GB-specific and only in `assign`.
- *Capacity*: capacity has to be finite; we use 1e15 for unconstrained
  links.
