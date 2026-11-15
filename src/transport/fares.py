"""Exact stage fares on assigned paths (for reporting).

Routing uses a link-additive approximation (a boarding charge plus a charge
per km, ``cost.fare``), because stage fares are not additive over links.
This module recomputes the fare of each path from stage data where they
exist:

- **Bus**: each *leg* (board, ride one route, alight) is priced by the
  number of fare stages travelled, ``|stage at alighting - stage at
  boarding| + 1``, using the statutory schedule (``cost.stage_fares.bus``).
  Stages come from ``fare_stage`` per stop in ``bus_route_stops.csv``,
  carried on the bus links as ``stage_from`` / ``stage_to``.
- **Metro**: each *journey* (from the first boarding to the last alighting,
  including a change between lines at a station) is priced from the
  station-to-station stage matrix, then the stage schedule
  (``cost.stage_fares.metro``). Stations are carried on the metro links as
  ``station_from`` / ``station_to``.

Where stage data are missing for a leg or journey, its approximate fare (the
sum of the link fares) is used instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

# Layers that can sit inside one metro journey (between two metro rides).
METRO_JOURNEY_LAYERS = {"metro", "metro_board", "metro_alight", "interchange"}


def bus_fare(stages: float, schedule: Sequence[Mapping]) -> float:
    """Fare for a number of stages from a schedule of {max_stages, fare} rows."""
    if not np.isfinite(stages):
        return np.nan
    for row in sorted(schedule, key=lambda r: r["max_stages"]):
        if stages <= row["max_stages"]:
            return float(row["fare"])
    return float(max(schedule, key=lambda r: r["max_stages"])["fare"])


def path_fares(
    paths: pd.DataFrame,
    links: pd.DataFrame,
    bus_schedule: Sequence[Mapping] | None,
    metro_stage_fares: Mapping | None,
    metro_stages: pd.DataFrame | None,
) -> pd.Series:
    """Fare per path (index-aligned with ``paths``).

    Parameters
    ----------
    paths
        Path table with ``edge_path`` (list of link ids).
    links
        Link table with edge_id, layer, fare (approximate, per link), and for
        bus links ``route_key``, ``stage_from``, ``stage_to``; for metro links
        ``station_from``, ``station_to``. Missing columns mean no stage data.
    bus_schedule
        ``cost.stage_fares.bus``: list of {max_stages, fare}.
    metro_stage_fares
        ``cost.stage_fares.metro``: {stage: fare}.
    metro_stages
        from_station, to_station, stage (the operator's stage matrix), or None.
    """
    columns = ["edge_id", "layer", "fare"]
    optional = ["route_key", "stage_from", "stage_to", "station_from", "station_to"]
    lookup = links[[c for c in columns + optional if c in links.columns]].set_index("edge_id")
    for column in optional:
        if column not in lookup:
            lookup[column] = np.nan

    rows = paths[["edge_path"]].explode("edge_path").rename(columns={"edge_path": "edge_id"})
    rows = rows.reset_index(names="path").join(lookup, on="edge_id")
    rows["fare"] = rows["fare"].fillna(0.0)
    fares = pd.Series(0.0, index=paths.index)

    # --- Bus legs: a new leg starts at each bus boarding link.
    rows["bus_leg"] = (rows["layer"] == "bus_board").groupby(rows["path"]).cumsum()
    bus_rows = rows[rows["layer"].isin(["bus_board", "bus"])]
    if len(bus_rows):
        legs = bus_rows.groupby(["path", "bus_leg"]).agg(
            approx=("fare", "sum"),
            first_stage=("stage_from", lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan),
            last_stage=("stage_to", lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan),
        )
        stages = (legs["last_stage"] - legs["first_stage"]).abs() + 1
        exact = stages.map(lambda n: bus_fare(n, bus_schedule)) if bus_schedule else pd.Series(np.nan, legs.index)
        legs["fare"] = exact.fillna(legs["approx"])
        fares = fares.add(legs.groupby(level="path")["fare"].sum(), fill_value=0.0)

    # --- Metro journeys: contiguous runs of metro-related links.
    outside = ~rows["layer"].isin(METRO_JOURNEY_LAYERS)
    rows["metro_block"] = outside.groupby(rows["path"]).cumsum()
    metro_rows = rows[rows["layer"].isin(["metro_board", "metro"])]
    if len(metro_rows):
        journeys = metro_rows.groupby(["path", "metro_block"]).agg(
            approx=("fare", "sum"),
            start=("station_from", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
            end=("station_to", lambda s: s.dropna().iloc[-1] if s.notna().any() else None),
        )
        exact = pd.Series(np.nan, index=journeys.index)
        if metro_stages is not None and metro_stage_fares:
            stage_of = {
                (str(a), str(b)): int(c)
                for a, b, c in metro_stages[["from_station", "to_station", "stage"]].itertuples(index=False)
            }
            fare_of = {int(k): float(v) for k, v in metro_stage_fares.items()}
            exact = pd.Series(
                [fare_of.get(stage_of.get((str(a), str(b)), -1), np.nan) for a, b in zip(journeys.start, journeys.end)],
                index=journeys.index,
            )
        journeys["fare"] = exact.fillna(journeys["approx"])
        fares = fares.add(journeys.groupby(level="path")["fare"].sum(), fill_value=0.0)
    return fares
