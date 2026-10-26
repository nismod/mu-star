"""Unmet demand when one substation, CEB line or power plant fails (N-1 criticality).

The CEB 66 kV network of inferred-provided is solved as a linear power flow (PyPSA, HiGHS) at peak
and at average demand, first with nothing failed, then with each asset failed in turn:

- Buses are the points where CEB lines meet. A substation's demand and plants sit on the 66 kV bus at
  the substation. An island without CEB lines (Rodrigues) is one bus with all its plants and demand.
- A line's rating is its number of circuits times the rating of one circuit of its PyPSA line type at
  66 kV; its reactance is the type's reactance per km times its length, divided by its circuits.
  Types and circuits come from ``ceb_line_circuits.csv``. The segments of one line (its ``route``)
  fail together.
- A supply point's demand is its share of its island's peak or average demand (``energy.demand``).
- Demand that cannot be supplied is shed. A part of the network cut off from the rest keeps supply
  only if it has a plant of an ``island_carriers`` type (thermal, hydro): solar and wind farms
  disconnect when the grid is lost.

Unmet demand is the demand the network cannot supply: demand minus supply, at each supply point. The
unmet demand of a failure is the demand shed minus any shed with nothing failed. Each failure gives two
quantities: the unmet demand at peak demand (MW), and the unmet energy per day of outage (MWh), which is
the unmet demand at average demand over 24 hours. The per-day value is exact when whole areas lose supply
and too low for shortfalls that occur only near peak. Each failure that leaves demand unmet at peak is
solved again without line ratings, to give the part due to the ratings.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

CEB_LINE_SOURCE = "provided_transmission"
VOLTAGE_KV = 66.0
DEFAULT_LINE_TYPES = {66: "149-AL1/24-ST1A 110.0", 132: "243-AL1/39-ST1A 110.0"}
DEFAULT_ISLAND_CARRIERS = ("thermal", "hydro")
SHED_COST = 10_000.0  # per MWh; plants cost nothing, so demand is shed only when it cannot be supplied
UNLIMITED_MVA = 1e6
MIN_REACTANCE_OHM = 1e-3  # for line segments of almost no length
SAME_POINT_M = 50.0  # the largest distance between a substation and its 66 kV bus
TOLERANCE_MW = 1e-6
HOURS_PER_DAY = 24
ASSET_TYPES = ("substation", "line", "plant")


@dataclass(frozen=True)
class TransmissionModel:
    """The 66 kV network solved for each failure.

    ``buses``: index bus, column ``island``. ``lines``: index line_id; ``bus0``, ``bus1``, ``route``,
    ``s_nom`` (MVA), ``x`` and ``r`` (ohm). ``plants``: index generator; ``bus``, ``p_nom`` (MW),
    ``carrier``. ``supply_points``: index supply point (network bus_id); ``bus`` and ``island``.
    """

    buses: pd.DataFrame
    lines: pd.DataFrame
    plants: pd.DataFrame
    supply_points: pd.DataFrame


@dataclass(frozen=True)
class CriticalityOutputs:
    criticality: Path
    by_area: Path
    metadata: Path


def read_line_circuits(path: Path) -> pd.DataFrame:
    """Read ``ceb_line_circuits.csv``: ``line_id``, ``route``, ``built_for_kv`` and ``circuits``, indexed by line_id."""
    table = pd.read_csv(path, comment="#")
    missing = {"line_id", "route", "built_for_kv", "circuits"} - set(table.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if table["line_id"].duplicated().any():
        raise ValueError(f"{path}: line_id appears more than once")
    return table.set_index("line_id")


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
        raise ValueError(f"{bus_id}: no 66 kV bus within {SAME_POINT_M:.0f} m (nearest {distances[nearest]:.0f} m)")
    return candidates[nearest]


def transmission_model(
    network: pypsa.Network,
    line_circuits: pd.DataFrame,
    supply_point_islands: pd.Series,
    *,
    line_types: dict[int, str] | None = None,
) -> TransmissionModel:
    """Build the 66 kV model from a network with CEB lines (inferred-provided).

    ``supply_point_islands`` maps each supply point (a substation, or a power station on an island
    with no substation) to its island, as in ``service_weights_substations.csv``.
    """
    line_types = {int(kv): name for kv, name in (line_types or DEFAULT_LINE_TYPES).items()}
    buses = network.buses
    ceb = network.lines[network.lines["source"].eq(CEB_LINE_SOURCE)]
    unlisted = sorted(set(ceb.index) - set(line_circuits.index))
    unknown = sorted(set(line_circuits.index) - set(ceb.index))
    if unlisted or unknown:
        raise ValueError(
            f"line circuits do not match the network: missing {unlisted[:5]}, not in network {unknown[:5]}"
        )
    circuits = line_circuits.reindex(ceb.index)
    no_type = sorted(set(circuits["built_for_kv"].astype(int)) - set(line_types))
    if no_type:
        raise ValueError(f"no line type for lines built for {no_type} kV")
    types = network.line_types.loc[circuits["built_for_kv"].astype(int).map(line_types)]
    count = circuits["circuits"].astype(float).to_numpy()
    if (count < 1).any():
        raise ValueError("every CEB line needs at least one circuit")
    length = ceb["length"].astype(float).to_numpy()
    lines = pd.DataFrame(
        {
            "bus0": ceb["bus0"],
            "bus1": ceb["bus1"],
            "route": circuits["route"].astype(str),
            "s_nom": math.sqrt(3) * VOLTAGE_KV * types["i_nom"].to_numpy() * count,
            "x": np.maximum(types["x_per_length"].to_numpy() * length / count, MIN_REACTANCE_OHM),
            "r": types["r_per_length"].to_numpy() * length / count,
        },
        index=ceb.index,
    )
    line_buses = sorted(set(lines["bus0"]) | set(lines["bus1"]))
    model_buses = pd.DataFrame({"island": buses.loc[line_buses, "region"].astype(str).to_numpy()}, index=line_buses)
    networked = set(model_buses["island"])

    supply_bus = {}
    for bus_id, island in supply_point_islands.astype(str).items():
        supply_bus[bus_id] = (
            _bus_at_same_point(buses, bus_id, line_buses) if island in networked else f"island::{island}"
        )
    island_buses = sorted({bus for bus in supply_bus.values() if bus.startswith("island::")})
    model_buses = pd.concat(
        [
            model_buses,
            pd.DataFrame({"island": [bus.removeprefix("island::") for bus in island_buses]}, index=island_buses),
        ]
    )

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
    supply_points = pd.DataFrame(
        {"bus": pd.Series(supply_bus), "island": supply_point_islands.astype(str)}
    ).rename_axis("bus_id")
    return TransmissionModel(model_buses.rename_axis("bus"), lines, plants[["bus", "p_nom", "carrier"]], supply_points)


def demand_by_supply_point(substation_weights: pd.DataFrame, demand_levels: pd.DataFrame, level: str) -> pd.Series:
    """Return each supply point's demand (MW): its ``service_weight`` times its island's ``level`` demand."""
    levels = demand_levels[demand_levels["level"].eq(level)].set_index("island")["demand_mw"].astype(float)
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
    line_limits: bool = True,
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
    live_plants = live_plants.loc[[plant for plant in live_plants.index if plant in set(keep)]]

    supply = model.supply_points
    load = demand.groupby(supply["bus"].reindex(demand.index)).sum()
    load = load[load.index.isin(buses) & (load > 0)]

    network = pypsa.Network()
    network.madd("Bus", buses, v_nom=VOLTAGE_KV)
    network.madd(
        "Line",
        live_lines.index,
        bus0=live_lines["bus0"].to_numpy(),
        bus1=live_lines["bus1"].to_numpy(),
        x=live_lines["x"].to_numpy(),
        r=live_lines["r"].to_numpy(),
        s_nom=live_lines["s_nom"].to_numpy() if line_limits else UNLIMITED_MVA,
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


def failure_assets(
    model: TransmissionModel,
    network: pypsa.Network,
    line_circuits: pd.DataFrame,
    substation_names: pd.Series | None = None,
) -> pd.DataFrame:
    """Return the assets to fail: substations, lines (one per route) and plants.

    Columns: ``asset_id``, ``asset_type``, ``name``, ``island``, ``lines``, ``bus`` and ``plants``.
    """
    names = substation_names if substation_names is not None else pd.Series(dtype=str)
    kind = network.buses["kind"].astype(str)
    rows = []
    for bus_id, point in model.supply_points.iterrows():
        if kind.get(bus_id) != "substation":
            continue
        asset_id = str(network.buses.at[bus_id, "asset_id"])
        rows.append(
            {
                "asset_id": asset_id,
                "asset_type": "substation",
                "name": names.get(asset_id, asset_id),
                "island": point["island"],
                "lines": (),
                "bus": point["bus"],
                "plants": (),
            }
        )
    ends = line_circuits["ends"] if "ends" in line_circuits else pd.Series(dtype=str)
    for route, block in model.lines.groupby("route", sort=True):
        island = model.buses.at[block["bus0"].iloc[0], "island"]
        name = str(ends.reindex(block.index).dropna().iloc[0]) if ends.reindex(block.index).notna().any() else route
        rows.append(
            {
                "asset_id": route,
                "asset_type": "line",
                "name": name,
                "island": island,
                "lines": tuple(block.index),
                "bus": None,
                "plants": (),
            }
        )
    for plant, row in model.plants.iterrows():
        rows.append(
            {
                "asset_id": plant,
                "asset_type": "plant",
                "name": plant,
                "island": model.buses.at[row["bus"], "island"],
                "lines": (),
                "bus": None,
                "plants": (plant,),
            }
        )
    return pd.DataFrame(rows)


def _unmet_by_failure(
    model: TransmissionModel, assets: pd.DataFrame, demand: pd.Series, settings: dict, *, split_limits: bool
) -> tuple[pd.DataFrame, pd.Series, float]:
    """Return the unmet demand (MW) of each failure (rows) at each supply point (columns), the part of each
    failure's total due to line ratings (zero unless ``split_limits``), and the total with nothing failed."""
    base = unmet_demand(model, demand, **settings)
    base_free = unmet_demand(model, demand, line_limits=False, **settings) if split_limits else None
    unmet_rows, limits = {}, {}
    for asset in assets.itertuples(index=False):
        failure = {"lines": asset.lines, "bus": asset.bus, "plants": asset.plants}
        unmet = (unmet_demand(model, demand, **failure, **settings) - base).clip(lower=0.0)
        limits[asset.asset_id] = 0.0
        if split_limits and unmet.sum() > TOLERANCE_MW:
            free = (unmet_demand(model, demand, **failure, line_limits=False, **settings) - base_free).clip(lower=0.0)
            limits[asset.asset_id] = max(0.0, float(unmet.sum() - free.sum()))
        unmet_rows[asset.asset_id] = unmet
    return pd.DataFrame(unmet_rows).T, pd.Series(limits), float(base.sum())


def _by_area(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return a failures-by-supply-points table as rows of ``asset_id``, ``area_bus_id`` and ``name``."""
    frame = frame.rename_axis(index="asset_id", columns="area_bus_id").reset_index()
    return frame.melt(id_vars="asset_id", var_name="area_bus_id", value_name=name)


def criticality(
    model: TransmissionModel,
    assets: pd.DataFrame,
    peak: pd.Series,
    average: pd.Series,
    *,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Fail each asset at peak and at average demand (MW per supply point).

    Returns one row per asset with ``unmet_mwh_per_day`` (the unmet demand at average demand over 24 hours),
    ``unmet_peak_mw`` and ``line_limits_peak_mw`` (the part of ``unmet_peak_mw`` due to line ratings); the two
    unmet quantities by asset and substation area (``area_bus_id``), where either is above zero; and the two
    for the intact network.
    """
    settings = {"s_max_pu": s_max_pu, "island_carriers": tuple(island_carriers)}
    at_peak, limits, intact_peak = _unmet_by_failure(model, assets, peak, settings, split_limits=True)
    at_average, _, intact_average = _unmet_by_failure(model, assets, average, settings, split_limits=False)

    summary = assets[["asset_id", "asset_type", "name", "island"]].copy()
    summary["lines"] = assets["lines"].map(" ".join)
    summary["unmet_mwh_per_day"] = summary["asset_id"].map(HOURS_PER_DAY * at_average.sum(axis=1))
    summary["unmet_peak_mw"] = summary["asset_id"].map(at_peak.sum(axis=1))
    summary["line_limits_peak_mw"] = summary["asset_id"].map(limits)
    order = {asset_type: position for position, asset_type in enumerate(ASSET_TYPES)}
    summary = summary.sort_values(
        ["asset_type", "unmet_mwh_per_day", "unmet_peak_mw", "asset_id"],
        key=lambda column: column.map(order) if column.name == "asset_type" else column,
        ascending=[True, False, False, True],
    )

    by_area = _by_area(HOURS_PER_DAY * at_average, "unmet_mwh_per_day").merge(
        _by_area(at_peak, "unmet_peak_mw"), on=["asset_id", "area_bus_id"]
    )
    hit = by_area["unmet_peak_mw"].gt(TOLERANCE_MW) | by_area["unmet_mwh_per_day"].gt(HOURS_PER_DAY * TOLERANCE_MW)
    by_area = by_area[hit].sort_values(["asset_id", "area_bus_id"]).reset_index(drop=True)
    intact = {"unmet_mwh_per_day": HOURS_PER_DAY * intact_average, "unmet_peak_mw": intact_peak}
    return summary.reset_index(drop=True), by_area, intact


def build_criticality(
    *,
    network_path: Path,
    line_circuits_path: Path,
    substation_names_path: Path,
    substation_weights_path: Path,
    demand_levels_path: Path,
    output_dir: Path,
    line_types: dict[int, str] | None = None,
    s_max_pu: float = 1.0,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> CriticalityOutputs:
    """Write ``criticality.csv``, ``criticality_by_area.csv`` and ``metadata.json`` to ``output_dir``."""
    network = pypsa.Network(str(network_path))
    line_circuits = read_line_circuits(line_circuits_path)
    names = pd.read_csv(substation_names_path, comment="#").set_index("substation")["name"]
    weights = pd.read_csv(substation_weights_path)
    levels = pd.read_csv(demand_levels_path, comment="#")
    model = transmission_model(network, line_circuits, weights.set_index("bus_id")["island"], line_types=line_types)
    assets = failure_assets(model, network, line_circuits, names)
    demands = {level: demand_by_supply_point(weights, levels, level) for level in ("peak", "average")}
    summary, by_area, intact = criticality(
        model, assets, demands["peak"], demands["average"], s_max_pu=s_max_pu, island_carriers=tuple(island_carriers)
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = CriticalityOutputs(
        criticality=output_dir / "criticality.csv",
        by_area=output_dir / "criticality_by_area.csv",
        metadata=output_dir / "metadata.json",
    )
    summary.round(3).to_csv(outputs.criticality, index=False)
    by_area.round(3).to_csv(outputs.by_area, index=False)
    metadata = {
        "network": str(network_path),
        "line_circuits": str(line_circuits_path),
        "substation_names": str(substation_names_path),
        "substation_weights": str(substation_weights_path),
        "demand_levels": str(demand_levels_path),
        "line_types": {str(kv): name for kv, name in (line_types or DEFAULT_LINE_TYPES).items()},
        "s_max_pu": s_max_pu,
        "island_carriers": list(island_carriers),
        "demand_mw": {level: round(float(demand.sum()), 3) for level, demand in demands.items()},
        "unmet_with_nothing_failed": {name: round(value, 3) for name, value in intact.items()},
        "assets": summary["asset_type"].value_counts().to_dict(),
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return outputs
