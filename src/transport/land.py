"""Shared helpers for the land transport workflow scripts.

Naming conventions for the multimodal network (transport_flow_model needs
unique string node and link ids):

Nodes
    road/<node_id>                      road junction
    zone/<zone_id>/car                  car trips start and end here
    zone/<zone_id>/pt                   bus, metro (and walk) trips start and end here
    bus_stop/<stop_id>                  physical bus stop (walk network side)
    bus/<route_id>/<direction>/<stop_id>  a route's call at a stop (in-vehicle side)
    metro_station/<station_id>          physical station (walk network side)
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
    walk_access     walk/<zone_id>/<stop node>/in|out
    walk_transfer   walk/transfer/<from node>/<to node>
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
)

MODES = ("car", "bus", "metro", "walk")


def read_config(path: str | Path) -> dict:
    """Return the ``transport_land`` section of config/transport/transport.yaml."""
    with open(path) as fh:
        return yaml.safe_load(fh)["transport_land"]


def h3_resolution(zoning: str) -> int:
    """'h3-r8' -> 8."""
    match = re.fullmatch(r"h3-r(\d+)", zoning)
    if not match:
        raise ValueError(f"Unsupported zoning {zoning!r}; expected e.g. 'h3-r8'")
    return int(match.group(1))


def zone_node(zone_id: str, kind: str) -> str:
    if kind not in ("car", "pt"):
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
