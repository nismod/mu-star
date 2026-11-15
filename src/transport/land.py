"""Shared helpers for the land transport workflow scripts.

Naming conventions for the multimodal network (transport_flow_model needs
unique string node and link ids):

Nodes
    road/<node_id>                      road junction
    zone/<zone_id>/car                  car trips start and end here
    zone/<zone_id>/bus                  bus trips start and end here
    zone/<zone_id>/metro                metro trips start and end here
    bus_stop/<stop_id>/dep|arr          bus stop: departure side (walk in, board)
                                        and arrival side (alight, walk out)
    bus/<route_id>/<direction>/<stop_id>  a route's call at a stop (in-vehicle side)
    metro_station/<station_id>/dep|arr  metro station, split the same way
    metro/<direction>/<station_id>      the line's call at a station (in-vehicle side)

Links (``layer`` column)
    road            road/<edge_id>/ab or road/<edge_id>/ba
    car_connector   connector/car/<zone_id>/<node_id>/in|out
    bus             bus/<route_id>/<direction>/<seq>
    bus_board       board/bus/<route_id>/<direction>/<stop_id>
    bus_alight      alight/bus/<route_id>/<direction>/<stop_id>
    metro           metro/<direction>/<seq>
    metro_board     board/metro/<direction>/<station_id>
    metro_alight    alight/metro/<direction>/<station_id>
    walk_access     walk/<zone_id>/<stop or station>/in (to dep) | out (from arr)
    interchange     interchange/<stop or station>: arr -> dep, to change route
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

LINK_COLUMNS = (
    "edge_id",
    "edge_from",
    "edge_to",
    "layer",
    "mode",
    "asset_id",  # physical asset that can fail (road edge id / metro segment id), else null
    "road_edge_ids",  # bus links: list of road edge ids the segment runs over
    "length_km",
    "time_min",  # perceived minutes (in-vehicle + weighted wait/walk)
    "fare",
    "cost",
    "capacity",  # persons per modelled period; inf = unconstrained
    # For exact stage fares (transport.fares), null elsewhere:
    "route_key",  # bus links: <route_id>/<direction>
    "stage_from",  # bus links: fare stage at the segment's first stop
    "stage_to",  # bus links: fare stage at the segment's last stop
    "station_from",  # metro links
    "station_to",  # metro links
)

MODES = ("car", "bus", "metro", "walk")
#: Modes assigned to the network; walk and cycle trips are counted only.
ASSIGNED_MODES = ("car", "bus", "metro")
ACTIVE_MODES = ("walk", "cycle")


#: Top-level config sections a scenario may override. Scenarios apply from
#: the multimodal network onwards; zones, demand estimation and road
#: networks are shared by all scenarios.
SCENARIO_SECTIONS = ("mode_split", "period", "cost", "bus", "metro", "criticality", "school")
#: Settings inside SCENARIO_SECTIONS that scenarios may not change: the bus
#: detours (transport_bus_detours) are computed once per island with them.
SCENARIO_FIXED = ("criticality.bus_detour", "bus.speed_factor")


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base``.

    Keys may be dotted paths (``"cost.per_minute": 2.0``).
    """
    out = dict(base)
    for key, value in override.items():
        if "." in key:
            head, rest = key.split(".", 1)
            value, key = {rest: value}, head
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _dotted_keys(overrides: dict, prefix: str = "") -> list[str]:
    """Every overridden setting as a dotted path (``{"a": {"b": 1}, "c.d": 2}`` -> ``["a.b", "c.d"]``)."""
    keys = []
    for key, value in overrides.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            keys += _dotted_keys(value, path + ".")
        else:
            keys.append(path)
    return keys


def read_config(path: str | Path, island: str | None = None, scenario: str | None = None) -> dict:
    """Return the ``transport_land`` section of config/transport/transport.yaml.

    With ``island``, that island's ``overrides`` are merged in (e.g. Rodrigues
    mode shares). With ``scenario``, the overrides in ``scenarios.<name>`` are
    merged in last (sensitivity runs; only SCENARIO_SECTIONS may change).
    """
    with open(path) as fh:
        cfg = yaml.safe_load(fh)["transport_land"]
    if island:
        cfg = deep_merge(cfg, cfg["islands"][island].get("overrides") or {})
    if scenario:
        scenarios = cfg.get("scenarios") or {}
        if scenario not in scenarios:
            raise ValueError(f"Unknown scenario {scenario!r}; defined: {sorted(scenarios)}")
        overrides = scenarios[scenario] or {}
        bad = {key.split(".")[0] for key in overrides} - set(SCENARIO_SECTIONS)
        if bad:
            raise ValueError(f"Scenario {scenario!r} overrides {sorted(bad)}; allowed: {SCENARIO_SECTIONS}")
        fixed = sorted(
            key
            for key in _dotted_keys(overrides)
            for setting in SCENARIO_FIXED
            if key == setting or key.startswith(setting + ".") or setting.startswith(key + ".")
        )
        if fixed:
            raise ValueError(
                f"Scenario {scenario!r} overrides {fixed}, but {list(SCENARIO_FIXED)} are the same for all "
                "scenarios (bus detours are computed once per island); change them in the base config instead"
            )
        cfg = deep_merge(cfg, overrides)
    return cfg


def h3_resolution(zoning: str) -> int:
    """'h3-r8' -> 8."""
    match = re.fullmatch(r"h3-r(\d+)", zoning)
    if not match:
        raise ValueError(f"Unsupported zoning {zoning!r}; expected e.g. 'h3-r8'")
    return int(match.group(1))


def zone_node(zone_id: str, kind: str) -> str:
    if kind not in ("car", "bus", "metro", "school_bus"):
        raise ValueError(kind)
    return f"zone/{zone_id}/{kind}"


def read_table(path: str | Path, **kwargs) -> pd.DataFrame:
    """Read a table by extension (csv, xlsx, parquet, or a vector format)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, **kwargs)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, **kwargs)
    if suffix == ".parquet":
        return pd.read_parquet(path, **kwargs)
    import geopandas

    if suffix == ".geoparquet":
        return geopandas.read_parquet(path, **kwargs)
    return geopandas.read_file(path, **kwargs)


def peak_capacity(headway_min: pd.Series | float, vehicle_capacity: pd.Series | float, period_hours: float):
    """Persons per modelled period that a service can carry."""
    return (60.0 / headway_min) * period_hours * vehicle_capacity


def require_columns(df: pd.DataFrame, columns, name: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")
