"""Unmet demand when one component fails (N-1 criticality): a power flow for the meshed network, connectivity below it.

``max_radial_line_voltage_kv`` (35 kV by default) splits the network. The lines above it are run meshed: each
substation, transmission line and power plant is failed in turn and the network is solved as a linear power flow
(PyPSA, HiGHS) at peak and at average demand, first with nothing failed. The lines at or below it are distribution
lines, run radially: each is failed in turn from the connectivity of the network (``energy.connectivity``), which for
one failure without switching gives what a power flow would. The power flow:

- Buses are the points where the lines above that voltage meet, at the voltage the network gives them. A
  substation's demand and plants sit on the transmission bus at the substation. An island without such
  lines (Rodrigues) is one bus with all its plants and demand.
- A line's rating is its number of circuits times the rating of one circuit of its PyPSA line type at
  the line's voltage; its reactance is the type's reactance per km times its length, divided by its
  circuits. The type is chosen by the voltage the line was built for. A line circuits table (on
  Mauritius, ``ceb_line_circuits.csv``) gives each line's route, design voltage and circuits; without
  one, each line has one circuit built for the voltage it runs at. The segments of one route fail
  together; without a table, a route is the run of segments between two substations or junctions.
- A supply point's demand is its share of its island's peak or average demand (``energy.demand``).
- Demand that cannot be supplied is shed. A part of the network cut off from the rest keeps supply
  only if it has a plant of an ``island_carriers`` type (thermal, hydro): solar and wind farms
  disconnect when the grid is lost.

Unmet demand is the demand the network cannot supply: demand minus supply, at each supply point. The
unmet demand of a failure is the rise in the total demand shed over that with nothing failed. At each
supply point it is the rise at that point; when demand is shed with nothing failed, a failure can move
that shedding from one point to another, so the points can add up to more than the total. Each failure
gives two quantities: the unmet demand at peak demand (MW), and the unmet energy per day of outage (MWh),
which is the unmet demand at average demand over 24 hours. The per-day value is exact when whole areas
lose supply and too low for shortfalls that occur only near peak. Each failure that leaves demand unmet
at peak is solved again without line ratings, to give the part due to the ratings. A distribution line's
failure starts from the supply that the power flow gives each area with nothing failed.

``build_criticality`` writes one table, with a row for each component and the ``method`` that gave it. The power
flow models lines at one voltage and no transformers, so ``transmission_lines`` stops with an error when the setting
puts lines of the estimated distribution network, or lines of more than one voltage, into it.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

from energy.connectivity import distribution_failures
from energy.demand import DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV

logger = logging.getLogger(__name__)

DEFAULT_LINE_TYPES = {66: "149-AL1/24-ST1A 110.0", 132: "243-AL1/39-ST1A 110.0"}
# Plants that keep supply going in a part of the network cut off from the rest. Solar and wind farms disconnect when
# the grid is lost.
DEFAULT_ISLAND_CARRIERS = ("thermal", "hydro")
SHED_COST = 10_000.0  # per MWh; plants cost nothing, so demand is shed only when it cannot be supplied
UNLIMITED_MVA = 1e6
MIN_REACTANCE_OHM = 1e-3  # for line segments of almost no length
SAME_POINT_M = 50.0  # the largest distance between a substation and its transmission bus
TOLERANCE_MW = 1e-6
HOURS_PER_DAY = 24
LEVELS = ("peak", "average")
QUANTITIES = ("unmet_mwh_per_day", "unmet_peak_mw")
ASSET_TYPES = ("substation", "line", "plant", "distribution_line")
# The build's sources for the lines of the estimated distribution network; its anchors end in "_anchor".
ESTIMATED_SOURCES = ("osm", "precomputed")


@dataclass(frozen=True)
class TransmissionModel:
    """The transmission network solved for each failure.

    ``buses``: index bus; ``island`` and ``v_nom`` (kV). ``lines``: index line_id; ``bus0``, ``bus1``,
    ``route``, ``v_nom`` and ``built_for_kv`` (kV), ``circuits``, ``s_nom`` (MVA), ``x`` and ``r`` (ohm).
    ``plants``: index generator; ``bus``, ``p_nom`` (MW), ``carrier``. ``supply_points``: index supply
    point (network bus_id); ``bus`` and ``island``.
    """

    buses: pd.DataFrame
    lines: pd.DataFrame
    plants: pd.DataFrame
    supply_points: pd.DataFrame


@dataclass(frozen=True)
class CriticalityOutputs:
    criticality: Path
    by_area: Path
    line_ratings: Path
    metadata: Path


def read_line_circuits(path: Path) -> pd.DataFrame:
    """Read a line circuits table: ``line_id``, ``route``, ``built_for_kv`` and ``circuits``, indexed by line_id.

    An optional ``ends`` column names each route.
    """
    table = pd.read_csv(path, comment="#")
    missing = {"line_id", "route", "built_for_kv", "circuits"} - set(table.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if table["line_id"].duplicated().any():
        raise ValueError(f"{path}: line_id appears more than once")
    return table.set_index("line_id")


def is_transmission_line(network: pypsa.Network, max_radial_line_voltage_kv: float) -> pd.Series:
    """Return, for each line of ``network``, whether it is a transmission line: above ``max_radial_line_voltage_kv``
    at either end. The others are distribution lines."""
    voltage = network.buses["v_nom"].astype(float)
    return network.lines["bus0"].map(voltage).gt(max_radial_line_voltage_kv) | network.lines["bus1"].map(voltage).gt(
        max_radial_line_voltage_kv
    )


def transmission_lines(network: pypsa.Network, max_radial_line_voltage_kv: float) -> pd.DataFrame:
    """Return the transmission lines of ``network``, those above ``max_radial_line_voltage_kv``, with their voltage
    (``v_nom``, kV): the lines of the power flow.

    The power flow models lines at one voltage and no transformers, and is meant for the meshed transmission network.
    Raises ValueError when the setting, set below the voltage of the distribution lines, puts lines of the estimated
    distribution network, or lines of more than one voltage, into it.
    """
    lines = network.lines[is_transmission_line(network, max_radial_line_voltage_kv)]
    voltage0 = lines["bus0"].map(network.buses["v_nom"]).astype(float)
    voltage1 = lines["bus1"].map(network.buses["v_nom"]).astype(float)
    setting = f"max_radial_line_voltage_kv ({max_radial_line_voltage_kv:g} kV)"
    if not np.allclose(voltage0, voltage1):
        raise ValueError(f"a line above {setting} joins buses of different voltages")
    source = lines["source"].astype(str) if "source" in lines else pd.Series("", index=lines.index)
    estimated = int((source.isin(ESTIMATED_SOURCES) | source.str.endswith("_anchor")).sum())
    if estimated:
        raise ValueError(
            f"{setting} puts {estimated:,} of the estimated distribution network's lines into the power flow, which "
            "would run them meshed, with every line in service. Set it between the voltage of the distribution lines "
            "and that of the transmission lines."
        )
    voltages = sorted(voltage0.unique())
    if len(voltages) > 1:
        raise ValueError(
            f"lines at {' and '.join(f'{voltage:g}' for voltage in voltages)} kV are above {setting}, but the power "
            "flow models lines at one voltage, not the transformers between voltages"
        )
    return lines.assign(v_nom=voltage0)


def _distance_m(lon0: float, lat0: float, lon1: np.ndarray, lat1: np.ndarray) -> np.ndarray:
    dx = (lon1 - lon0) * 111_320.0 * math.cos(math.radians(lat0))
    dy = (lat1 - lat0) * 110_574.0
    return np.hypot(dx, dy)


def _bus_at_same_point(buses: pd.DataFrame, bus_id: str, candidates: list[str]) -> str:
    """Return the bus of ``candidates`` at the point of ``bus_id``."""
    distances = _distance_m(
        float(buses.at[bus_id, "x"]),
        float(buses.at[bus_id, "y"]),
        buses.loc[candidates, "x"].to_numpy(dtype=float),
        buses.loc[candidates, "y"].to_numpy(dtype=float),
    )
    nearest = int(np.argmin(distances))
    if distances[nearest] > SAME_POINT_M:
        raise ValueError(
            f"{bus_id}: no transmission bus within {SAME_POINT_M:.0f} m (nearest {distances[nearest]:.0f} m)"
        )
    return candidates[nearest]


def _routes(lines: pd.DataFrame, ends: set[str]) -> pd.Series:
    """Return the route of each line: the run of segments between buses in ``ends`` or where lines branch.

    Segments join at a bus that only two lines meet and that is not in ``ends``. A route is named after
    its first segment.
    """
    graph = nx.MultiGraph()
    graph.add_edges_from(
        (bus0, bus1, line_id) for line_id, bus0, bus1 in zip(lines.index, lines["bus0"], lines["bus1"])
    )
    segments = nx.Graph()
    segments.add_nodes_from(lines.index)
    for bus in graph.nodes:
        if bus not in ends and graph.degree(bus) == 2:
            segments.add_edge(*(line_id for _, _, line_id in graph.edges(bus, keys=True)))
    route = {line_id: min(part) for part in nx.connected_components(segments) for line_id in part}
    return pd.Series(route).reindex(lines.index)


def transmission_model(
    network: pypsa.Network,
    supply_point_islands: pd.Series,
    *,
    line_circuits: pd.DataFrame | None = None,
    line_types: dict[int, str] | None = None,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
) -> TransmissionModel:
    """Build the transmission model from the lines of ``network`` above ``max_radial_line_voltage_kv``.

    ``supply_point_islands`` maps each supply point (a substation, or a power station on an island
    with no substation) to its island, as in ``service_weights_substations.csv``. ``line_circuits``
    (see ``read_line_circuits``) gives each line's route, design voltage and circuits; without it, each
    line has one circuit built for the voltage it runs at, and a route is the run of segments between
    two substations or junctions. ``line_types`` maps a design voltage (kV) to the PyPSA line type of one
    circuit.
    """
    line_types = {int(kv): name for kv, name in (line_types or DEFAULT_LINE_TYPES).items()}
    buses = network.buses
    meshed = transmission_lines(network, max_radial_line_voltage_kv)
    voltage = meshed["v_nom"].to_numpy()
    line_buses = sorted(set(meshed["bus0"]) | set(meshed["bus1"]))
    model_buses = buses.loc[line_buses, ["region", "v_nom"]].rename(columns={"region": "island"})
    model_buses = model_buses.astype({"island": str, "v_nom": float})
    networked = set(model_buses["island"])

    supply_bus = {}
    for bus_id, island in supply_point_islands.astype(str).items():
        supply_bus[bus_id] = (
            _bus_at_same_point(buses, bus_id, line_buses) if island in networked else f"island::{island}"
        )
    # An island without lines is one bus, at the voltage of its supply points; with no lines on it, the
    # voltage does not enter the power flow.
    island_buses = sorted({bus for bus in supply_bus.values() if bus.startswith("island::")})
    point_voltage = buses.loc[supply_point_islands.index, "v_nom"].astype(float)
    point_voltage = point_voltage.groupby(supply_point_islands.astype(str).to_numpy()).max()
    island_rows = pd.DataFrame({"island": [bus.removeprefix("island::") for bus in island_buses]}, index=island_buses)
    island_rows["v_nom"] = island_rows["island"].map(point_voltage)
    model_buses = pd.concat([model_buses, island_rows])

    plant_bus = {}
    for generator, bus in network.generators["bus"].astype(str).items():
        island = str(buses.at[bus, "region"])
        if bus in supply_bus:
            plant_bus[generator] = supply_bus[bus]
        elif island not in networked:
            plant_bus[generator] = f"island::{island}"
        else:
            raise ValueError(f"{generator}: plant on {bus}, which is not a substation")
    plants = network.generators[["p_nom", "carrier"]].assign(bus=pd.Series(plant_bus))

    if line_circuits is None:
        circuits = pd.DataFrame(
            {
                "route": _routes(meshed, set(supply_bus.values()) | set(plant_bus.values())),
                "built_for_kv": voltage.round().astype(int),
                "circuits": 1,
            },
            index=meshed.index,
        )
    else:
        unlisted = sorted(set(meshed.index) - set(line_circuits.index))
        unknown = sorted(set(line_circuits.index) - set(meshed.index))
        if unlisted or unknown:
            raise ValueError(
                f"line circuits do not match the network: missing {unlisted[:5]}, not in network {unknown[:5]}"
            )
        circuits = line_circuits.reindex(meshed.index)
    count = circuits["circuits"].astype(float).to_numpy()
    if (count < 1).any():
        raise ValueError("every transmission line needs at least one circuit")
    length = meshed["length"].astype(float).to_numpy()
    no_type = sorted(set(circuits["built_for_kv"].astype(int)) - set(line_types))
    if no_type:
        raise ValueError(f"no line type for lines built for {no_type} kV")
    types = network.line_types.loc[circuits["built_for_kv"].astype(int).map(line_types)]
    lines = pd.DataFrame(
        {
            "bus0": meshed["bus0"],
            "bus1": meshed["bus1"],
            "route": circuits["route"].astype(str),
            "v_nom": voltage,
            "built_for_kv": circuits["built_for_kv"].astype(int),
            "circuits": circuits["circuits"].astype(int),
            "s_nom": math.sqrt(3) * voltage * types["i_nom"].to_numpy() * count,
            "x": np.maximum(types["x_per_length"].to_numpy() * length / count, MIN_REACTANCE_OHM),
            "r": types["r_per_length"].to_numpy() * length / count,
        },
        index=meshed.index,
    )
    supply_points = pd.DataFrame(
        {"bus": pd.Series(supply_bus), "island": supply_point_islands.astype(str)}
    ).rename_axis("bus_id")
    return TransmissionModel(model_buses.rename_axis("bus"), lines, plants[["bus", "p_nom", "carrier"]], supply_points)


def island_demand(demand_levels: pd.DataFrame, level: str) -> pd.Series:
    """Return each island's ``level`` demand (MW), from ``demand_levels.csv``."""
    return demand_levels[demand_levels["level"].eq(level)].set_index("island")["demand_mw"].astype(float)


def demand_by_supply_point(substation_weights: pd.DataFrame, demand_levels: pd.DataFrame, level: str) -> pd.Series:
    """Return each supply point's demand (MW): its ``service_weight`` times its island's ``level`` demand."""
    levels = island_demand(demand_levels, level)
    weights = substation_weights.set_index("bus_id")
    missing = sorted(set(weights["island"]) - set(levels.index))
    if missing:
        raise ValueError(f"no {level} demand for {missing}")
    return (weights["service_weight"] * weights["island"].map(levels)).rename("demand_mw")


def unmet_demand(
    model: TransmissionModel,
    demand: pd.Series,
    *,
    lines: tuple[str, ...] = (),
    bus: str | None = None,
    plants: tuple[str, ...] = (),
    line_ratings: bool = True,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> pd.Series:
    """Return the unmet demand at each supply point (MW) with ``lines``, ``bus`` and ``plants`` failed.

    A failed bus takes its lines, plants and demand with it. Demand shed at a bus is split between its
    supply points in proportion to their demand.
    """
    live_lines = model.lines.drop(index=list(lines))
    live_plants = model.plants.drop(index=list(plants))
    buses = model.buses.index
    if bus is not None:
        live_lines = live_lines[live_lines["bus0"].ne(bus) & live_lines["bus1"].ne(bus)]
        live_plants = live_plants[live_plants["bus"].ne(bus)]
        buses = buses.drop(bus)
    graph = nx.Graph()
    graph.add_nodes_from(buses)
    graph.add_edges_from(zip(live_lines["bus0"], live_lines["bus1"]))
    keep = []
    for part in nx.connected_components(graph):
        part_plants = live_plants[live_plants["bus"].isin(part)]
        if part_plants["carrier"].isin(island_carriers).any():
            keep.extend(part_plants.index)
    live_plants = live_plants[live_plants.index.isin(keep)]

    supply = model.supply_points
    load = demand.groupby(supply["bus"].reindex(demand.index)).sum()
    load = load[load.index.isin(buses) & (load > 0)]

    network = pypsa.Network()
    network.madd("Bus", buses, v_nom=model.buses.loc[buses, "v_nom"].to_numpy())
    network.madd(
        "Line",
        live_lines.index,
        bus0=live_lines["bus0"].to_numpy(),
        bus1=live_lines["bus1"].to_numpy(),
        x=live_lines["x"].to_numpy(),
        r=live_lines["r"].to_numpy(),
        s_nom=live_lines["s_nom"].to_numpy() if line_ratings else UNLIMITED_MVA,
        s_max_pu=s_max_pu,
    )
    network.madd(
        "Generator",
        live_plants.index,
        bus=live_plants["bus"].to_numpy(),
        p_nom=live_plants["p_nom"].to_numpy(),
        carrier=live_plants["carrier"].to_numpy(),
    )
    network.madd("Load", "load::" + load.index, bus=load.index.to_numpy(), p_set=load.to_numpy())
    network.madd(
        "Generator",
        "shed::" + load.index,
        bus=load.index.to_numpy(),
        p_nom=load.to_numpy(),
        marginal_cost=SHED_COST,
        carrier="shed",
    )
    status, condition = network.optimize(solver_name="highs", log_to_console=False)
    if status != "ok":
        raise RuntimeError(f"power flow failed: {status}, {condition}")
    shed_names = "shed::" + load.index
    shed = pd.Series(network.generators_t.p[shed_names].iloc[0].to_numpy(), index=load.index)

    points = supply.reindex(demand.index)
    at_bus = demand.groupby(points["bus"]).transform("sum")
    share = (demand / at_bus.where(at_bus > 0)).fillna(0.0)
    unmet = points["bus"].map(shed).fillna(0.0) * share
    if bus is not None:
        unmet[points["bus"].eq(bus)] = demand[points["bus"].eq(bus)]
    return unmet.clip(lower=0.0).rename("unmet_mw")


def bus_names(network: pypsa.Network) -> pd.Series:
    """Return each bus's name: its ``name``, or the id of the asset at it, or the bus id."""
    names = pd.Series(network.buses.index, index=network.buses.index)
    for column in ("asset_id", "name"):  # each fills in where it has a value
        if column in network.buses:
            values = network.buses[column].replace("", np.nan)
            names = values.where(values.notna(), names)
    return names.astype(str)


def failure_assets(
    model: TransmissionModel, network: pypsa.Network, line_circuits: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Return the assets to fail in the power flow: substations, lines (one per route) and plants.

    Columns: ``asset_id``, ``asset_type``, ``name``, ``island``, ``lines``, ``bus`` and ``plants``. A substation
    and a plant take the ``name`` the network gives them, and a line the ``ends`` column of ``line_circuits``;
    without them, their ids.
    """
    line_circuits = line_circuits if line_circuits is not None else pd.DataFrame()
    kind = network.buses["kind"].astype(str)
    names = bus_names(network)
    plant_names = (
        network.generators["name"].replace("", np.nan).dropna()
        if "name" in network.generators
        else pd.Series(dtype=str)
    )
    takes_out_nothing = {"lines": (), "bus": None, "plants": ()}  # what else a failure takes out; set per type below
    rows = []
    for bus_id, point in model.supply_points.iterrows():
        if kind.get(bus_id) == "substation":
            rows.append(
                {
                    **takes_out_nothing,
                    "asset_id": str(network.buses.at[bus_id, "asset_id"]),
                    "asset_type": "substation",
                    "name": names[bus_id],
                    "island": point["island"],
                    "bus": point["bus"],
                }
            )
    ends = line_circuits["ends"] if "ends" in line_circuits else pd.Series(dtype=str)
    for route, block in model.lines.groupby("route", sort=True):
        route_ends = ends.reindex(block.index).dropna()
        name = str(route_ends.iloc[0]) if len(route_ends) else route
        island = model.buses.at[block["bus0"].iloc[0], "island"]
        rows.append(
            {
                **takes_out_nothing,
                "asset_id": route,
                "asset_type": "line",
                "name": name,
                "island": island,
                "lines": tuple(block.index),
            }
        )
    for plant, row in model.plants.iterrows():
        island = model.buses.at[row["bus"], "island"]
        rows.append(
            {
                **takes_out_nothing,
                "asset_id": plant,
                "asset_type": "plant",
                "name": str(plant_names.get(plant, plant)),
                "island": island,
                "plants": (plant,),
            }
        )
    columns = ["asset_id", "asset_type", "name", "island", "lines", "bus", "plants"]
    return pd.DataFrame(rows, columns=columns)


def _failures(
    model: TransmissionModel, assets: pd.DataFrame, demand: pd.Series, settings: dict, *, line_ratings: bool = True
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return the unmet demand (MW) of each failure, at each supply point (rows: failures, columns: supply points) and
    in total, and the unmet demand at each supply point with nothing failed.

    A failure's total is the rise in the network's total unmet demand, and its value at a supply point the rise at
    that point.
    """
    nothing_failed = unmet_demand(model, demand, line_ratings=line_ratings, **settings)
    at_points, totals = {}, {}
    for asset in assets.itertuples(index=False):
        failure = {"lines": asset.lines, "bus": asset.bus, "plants": asset.plants}
        unmet = unmet_demand(model, demand, **failure, line_ratings=line_ratings, **settings)
        at_points[asset.asset_id] = (unmet - nothing_failed).clip(lower=0.0)
        totals[asset.asset_id] = max(0.0, float(unmet.sum() - nothing_failed.sum()))
    return pd.DataFrame(at_points).T, pd.Series(totals, dtype=float), nothing_failed


def _by_area(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return a failures-by-supply-points table as rows of ``asset_id``, ``area_bus_id`` and ``name``."""
    frame = frame.rename_axis(index="asset_id", columns="area_bus_id").reset_index()
    return frame.melt(id_vars="asset_id", var_name="area_bus_id", value_name=name)


def _leaves_demand_unmet(frame: pd.DataFrame) -> pd.Series:
    """Return whether each row of ``frame`` has unmet demand, at peak or per day."""
    return frame["unmet_peak_mw"].gt(TOLERANCE_MW) | frame["unmet_mwh_per_day"].gt(HOURS_PER_DAY * TOLERANCE_MW)


def _sorted(summary: pd.DataFrame) -> pd.DataFrame:
    """Return ``summary`` in the order of ``ASSET_TYPES``, each type from the most unmet demand to the least."""
    order = {asset_type: position for position, asset_type in enumerate(ASSET_TYPES)}
    return summary.sort_values(
        ["asset_type", "unmet_mwh_per_day", "unmet_peak_mw", "asset_id"],
        key=lambda column: column.map(order) if column.name == "asset_type" else column,
        ascending=[True, False, False, True],
    ).reset_index(drop=True)


def power_flow_failures(
    model: TransmissionModel,
    assets: pd.DataFrame,
    peak: pd.Series,
    average: pd.Series,
    *,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.Series]]:
    """Fail each asset of the power flow at peak and at average demand (MW per supply point).

    Returns one row per asset with ``unmet_mwh_per_day`` (the unmet demand at average demand over 24 hours),
    ``unmet_peak_mw`` and ``line_limits_peak_mw`` (the part of ``unmet_peak_mw`` due to line ratings); the two
    unmet quantities by asset and substation area (``area_bus_id``), where either is above zero; and the unmet
    demand at each supply point with nothing failed, by level (MW). When demand is unmet with nothing failed, an
    asset's areas can add up to more than its total, since a failure can move the shortfall from one area to another.
    """
    settings = {"s_max_pu": s_max_pu, "island_carriers": island_carriers}
    at_peak, peak_totals, nothing_failed_peak = _failures(model, assets, peak, settings)
    at_average, average_totals, nothing_failed_average = _failures(model, assets, average, settings)
    # The part due to line ratings: each failure that leaves demand unmet at peak, solved again without ratings.
    leaving_unmet = assets[assets["asset_id"].map(peak_totals).gt(TOLERANCE_MW)]
    _, unrated_totals, _ = _failures(model, leaving_unmet, peak, settings, line_ratings=False)

    summary = assets[["asset_id", "asset_type", "name", "island"]].copy()
    summary["lines"] = assets["lines"].map(" ".join)
    summary["unmet_mwh_per_day"] = summary["asset_id"].map(HOURS_PER_DAY * average_totals)
    summary["unmet_peak_mw"] = summary["asset_id"].map(peak_totals)
    summary["line_limits_peak_mw"] = summary["asset_id"].map((peak_totals - unrated_totals).clip(lower=0.0)).fillna(0.0)

    by_area = _by_area(HOURS_PER_DAY * at_average, "unmet_mwh_per_day").merge(
        _by_area(at_peak, "unmet_peak_mw"), on=["asset_id", "area_bus_id"]
    )
    by_area = by_area[_leaves_demand_unmet(by_area)].sort_values(["asset_id", "area_bus_id"]).reset_index(drop=True)
    return _sorted(summary), by_area, {"peak": nothing_failed_peak, "average": nothing_failed_average}


def all_failures(
    network: pypsa.Network,
    model: TransmissionModel,
    assets: pd.DataFrame,
    substation_weights: pd.DataFrame,
    node_weights: pd.DataFrame,
    demand_levels: pd.DataFrame,
    *,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Fail each component at peak and at average demand: by power flow for the substations, transmission lines and
    plants (``assets``), and by connectivity for the distribution lines.

    ``substation_weights`` and ``node_weights`` are ``service_weights_substations.csv`` and
    ``service_weights_nodes.csv``. Returns one row per component with ``unmet_mwh_per_day``, ``unmet_peak_mw``,
    ``line_limits_peak_mw`` (empty for a distribution line, which the power flow does not include), ``in_service``
    (false for a distribution line left open) and ``method`` (``power_flow`` or ``connectivity``); the two unmet
    quantities by component and service area (``area_bus_id`` and ``area_name``), where either is above zero; and a
    summary with the demand, the unmet demand with nothing failed and counts of distribution lines.
    """
    demand = {level: demand_by_supply_point(substation_weights, demand_levels, level) for level in LEVELS}
    power_flow_rows, power_flow_by_area, nothing_failed = power_flow_failures(
        model, assets, demand["peak"], demand["average"], s_max_pu=s_max_pu, island_carriers=island_carriers
    )
    # Each distribution line's failure starts from the supply the power flow gives its area with nothing failed.
    supplied_share = {
        level: (1.0 - nothing_failed[level] / demand[level].where(demand[level] > 0)).fillna(1.0).clip(0.0, 1.0)
        for level in LEVELS
    }
    lines, not_joined, counts = distribution_failures(
        network.lines[~is_transmission_line(network, max_radial_line_voltage_kv)],
        network.generators,
        node_weights,
        {level: island_demand(demand_levels, level) for level in LEVELS},
        island_carriers=island_carriers,
        supplied_share=supplied_share,
    )

    power_flow_rows = power_flow_rows.assign(in_service=True, method="power_flow")
    distribution_rows = pd.DataFrame(
        {
            "asset_id": lines.index,
            "asset_type": "distribution_line",
            "name": lines.index,
            "island": lines["island"].to_numpy(),
            "lines": lines.index,
            "unmet_mwh_per_day": HOURS_PER_DAY * lines["unmet_average_mw"].to_numpy(),
            "unmet_peak_mw": lines["unmet_peak_mw"].to_numpy(),
            "line_limits_peak_mw": np.nan,
            "in_service": lines["in_service"].to_numpy(dtype=bool),
            "method": "connectivity",
        }
    )
    summary = _sorted(pd.concat([power_flow_rows, distribution_rows], ignore_index=True))

    distribution_by_area = distribution_rows[["asset_id", *QUANTITIES]].assign(
        area_bus_id=lines["area_bus_id"].to_numpy()
    )
    distribution_by_area = distribution_by_area[
        distribution_by_area["area_bus_id"].ne("") & _leaves_demand_unmet(distribution_by_area)
    ]
    by_area = pd.concat([power_flow_by_area, distribution_by_area], ignore_index=True)[
        ["asset_id", "area_bus_id", *QUANTITIES]
    ]
    by_area = by_area.sort_values(["asset_id", "area_bus_id"]).reset_index(drop=True)
    by_area.insert(2, "area_name", by_area["area_bus_id"].map(bus_names(network)))

    notes = {
        "demand_mw": {level: round(float(demand[level].sum()), 3) for level in LEVELS},
        "unmet_with_nothing_failed": {
            "unmet_mwh_per_day": HOURS_PER_DAY * (float(nothing_failed["average"].sum()) + not_joined["average"]),
            "unmet_peak_mw": float(nothing_failed["peak"].sum()) + not_joined["peak"],
        },
        "distribution_lines": counts,
    }
    return summary, by_area, notes


def build_criticality(
    *,
    network_path: Path,
    substation_weights_path: Path,
    node_weights_path: Path,
    demand_levels_path: Path,
    output_dir: Path,
    line_circuits_path: Path | None = None,
    line_types: dict[int, str] | None = None,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> CriticalityOutputs:
    """Write ``criticality.csv``, ``criticality_by_area.csv``, ``line_ratings.csv`` and ``metadata.json``.

    ``line_circuits_path`` is optional (see ``transmission_model``). Components and service areas take their names
    from the network. A setting that leaves the power flow with no lines is logged as a warning and kept in
    ``metadata.json``.
    """
    line_types = line_types or DEFAULT_LINE_TYPES
    island_carriers = tuple(island_carriers)
    network = pypsa.Network(str(network_path))
    line_circuits = read_line_circuits(line_circuits_path) if line_circuits_path else None
    weights = pd.read_csv(substation_weights_path)
    node_weights = pd.read_csv(node_weights_path)
    levels = pd.read_csv(demand_levels_path, comment="#")
    model = transmission_model(
        network,
        weights.set_index("bus_id")["island"],
        line_circuits=line_circuits,
        line_types=line_types,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
    )
    warnings = []
    if model.lines.empty:
        warnings.append(
            f"No line is above max_radial_line_voltage_kv ({max_radial_line_voltage_kv:g} kV), so the power flow has "
            "no lines: each island is one bus, and every line, transmission lines included, is run radially."
        )
        logger.warning(warnings[-1])
    assets = failure_assets(model, network, line_circuits)
    summary, by_area, notes = all_failures(
        network,
        model,
        assets,
        weights,
        node_weights,
        levels,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        s_max_pu=s_max_pu,
        island_carriers=island_carriers,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = CriticalityOutputs(
        criticality=output_dir / "criticality.csv",
        by_area=output_dir / "criticality_by_area.csv",
        line_ratings=output_dir / "line_ratings.csv",
        metadata=output_dir / "metadata.json",
    )
    summary.round(3).to_csv(outputs.criticality, index=False)
    by_area.round(3).to_csv(outputs.by_area, index=False)
    ratings = model.lines[["route", "built_for_kv", "circuits", "v_nom", "s_nom"]].rename(
        columns={"route": "asset_id", "v_nom": "voltage_kv", "s_nom": "rating_mva"}
    )
    ratings.rename_axis("line_id").round(3).to_csv(outputs.line_ratings)
    metadata = {
        "network": str(network_path),
        "line_circuits": str(line_circuits_path) if line_circuits_path else None,
        "substation_weights": str(substation_weights_path),
        "node_weights": str(node_weights_path),
        "demand_levels": str(demand_levels_path),
        "line_types": {str(kv): name for kv, name in line_types.items()},
        "max_radial_line_voltage_kv": max_radial_line_voltage_kv,
        "s_max_pu": s_max_pu,
        "island_carriers": list(island_carriers),
        "demand_mw": notes["demand_mw"],
        "unmet_with_nothing_failed": {
            name: round(value, 3) for name, value in notes["unmet_with_nothing_failed"].items()
        },
        "power_flow": {"buses": len(model.buses), "lines": len(model.lines), "failures": len(assets)},
        "distribution_lines": notes["distribution_lines"],
        "assets": summary["asset_type"].value_counts().to_dict(),
        "warnings": warnings,
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return outputs
