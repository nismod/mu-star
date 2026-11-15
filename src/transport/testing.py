"""Synthetic land transport inputs for the end-to-end test.

``write_inputs(directory)`` writes a small, deterministic study area in the
provided-input schemas (docs/src/transport-data.md):

- roads: a 5 x 5 grid of two-way roads, 1 km apart (EPSG:32740); the
  middle row, and the vertical edges from it to the row above, are
  ``primary``, the rest ``residential``;
- one bus route ``R1`` along the middle row (stops s0-s4), both directions,
  3 minutes between stops, fare stages 1-5;
- a three-station metro line (``main``: m0, m2, m4), 200 m east of the
  western column, with a station-to-station fare stage matrix;
- 25 zones (one per grid node, 100 m north-east of it), districts "11"
  (west, x < 2) and "12" (east);
- a mock census CA27 table for the two districts;
- three schools and school-age children per zone.

``write_config(path)`` writes a copy of config/transport/transport.yaml with
the overrides the toy needs (two districts, two batches, shorter metro walk,
fixed mode shares).

``script_chain(config, inputs, outputs)`` lists the workflow scripts with
their arguments, in rule order, as the Snakemake rules call them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import geopandas
import pandas as pd
import yaml
from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "transport" / "transport.yaml"
CRS = "EPSG:32740"
X0, Y0, SPACING, N = 550_000.0, 7_760_000.0, 1_000.0, 5

# Employed residents and jobs per zone z<i>_<j>, in zone order (i, then j).
EMPLOYED = [525, 355, 253, 137, 187, 424, 351, 585, 416, 379, 238, 435, 297]
EMPLOYED += [377, 482, 523, 144, 111, 140, 340, 301, 102, 104, 362, 228]
JOBS = [509, 215, 32, 13, 650, 730, 485, 583, 434, 748, 652, 2, 685]
JOBS += [26, 583, 140, 690, 433, 239, 338, 22, 99, 536, 517, 492]

#: Overrides applied to the repository config for the toy (dotted keys).
CONFIG_OVERRIDES = {
    "districts": {"11": "West", "12": "East"},
    "criticality.n_batches": 2,
    "connectors.metro_walk_max_km": 1.0,
    "mode_split.method": "shares",
}


def _xy(i, j):
    return X0 + i * SPACING, Y0 + j * SPACING


def _roads():
    rows = []
    for i in range(N):
        for j in range(N):
            for di, dj in ((1, 0), (0, 1)):
                a, b = (i, j), (i + di, j + dj)
                if b[0] >= N or b[1] >= N:
                    continue
                rows.append(
                    {
                        "edge_id": f"en{a[0]}_{a[1]}-n{b[0]}_{b[1]}",
                        "from_node": f"n{a[0]}_{a[1]}",
                        "to_node": f"n{b[0]}_{b[1]}",
                        "road_class": "primary" if j == 2 else "residential",
                        "oneway": False,
                        "geometry": LineString([_xy(*a), _xy(*b)]),
                    }
                )
    return geopandas.GeoDataFrame(rows, crs=CRS)


def write_inputs(directory: str | Path) -> Path:
    """Write the synthetic inputs into ``directory`` (created if needed)."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)

    _roads().to_file(out / "roads.gpkg", layer="edges", driver="GPKG")

    # Bus: one route along the middle row (y = 2), both directions.
    stops = [f"s{i}" for i in range(N)]
    geopandas.GeoDataFrame({"stop_id": stops, "geometry": [Point(_xy(i, 2)) for i in range(N)]}, crs=CRS).to_file(
        out / "bus_stops.gpkg", driver="GPKG"
    )
    geopandas.GeoDataFrame(
        {"route_id": ["R1"], "frequency_per_hour": [6], "geometry": [None]}, geometry="geometry", crs=CRS
    ).to_file(out / "bus_routes.gpkg", driver="GPKG")
    route_stops = []
    for direction, order in ((0, stops), (1, stops[::-1])):
        for seq, stop in enumerate(order):
            route_stops.append(
                {
                    "route_id": "R1",
                    "direction": direction,
                    "stop_sequence": seq,
                    "stop_id": stop,
                    "time_to_next_min": 3.0 if seq < len(order) - 1 else None,
                    "fare_stage": seq + 1,
                }
            )
    pd.DataFrame(route_stops).to_csv(out / "bus_route_stops.csv", index=False)

    # Metro: three stations on a north-south line near the western column.
    stations = ["m0", "m2", "m4"]
    names = ["Place d'Armes", "Rose Hill Central", "Curepipe Central"]
    geopandas.GeoDataFrame(
        {"station_id": stations, "name": names, "geometry": [Point(X0 + 200, Y0 + j * SPACING) for j in (0, 2, 4)]},
        crs=CRS,
    ).to_file(out / "metro_stations.gpkg", driver="GPKG")
    geopandas.GeoDataFrame(
        {"line_id": ["main"], "geometry": [LineString([(X0 + 200, Y0), (X0 + 200, Y0 + 4 * SPACING)])]}, crs=CRS
    ).to_file(out / "metro_lines.gpkg", driver="GPKG")
    pd.DataFrame({"station_id": stations, "seq": range(3), "line_id": "main"}).to_csv(
        out / "metro_line_stations.csv", index=False
    )
    stage = {("m0", "m4"): 2, ("m4", "m0"): 2}
    pd.DataFrame(
        [{"from_station": a, "to_station": b, "stage": stage.get((a, b), 1)} for a in stations for b in stations]
    ).to_csv(out / "metro_fare_stages.csv", index=False)

    # Zones: one per grid node, offset 100 m so that no centroid sits on a node.
    zone_ids = [f"z{i}_{j}" for i in range(N) for j in range(N)]
    geopandas.GeoDataFrame(
        {
            "zone_id": zone_ids,
            "population": 1000.0,
            "employed_residents": [float(v) for v in EMPLOYED],
            "jobs": [float(v) for v in JOBS],
            "geometry": [Point(X0 + i * SPACING + 100, Y0 + j * SPACING + 100) for i in range(N) for j in range(N)],
            "district": ["11" if i < 2 else "12" for i in range(N) for _ in range(N)],
        },
        crs=CRS,
    ).to_parquet(out / "zones.geoparquet")

    # Mock CA27: residence district x workplace district, two districts.
    # The "Port Louis" rows have no district workplaces (dropped as non-toy).
    ca27 = []
    for residence, west, east in (("West", 2000.0, 800.0), ("East", 1500.0, 3000.0), ("Port Louis", 0.0, 0.0)):
        for sex, factor in (("Both sexes", 1.0), ("Male", 0.5)):
            row = {
                "residence_district": residence,
                "sex": sex,
                "age_group": "All ages",
                "West": west * factor,
                "East": east * factor,
                "Not stated": 5.0 * factor,
                "Rodrigues and Outer Islands": 3.0 * factor,
                "Outside Mauritius": 10.0 * factor,
            }
            row["Total"] = sum(v for k, v in row.items() if isinstance(v, float))
            ca27.append(row)
    pd.DataFrame(ca27).to_csv(out / "ca27.csv", index=False)

    # Schools and children.
    geopandas.GeoDataFrame(
        {
            "school_id": ["p1", "p2", "s1"],
            "level": ["primary", "primary", "secondary"],
            "enrolment": [300, 200, 600],
            "geometry": [Point(X0 + 100, Y0 + 100), Point(X0 + 2100, Y0 + 2100), Point(X0 + 4100, Y0 + 4100)],
        },
        crs=CRS,
    ).to_file(out / "schools.gpkg", driver="GPKG")
    pd.DataFrame(
        [
            {"h3_index": z, "level": level, "children": n}
            for z in zone_ids
            for level, n in (("primary", 60.0), ("secondary", 40.0))
        ]
    ).to_parquet(out / "children.parquet", index=False)
    return out


def _set(cfg: dict, dotted: str, value) -> None:
    *parents, last = dotted.split(".")
    for key in parents:
        cfg = cfg[key]
    cfg[last] = value


def write_config(path: str | Path, base: str | Path = CONFIG, overrides: dict | None = None) -> Path:
    """Write the repository config with the toy overrides (``CONFIG_OVERRIDES``, then ``overrides``)."""
    with open(base) as fh:
        cfg = yaml.safe_load(fh)
    for key, value in {**CONFIG_OVERRIDES, **(overrides or {})}.items():
        _set(cfg["transport_land"], key, value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    return path


def _cmd(script: Path, *flags: str, **options) -> list[str]:
    """[script, *flags, --option value, ...]; underscores in option names become dashes."""
    args = [str(script), *flags]
    for name, value in options.items():
        args += ["--" + name.replace("_", "-"), str(value)]
    return args


def script_chain(config: str | Path, inputs: str | Path, outputs: str | Path, n_batches: int = 2) -> list[list[str]]:
    """Script paths and arguments, from the road network to the criticality summary.

    Output file names are short; see the Snakemake rules for the real layout.
    """
    i, o = Path(inputs), Path(outputs)
    p0, p2 = REPO / "workflow" / "0-preprocess", REPO / "workflow" / "2-losses"
    common = {"config": config}
    scenario = {"config": config, "island": "mauritius", "scenario": "central"}
    zoning = {"config": config, "island": "mauritius", "zoning": "h3-r8"}
    batches = [f"batch-{b:04d}" for b in range(n_batches)]
    return [
        _cmd(
            p0 / "transport_road_network.py",
            **common,
            roads=i / "roads.gpkg",
            output_edges=o / "road-edges.gpq",
            output_nodes=o / "road-nodes.gpq",
        ),
        _cmd(
            p0 / "transport_bus_network.py",
            **common,
            stops=i / "bus_stops.gpkg",
            routes=i / "bus_routes.gpkg",
            route_stops=i / "bus_route_stops.csv",
            road_edges=o / "road-edges.gpq",
            road_nodes=o / "road-nodes.gpq",
            output_stops=o / "bus-stops.gpq",
            output_segments=o / "bus-segments.gpq",
        ),
        _cmd(
            p0 / "transport_metro_network.py",
            **common,
            stations=i / "metro_stations.gpkg",
            lines=i / "metro_lines.gpkg",
            line_stations=i / "metro_line_stations.csv",
            output_stations=o / "metro-stations.gpq",
            output_segments=o / "metro-segments.gpq",
        ),
        _cmd(
            p0 / "transport_bus_detours.py",
            **common,
            island="mauritius",
            road_edges=o / "road-edges.gpq",
            bus_stops=o / "bus-stops.gpq",
            bus_segments=o / "bus-segments.gpq",
            output=o / "detours.parquet",
        ),
        _cmd(
            p0 / "transport_multimodal_network.py",
            **scenario,
            road_edges=o / "road-edges.gpq",
            road_nodes=o / "road-nodes.gpq",
            bus_stops=o / "bus-stops.gpq",
            bus_segments=o / "bus-segments.gpq",
            bus_detours=o / "detours.parquet",
            metro_stations=o / "metro-stations.gpq",
            metro_segments=o / "metro-segments.gpq",
            zones=i / "zones.geoparquet",
            output_links=o / "links.parquet",
            output_nodes=o / "nodes.gpq",
            output_failure_units=o / "units.parquet",
        ),
        _cmd(
            p0 / "transport_car_network.py",
            **common,
            island="mauritius",
            road_edges=o / "road-edges.gpq",
            road_nodes=o / "road-nodes.gpq",
            zones=i / "zones.geoparquet",
            output_links=o / "car-links.parquet",
            output_no_access=o / "zones-without-car-access.csv",
        ),
        _cmd(
            p0 / "transport_road_skim.py",
            **common,
            links=o / "car-links.parquet",
            zones=i / "zones.geoparquet",
            output=o / "skim.parquet",
        ),
        _cmd(
            p0 / "transport_census_district_od.py",
            **common,
            table=i / "ca27.csv",
            output=o / "ca27.parquet",
            output_summary=o / "ca27.json",
        ),
        _cmd(
            p0 / "transport_radiation_od.py",
            **zoning,
            zones=i / "zones.geoparquet",
            skim=o / "skim.parquet",
            district_od=o / "ca27.parquet",
            output=o / "od.parquet",
            output_summary=o / "od.json",
            output_comparison=o / "district-cmp.csv",
            output_calibration=o / "calibration.csv",
        ),
        _cmd(
            p0 / "transport_school_od.py",
            **zoning,
            zones=i / "zones.geoparquet",
            children=i / "children.parquet",
            schools=i / "schools.gpkg",
            skim=o / "skim.parquet",
            output=o / "school-od.parquet",
            output_summary=o / "school-od.json",
        ),
        _cmd(
            p0 / "transport_mode_split.py",
            **scenario,
            od=o / "od.parquet",
            school_od=o / "school-od.parquet",
            skim=o / "skim.parquet",
            nodes=o / "nodes.gpq",
            output=o / "demand.parquet",
            output_summary=o / "demand.json",
        ),
        _cmd(
            p2 / "transport_baseline_assignment.py",
            **scenario,
            links=o / "links.parquet",
            demand=o / "demand.parquet",
            metro_fare_stages=i / "metro_fare_stages.csv",
            output_link_flows=o / "lf.parquet",
            output_paths=o / "paths.parquet",
            output_od_journeys=o / "odj.parquet",
            output_unassigned=o / "un.parquet",
            output_summary=o / "base.json",
            output_purpose_shares=o / "purpose.parquet",
        ),
        _cmd(
            p2 / "transport_failure_sets.py",
            **common,
            failure_units=o / "units.parquet",
            link_flows=o / "lf.parquet",
            output=o / "fs.parquet",
        ),
        *(
            _cmd(
                p2 / "transport_criticality_batch.py",
                **scenario,
                batch=b,
                links=o / "links.parquet",
                paths=o / "paths.parquet",
                link_flows=o / "lf.parquet",
                failure_sets=o / "fs.parquet",
                purpose_shares=o / "purpose.parquet",
                metro_fare_stages=i / "metro_fare_stages.csv",
                output_scenario_impacts=o / f"si-{b}.parquet",
                output_od_impacts=o / f"oi-{b}.parquet",
            )
            for b in batches
        ),
        _cmd(
            p2 / "transport_criticality_summary.py",
            *[str(o / f"si-{b}.parquet") for b in batches],
            **common,
            failure_sets=o / "fs.parquet",
            road_edges=o / "road-edges.gpq",
            metro_segments=o / "metro-segments.gpq",
            metro_stations=o / "metro-stations.gpq",
            baseline_summary=o / "base.json",
            output_table=o / "crit.csv",
            output_spatial=o / "crit.gpq",
        ),
    ]


def run_chain(config, inputs, outputs, n_batches: int = 2, python: str = sys.executable) -> None:
    """Run ``script_chain`` in order, with ``src`` on PYTHONPATH; raise with the script's output on failure."""
    Path(outputs).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO / "src"), os.environ.get("PYTHONPATH")]))}
    for cmd in script_chain(config, inputs, outputs, n_batches):
        done = subprocess.run([python, *cmd], cwd=REPO, env=env, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f"{Path(cmd[0]).name} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}")
