# Transport data requirements

This page lists every dataset the land transport and inter-island analyses
need, where each one comes from, where it goes in the data directory, and the
schema it must follow. It also lists every processed and output file the
workflow writes. The method is described in [Transport](./infrastructure-transport.md).

The paths in the config (`config/transport/transport.yaml`) are relative to
the data root, the `{data}` wildcard (normally `data/`). The tables below
use the default paths. If a file has to live somewhere else, change the
config, not the scripts.

## Conventions

| Topic | Convention |
| --- | --- |
| Identifiers | Strings, unique within a file, and stable between versions: results are reported against them. Census codes keep leading zeros. |
| Census codes (2022) | Island (1 digit: 1 Mauritius, 2 Rodrigues, 3 Agaléga), district (+1: 11–19), area (+2), e.g. `1201` Arsenal (Pamplemousses). |
| H3 cells | `h3_index` strings at the zoning resolution (`h3-r8` = resolution 8), or finer where stated. |
| CRS | Provided vector data can be in any CRS that is defined in the file. Processed spatial files are in EPSG:32740 (WGS 84 / UTM 40S). |
| Formats | Vector inputs: GeoPackage (or GeoParquet / GeoJSON). Tables: CSV. Processed: GeoParquet for spatial data, Parquet for tables, CSV / JSON for small summaries. |
| Units | Time in minutes, distance in km, money in MUR at 2026 prices, trips in persons (per day, or per modelled peak period where stated). |
| Transcriptions | Tables taken from PDFs are transcribed to CSV with the printed names kept as printed. Aggregate rows are flagged or dropped, never mixed with area rows, and the source page is recorded in a `source` column where practical. |

## Status key

| Status | Meaning |
| --- | --- |
| **have** | identified and available, to be placed in `incoming/` |
| **transcribe** | in a published PDF; needs transcribing to the CSV schema below |
| **build** | to be assembled by us from several sources (e.g. GTFS-like bus files) |
| **mu-star** | produced by another part of the mu-star workflow |
| **find** | source still to be found or confirmed |
| **open** | downloaded and built automatically from published open data by the named rule |
| **open fallback** | built automatically from open data as a stand-in until better (provided or official) data arrive |

## Input inventory

| # | Dataset | Used for | Source | Status | Location under `{data}/` |
| --- | --- | --- | --- | --- | --- |
| 1 | Census Table CA1: employed by area, with total population | employed residents (trip origins) | 2022 census workbooks: Vol. VII sheet CA1; population from Vol. II sheet D2 | **open** (`transport_open_census_tables`) | `incoming/Socio-economic/Census/2022/CA1-employment-by-area.csv` |
| 2 | Census area codes | matching area names to codes; district and island | Vol. VII Appendix II; Rodrigues regions from Appendix IV with synthetic codes 2101–2106 (none are published) | **open** (`transport_open_census_tables`) | `incoming/Socio-economic/Census/2022/appendix-ii-iv-area-codes.csv` |
| 3 | Census area boundaries | spreading area totals over WorldPop | OpenStreetMap admin boundaries (levels 8 and 9), matched to the codes. Not official census boundaries | **open** (`transport_open_census_boundaries`) | `incoming/Socio-economic/Census/2022/census-areas.gpkg` |
| 4 | Census Table CA27: residence × workplace district | calibrating and validating journeys to work | Vol. VII sheet CA27 | **open** (`transport_open_census_tables`) | `incoming/Socio-economic/Census/2022/CA27-residence-by-workplace-district.csv` |
| 5 | Children by area and age band | origins of school trips | Vol. II sheet D2 (5-year bands); 10–11 and 12–14 split using district single-year shares from D1 | **open** (`transport_open_census_tables`) | `incoming/Socio-economic/Census/2022/children-by-area-age.csv` |
| 6 | Total population by area | scaling WorldPop to people | Vol. II sheet D2 | **open** (the `population` column of item 1) | column in item 1 |
| 7 | WorldPop gridded population | spatial pattern within areas | WorldPop Global2 R2025A, 2022, 100 m constrained (CC BY 4.0) | **open** (`transport_open_osm_worldpop_total`) | `incoming/Population/WorldPop/mus_ppp_2022_constrained.tif` |
| 8 | H3 economic activity (output by sector) | jobs (trip destinations) | mu-star economics workflow, rule `economic_activity_h3` | mu-star (stub; path and columns need checking) | `processed/economy/{zoning}/economic-activity.parquet` |
| 9 | Output per worker by sector | converting output to jobs | Statistics Mauritius: GVA by industry (National Accounts 2013–2026 series, Table 3, 2022) ÷ 2022 census employment by industry (Vol. VII, CA3) | **open** (`transport_open_stats_output_per_worker`) | `incoming/Socio-economic/GDP/output-per-worker-by-sector.csv` |
| 10 | Schools | destinations of school trips | Official geoportal primary school list plus OpenStreetMap; no enrolment, so placeholder capacities. Ministry of Education list still wanted | **open fallback** (`transport_open_osm_schools`) | `incoming/Education/{island}/schools.gpkg` |
| 11 | Road network | car routing, bus mapping, criticality assets | provided; else OpenStreetMap (ODbL) | **open fallback** (`transport_open_osm_roads`) | `incoming/Infrastructure/Transport/{island}/road/roads.gpkg` |
| 12 | Bus stops | bus network | provided; else www.mauritius-buses.com (transcribed from NLTA timetables, no explicit reuse licence) with stop locations from OpenStreetMap. Locations are approximate | **open fallback** (`transport_open_bus_write`) | `incoming/Infrastructure/Transport/{island}/bus/bus_stops.gpkg` |
| 13 | Bus routes and frequencies | bus network, capacity | provided; else www.mauritius-buses.com (transcribed from NLTA timetables, no explicit reuse licence) with stop locations from OpenStreetMap | **open fallback** (`transport_open_bus_write`) | `incoming/Infrastructure/Transport/{island}/bus/bus_routes.gpkg` |
| 14 | Bus route stop sequences, timings, fare stages | bus network, fares | provided; else www.mauritius-buses.com (transcribed from NLTA timetables, no explicit reuse licence) with stop locations from OpenStreetMap | **open fallback** (`transport_open_bus_write`) | `incoming/Infrastructure/Transport/{island}/bus/bus_route_stops.csv` |
| 15 | Metro stations | metro network | provided; else OpenStreetMap | **open fallback** (`transport_open_osm_metro`) | `incoming/Infrastructure/Transport/mauritius/metro/metro_stations.gpkg` |
| 16 | Metro lines | metro network | provided; else OpenStreetMap | **open fallback** (`transport_open_osm_metro`) | `incoming/Infrastructure/Transport/mauritius/metro/metro_lines.gpkg` |
| 17 | Metro line station order and run times | metro network | OpenStreetMap route relations (order only; no run times) | **open fallback** (`transport_open_osm_metro`) | `incoming/Infrastructure/Transport/mauritius/metro/metro_line_stations.csv` |
| 18 | Metro fare stage matrix | exact metro fares | Metro Express conditions of travel (2023) | transcribe | `incoming/Infrastructure/Transport/mauritius/metro/metro_fare_stages.csv` |
| 19 | Inter-island services | inter-island flows and criticality | MSC sailing schedules; airline schedules | build | `incoming/Infrastructure/Transport/interisland/links.csv` |
| 20 | Airports and ports | inter-island network | OpenStreetMap aerodromes; ports from a short documented list located in OSM | **open fallback** (`transport_open_osm_interisland_nodes`) | `incoming/Infrastructure/Transport/interisland/nodes.gpkg` |
| 21 | Inter-island traffic (optional) | observed passengers and freight by commodity | by link: still to assemble. Published totals: Statistics Mauritius tourism and Rodrigues digests | find; published totals **open** (`transport_open_stats_traffic`, `traffic-published.csv`) | `incoming/Infrastructure/Transport/interisland/traffic.csv` |

**Open data rules.** `workflow/0-preprocess/transport_open_census.smk` and
`transport_open_osm.smk` download the sources and write these inputs in the
schemas below, so the workflow runs from published data alone. A file placed
by hand at the same location is used as it is. What to know about them:

- *Census workbooks* (Statistics Mauritius, 2022 Housing and Population
  Census, Volumes II and VII, Excel). Every published total is checked
  against the sum of its parts. On area rows, `area_name` is the official
  name from the code list, and the printed name is kept in `printed_name`.
- *Area boundaries* come from OpenStreetMap, not the census office. Two
  tabulated wards (Vacoas-Phoenix Wards 5 and 6 West, codes 1735 and 1736)
  are not mapped. With `open_census_approximate_residuals: true` they are cut
  from the unmapped part of the town by a straight line and flagged
  `approximate`.
- *Schools* have no enrolment. Each gets a flat placeholder capacity by
  level, and OSM misses many secondary schools and most Rodrigues schools.
- *Metro* has no run times, so the default speed is used.
- *Bus* (`transport_open_bus.smk`, Mauritius only): 282 routes with stop
  order, scheduled minutes between stops and, for 87% of stop rows, official
  fare stages, read from www.mauritius-buses.com (about 1,080 pages, fetched
  once at one request per second and cached).
  - **Stop locations are approximate.** The site has no coordinates, so
    stops are located by name against OpenStreetMap. `location_quality`
    records how: 1 = a named OSM bus stop (9% of stops); 2 = a named feature
    or road junction (18%); 3 = a place or named road (32%); 4 = the village
    centroid or interpolated along the route (41%). Typical error is 0.3–1 km
    for levels 2–4, and some are much worse.
  - 41 of 767 stops could not be located and are left out. Two routes are
    dropped for having fewer than two located stops.
  - Headways are the published average frequency, not the peak, and are
    missing for 58 lines (the config default applies).
  - The site has no explicit reuse licence. Confirm with its owner before
    publishing derived files. Official NLTA stop coordinates or a GTFS feed
    would replace all of this.
  - Rodrigues buses are not covered: its bus inputs must be provided.
- *Output per worker* (`transport_open_stats.smk`): run this rule before
  the jobs step, which uses the table only if the file exists.
  - The table has `output_per_worker` and `output_per_worker_adjusted`.
    The adjusted column leaves out global business companies (sections G, K
    and M) and the imputed rent of owner-occupied dwellings (about 78% of
    section L). The jobs step uses it by default
    (`jobs.output_per_worker_column`).
  - Section T (households as employers) shares section S's ratio.
  - The ratios depend on the national accounts basis: the older Digest of
    National Accounts gives noticeably different 2022 values.
- *School enrolment* by district and level (Digest of Education Statistics
  2025) is written to `incoming/Education/enrolment-by-district-level.csv`.
  No school-by-school enrolment is published. It is not yet used to scale
  the placeholder school capacities.
- *Inter-island traffic* (`traffic-published.csv`): Mauritius–Rodrigues
  passengers by direction, 2005–2025, with air and sea combined (about
  155,000 each way in 2025), plus Rodrigues exports to Mauritius by
  commodity. No freight from Mauritius to Rodrigues, and nothing for Agaléga
  or St Brandon, is published.
- *Attribution*: © OpenStreetMap contributors (ODbL; derived files shared
  publicly must also be ODbL); WorldPop (CC BY 4.0); Statistics Mauritius.

Downloaded source files are kept next to the outputs, with a `.source.json`
sidecar (URL, time, checksum) for the census workbooks and the OSM extract.

The scalar parameters (ridership, headways, vehicle capacities, fare
schedules, values of time, operating costs) are not files. They are set in
the config, with their sources. See [Parameters and their
sources](./infrastructure-transport.md#parameters-and-their-sources).

## Input schemas

Required columns are in **bold**. Other columns are optional, and extra
columns are kept or ignored.

### 1. CA1 transcription: `CA1-employment-by-area.csv`

There is one row per printed row of Table CA1. The national and district
aggregates are kept, but flagged.

| Column | Type | Description |
| --- | --- | --- |
| **`area_name`** | str | area name as printed (Municipal Ward, Village Council Area; Rodrigues region) |
| **`district`** | str | the district heading the row is printed under (blank for national rows) |
| **`row_type`** | str | `area` for an individual area, `aggregate` for national, island, district, urban/rural rows |
| **`employed`** | int | *Currently active → Employed* (aged 16+, includes informal work) |
| `population` | int | total population of the area, if available (otherwise WorldPop is scaled to employed residents) |
| `source` | str | page reference, e.g. `Vol VII p. 12` |

The column names can be mapped in `inputs.census_columns`.

### 2. Area codes: `appendix-ii-iv-area-codes.csv`

There is one row per area, for Mauritius (Appendix II) and Rodrigues
(Appendices III–IV).

| Column | Type | Description |
| --- | --- | --- |
| **`zone_id`** | str | official 4-digit area code, e.g. `1201` |
| **`zone_name`** | str | official name, e.g. `Arsenal` |
| **`district_code`** | str | 2-digit district code, e.g. `12` |

CA1 names are matched to this list within their district, ignoring
accents, case and punctuation. Any name that doesn't match goes in
`inputs.census_name_overrides` (`"printed name|district": "code"`).

### 3. Area boundaries: `census-areas.gpkg`

| Column | Type | Description |
| --- | --- | --- |
| **code** (name set in `inputs.census_boundaries_zone_id`) | str | 4-digit area code, matching item 2 |
| **`geometry`** | Polygon / MultiPolygon | area boundary |

Every area in CA1 needs a boundary, and every boundary a CA1 row. Any
mismatch is an error.

### 4. CA27 transcription: `CA27-residence-by-workplace-district.csv`

There is one row per residence district × sex × age group, as printed.

| Column | Type | Description |
| --- | --- | --- |
| **`residence_district`** | str | district name as printed (the nine names in `transport_land.districts`) |
| **`sex`** | str | `Both sexes`, `Male`, `Female` |
| **`age_group`** | str | as printed. The all-ages label is set in `census_district_od_layout.age_value` |
| **`Port Louis`** … **`Black River`** | int | workers by workplace district (nine columns, names as in `transport_land.districts`) |
| **`Not stated`**, **`Rodrigues and Outer Islands`**, **`Outside Mauritius`** | int | non-district workplaces, kept for audit |
| `Total` | int | published row total, used to check the row |

### 5. Children: `children-by-area-age.csv`

| Column | Type | Description |
| --- | --- | --- |
| **`area_name`**, **`district`**, **`row_type`** | str | as for CA1 |
| **`age_band`** | str | as printed. Bands are mapped to school levels in `school.levels` |
| **`children`** | int | resident children in the band |

### 7. WorldPop

This is a single-band GeoTIFF of persons per pixel (about 100 m). Nodata
and negative values are treated as zero.

### 8. H3 economic activity: `economic-activity.parquet`

| Column | Type | Description |
| --- | --- | --- |
| **`h3_index`** | str | H3 cell at the `{zoning}` resolution or finer |
| **`gva_<sector>`** | float | output (GVA) of each sector, one column per ISIC section, e.g. `gva_C` |
| `geometry` | Polygon | cell outline (optional) |

The prefix and cell column name are set in `inputs.economic_activity_columns`.

### 9. Output per worker: `output-per-worker-by-sector.csv`

| Column | Type | Description |
| --- | --- | --- |
| **`sector`** | str | sector code, matching the `gva_<sector>` suffix |
| **`output_per_worker`** | float | output per worker, in the same currency and year as item 8 |

### 10. Schools: `schools.gpkg`

| Column | Type | Description |
| --- | --- | --- |
| **`school_id`** | str | unique id |
| `name` | str | |
| **`level`** | str | `primary` or `secondary` (keys of `school.levels`) |
| **`enrolment`** | int | pupils (or `capacity` if enrolment is not known) |
| **`geometry`** | Point | school location |

### 11. Roads: `roads.gpkg`, layer `edges`

These are LineStrings, noded at junctions: every junction is an edge end.

| Column | Type | Description |
| --- | --- | --- |
| **`edge_id`** | str | unique, stable |
| **`from_node`**, **`to_node`** | str | node ids at the start and end of the geometry |
| **`road_class`** | str | `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential` or `service` |
| **`oneway`** | bool | true: traffic only runs from `from_node` to `to_node` |
| `speed_kph` | float | free-flow speed (default by class) |
| `capacity_vph` | float | vehicles per hour per direction (default by class) |
| `name`, `lanes`, `bridge` | | carried through |

### 12–14. Bus

`bus_stops.gpkg`:

| Column | Type | Description |
| --- | --- | --- |
| **`stop_id`** | str | unique |
| `name` | str | |
| **`geometry`** | Point | stop location |

`bus_routes.gpkg`:

| Column | Type | Description |
| --- | --- | --- |
| **`route_id`** | str | unique |
| `name`, `operator` | str | |
| `frequency_per_hour` or `headway_min` | float | peak service level (default `bus.default_headway_min`) |
| `geometry` | LineString | route shape (optional; for map matching later) |

`bus_route_stops.csv`:

| Column | Type | Description |
| --- | --- | --- |
| **`route_id`** | str | matches `bus_routes` |
| **`direction`** | int | 0 or 1 |
| **`stop_sequence`** | int | order along the route in that direction |
| **`stop_id`** | str | matches `bus_stops` |
| `time_to_next_min` | float | scheduled minutes to the next stop, including dwell; blank on the last stop. Without it, the time comes from road speed |
| `fare_stage` | int | the stop's fare stage number on the route, for exact fares. Without it, fares are per km |

### 15–18. Metro

`metro_stations.gpkg`:

| Column | Type | Description |
| --- | --- | --- |
| **`station_id`** | str | unique |
| **`name`** | str | as in the published itinerary (used for journey-time checks) |
| **`geometry`** | Point | |

`metro_lines.gpkg`:

| Column | Type | Description |
| --- | --- | --- |
| **`line_id`** | str | `main` (Port Louis – Curepipe) or `branch` (Rose Hill – Réduit); keys of `metro.headway_min` |
| **`geometry`** | LineString | line alignment |

`metro_line_stations.csv`:

| Column | Type | Description |
| --- | --- | --- |
| **`line_id`** | str | |
| **`station_id`** | str | |
| **`seq`** | int | order along the line |
| `run_time_min_to_next` | float | timetabled minutes to the next station. Without it, the time uses `metro.default_speed_kph` |

`metro_fare_stages.csv`:

| Column | Type | Description |
| --- | --- | --- |
| **`from_station`**, **`to_station`** | str | station ids (every ordered pair, including both directions) |
| **`stage`** | int | fare stage, 1–5, priced from `cost.stage_fares.metro` |

### 19–21. Inter-island

`links.csv` has one row per service and direction:

| Column | Type | Description |
| --- | --- | --- |
| **`link_id`** | str | unique per direction |
| **`mode`** | str | `air` or `sea` |
| **`from_node`**, **`to_node`** | str | airport or port ids (item 20) |
| `operator` | str | |
| **`services_per_week`** | float | |
| **`passenger_capacity`** | float | per service |
| **`freight_capacity_t`** | float | per service, tonnes |
| **`service_type`** | str | `scheduled`, `charter` or `irregular` (only scheduled services absorb diverted passengers) |

`nodes.gpkg`:

| Column | Type | Description |
| --- | --- | --- |
| **`node_id`** | str | |
| `name` | str | |
| **`island`** | str | `mauritius`, `rodrigues`, `agalega`, `st-brandon` |
| **`kind`** | str | `airport` or `port` |
| **`geometry`** | Point | |

`traffic.csv` is optional. Without it, the baseline table in the config is
used.

| Column | Type | Description |
| --- | --- | --- |
| **`link_id`**, **`from_node`**, **`to_node`** | str | |
| **`year`** | int | latest year is used |
| `passengers` | float | annual |
| `freight_t` | float | annual tonnes |
| `commodity` | str | freight commodity (`all` if not split) |
| `source` | str | |

## Processed data and outputs

These files are written by the workflow. Their locations are fixed by the
rules: change the rules, not the config, to move them. The column lists are
the contract between rules, and with the viewer.

### National: `{data}/processed/transport/national/`

| File | Rule | Columns |
| --- | --- | --- |
| `population/census-zones.geoparquet` | `transport_census_zones` | `zone_id` (4-digit code), `zone_name`, `district` (2-digit), `island_code`, `employed_residents`, `population` (NaN if not given), `scale_target`, `employment_rate`, geometry (Polygon) |
| `population/census-district-od.parquet` | `transport_census_district_od` | `origin_district`, `destination_district` (2-digit codes), `workers` (9 × 9 rows) |
| `population/census-district-od-summary.json` | `transport_census_district_od` | totals, non-district workplaces by residence district, row-total check |
| `population/worldpop-scaled.tif` | `transport_scale_worldpop` | WorldPop scaled to each area's `scale_target` |
| `population/worldpop-census-check.csv` | `transport_scale_worldpop` | `zone_id`, `census_target`, `worldpop_population`, `ratio`, `flagged` |
| `zones/{zoning}/residents.parquet` | `transport_residents_h3` | `h3_index`, `population`, `employed_residents`, `census_zone_id`, `centroid_x`, `centroid_y` (population-weighted, EPSG:32740) |
| `zones/{zoning}/jobs.parquet` | `transport_jobs_h3` | `h3_index`, `output`, `jobs`, `jobs_<sector>` (national, unscaled) |
| `zones/{zoning}/children.parquet` | `transport_school_children` | `h3_index`, `level`, `children` (enrolled, by residence) |

### Per island: `{data}/processed/transport/{island}/`

| File | Rule | Columns |
| --- | --- | --- |
| `zones/{zoning}/zones.geoparquet` | `transport_zones` | `zone_id` (= H3 cell), `population`, `employed_residents`, `jobs` (scaled to the island's employed residents), `output`, `jobs_<sector>`, `census_zone_id`, `district`, `island_code`, geometry (Point: zone centroid) |
| `networks/road/edges.geoparquet` | `transport_road_network` | input columns plus `length_km`, `speed_kph`, `capacity_vph`, `time_min`; LineString |
| `networks/road/nodes.geoparquet` | `transport_road_network` | `node_id`, geometry (Point) |
| `networks/bus/stops.geoparquet` | `transport_bus_network` | input columns plus `road_node`, `snap_m` |
| `networks/bus/segments.geoparquet` | `transport_bus_network` | `segment_id` (`<route>/<direction>/<seq>`), `route_id`, `direction`, `seq`, `from_stop`, `to_stop`, `road_edge_ids` (list), `length_km`, `time_min`, `time_source` (`timetable` or `road`), `headway_min`, `vehicle_capacity`, `stage_from`, `stage_to`; LineString |
| `networks/bus/detours.parquet` | `transport_bus_detours` | one row per road edge that buses use, failed on its own: `road_edge_id`, `changes` (list of {`segment_id`, `time_min`, `length_km`}: detoured bus segments), `skipped_stops` (list of {`route_id`, `direction`, `stop_id`}), `cut_segments` (list of segment ids). Same for every scenario |
| `networks/car/{zoning}/links.parquet` | `transport_car_network` | car base network for the road skim (no scenario settings): `edge_id`, `edge_from`, `edge_to`, `layer` (`road`, `car_connector`), `mode`, `asset_id`, `length_km`, `time_min` (free flow) |
| `networks/car/{zoning}/zones-without-car-access.csv` | `transport_car_network` | zones with no road node within `connectors.car_max_km` (no car connector, so no car trips): `zone_id`, `nearest_road_node`, `nearest_km`, `population`, `employed_residents`, `jobs` |
| `networks/metro/stations.geoparquet` | `transport_metro_network` | one row per line call: `station_id`, `name`, `line_id`, `seq`, `chainage_m`; Point (empty on Rodrigues) |
| `networks/metro/segments.geoparquet` | `transport_metro_network` | `segment_id` (`<line>/<from>-<to>`), `line_id`, `direction`, `from_station`, `to_station`, `length_km`, `time_min`, `headway_min`, `capacity_per_train`; LineString |
| `networks/multimodal/{zoning}/{scenario}/links.parquet` | `transport_multimodal_network` | `edge_id`, `edge_from`, `edge_to`, `layer`, `mode`, `asset_id`, `road_edge_ids`, `length_km`, `time_min`, `fare`, `cost`, `capacity` (persons per period; inf = unconstrained), `route_key`, `stage_from`, `stage_to`, `station_from`, `station_to` (naming: `src/transport/land.py`) |
| `networks/multimodal/{zoning}/{scenario}/nodes.geoparquet` | `transport_multimodal_network` | `node_id`, `kind` (`zone_car`, `zone_bus`, `zone_metro`, `road`, `bus_stop`, `metro_station`), `zone_id`, `has_access`; Point |
| `networks/multimodal/{zoning}/{scenario}/failure-units.parquet` | `transport_multimodal_network` | `asset_type` (`road`, `metro`, `metro_station`), `asset_id`, `road_class`, `link_ids` (list of links removed), `link_changes` (list of {`edge_id`, `time_min`, `length_km`, `fare`}: bus detours from `detours.parquet`, with the scenario's fares) |
| `demand/{zoning}/road-skim.parquet` | `transport_road_skim` | `origin_zone`, `destination_zone`, `time_min`, `length_km` (free flow on the car base network, every reachable pair) |
| `demand/{zoning}/commute-od.parquet` | `transport_radiation_od` | `origin_zone`, `destination_zone`, `trips` (journeys to work per day, between cells) |
| `demand/{zoning}/commute-od-summary.json` | `transport_radiation_od` | settings used (calibrated or from config), totals, within-cell trips, district fit |
| `demand/{zoning}/radiation-calibration.csv` | `transport_radiation_od` | `impedance`, `origin_mass`, `max_impedance`, `include_self`, `cpc`, `cpc_shares`, `r2`, `rmse`, internal shares; best first (empty without CA27) |
| `demand/{zoning}/district-od-comparison.csv` | `transport_radiation_od` | `origin`, `destination` (districts), `modelled`, `observed`, `difference`, `ratio`, `constrained` (if constrained) |
| `demand/{zoning}/school-od.parquet` | `transport_school_od` | `origin_zone`, `destination_zone`, `level`, `trips` (per day, between cells) |
| `demand/{zoning}/school-od-summary.json` | `transport_school_od` | children, places, within- and between-cell trips per level |
| `demand/{zoning}/{scenario}/demand-by-mode.parquet` | `transport_mode_split` | `origin_id`, `destination_id` (network node ids), `value` (persons per peak period), `mode` (`car`, `bus`, `metro`), `purpose` (`work`, `school-<level>`), `origin_zone`, `destination_zone` |
| `demand/{zoning}/{scenario}/demand-by-mode-summary.json` | `transport_mode_split` | trips by purpose and mode, including walk and cycle (not assigned) |

### Results: `{data}/out/transport/land/{island}/{zoning}/{scenario}/`

| File | Rule | Columns |
| --- | --- | --- |
| `baseline/link-flows.parquet` | `transport_baseline_assignment` | `edge_id`, `layer`, `mode`, `asset_id`, `capacity`, `flow`, `utilisation` |
| `baseline/paths.parquet` | `transport_baseline_assignment` | `origin_id`, `destination_id`, `flow`, `edge_path` (list), `cost` (tfm path table) |
| `baseline/od-journeys.parquet` | `transport_baseline_assignment` | `origin_id`, `destination_id`, `mode`, `flow`, and per trip `time_min`, `length_km`, `fare` (exact where stage data exist), `cost`, each also as `*_total` (× flow) |
| `baseline/purpose-shares.parquet` | `transport_baseline_assignment` | `origin_id`, `destination_id`, `purpose`, `share` |
| `baseline/unassigned.parquet` | `transport_baseline_assignment` | `origin_id`, `destination_id`, `value`, `mode` |
| `baseline/summary.json` | `transport_baseline_assignment` | totals by mode and purpose, bus / metro links at capacity |
| `criticality/failure-sets.parquet` | `transport_failure_sets` | `scenario_id` (`<asset_type>/<asset_id>`), `asset_type`, `asset_id`, `road_class`, `link_ids`, `link_changes`, `baseline_flow`, `batch` |
| `criticality/batches/{batch}/scenario-impacts.parquet` | `transport_criticality_batch` | one row per failure (columns as in `asset-criticality` below, without geometry and ranks) |
| `criticality/batches/{batch}/od-impacts.parquet` | `transport_criticality_batch` | `scenario_id`, `origin_id`, `destination_id`, `mode`, `purpose`, `share`, `affected_flow`, `rerouted_flow`, `isolated_flow`, `baseline_*` per trip, `delta_*_total`, `shifted_to_<mode>`, `shifted` |
| `criticality/asset-criticality.csv` | `transport_criticality_summary` | see the asset criticality columns below |
| `criticality/asset-criticality.geoparquet` | `transport_criticality_summary` | the same, with the road edge, metro segment or station geometry |

The `asset-criticality` files have one row per failed asset:

- `scenario_id`, `asset_type`, `asset_id`, `road_class`, `baseline_flow`;
- `affected_flow`, `rerouted_flow`, `isolated_flow` (trips per peak period;
  isolated trips are counted, not costed);
- `delta_time_min_total`, `delta_length_km_total`, `delta_fare_total`,
  `delta_cost_total` (raw: they can be negative where trips switch mode);
- `shifted_to_bus`, `shifted_to_car`;
- `*_<mode>` and `*_<purpose>` splits;
- `evaluated`, `delta_cost_share`, `delta_time_share`, `rank_cost`,
  `rank_isolated`, `currency`.

### Inter-island: `{data}/out/transport/interisland/`

| File | Rule | Columns |
| --- | --- | --- |
| `link-flows.csv` | `transport_interisland_flows` | input link columns plus `from_island`, `to_island`, `pair`, `passengers`, `freight_t` (annual), `traffic_source` (`observed` or `baseline (…)`), `*_per_day`, `*_capacity_annual`, `*_utilisation` |
| `criticality.csv` | `transport_interisland_criticality` | `asset_type` (`link` or `node`), `asset_id`, `direction` (`<island> -> <island>`), `modes_failed`, and for passengers and `freight_t`: `_affected`, `_shifted` (to scheduled services in the same direction; freight never shifts), `_stranded` |
