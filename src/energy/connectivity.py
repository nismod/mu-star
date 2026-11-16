"""Unmet demand when one component fails, from the connectivity of the network (N-1 criticality).

The power-flow analysis (``energy.criticality``) needs line ratings and one optimisation per failure, which
limits it to transmission networks of a few thousand buses. This analysis needs neither, so it also covers
the distribution lines:

- The transmission lines, those above ``max_radial_line_voltage_kv``, are run meshed. After a failure, each
  part of the transmission network supplies its demand up to the capacity of its plants, and only if it has a
  plant of an ``island_carriers`` type (thermal, hydro); a shortfall is shared between the part's supply
  points in proportion to their demand. Line ratings are left out, so the results equal the power flow's
  wherever no rating binds.
- The distribution lines, those at or below it, are run radially. Each supply point feeds the nodes of its
  service area (``area_bus_id`` in ``service_weights_nodes.csv``) along the shortest routes through the area's
  lines; the lines between areas are open. A failed line cuts off the nodes beyond it. After switching, a
  failed line cuts off demand only if no other route through the area's lines remains, that is, if it is a
  bridge of the area's network. No demand moves to another area, since the ratings of the lines between
  areas are unknown. A part cut off keeps supply only as a part of the transmission network does: from a
  plant of an ``island_carriers`` type within it.
- Substations, transmission lines and plants fail as in the power-flow analysis. The connections that the
  build adds between a substation or plant and the nearest road (``CONNECTOR_SOURCES``) fail with it, not
  alone.

Each failure gives the unmet demand at peak (MW) and the unmet energy per day of outage (MWh, at average
demand), with the distribution lines run radially and after switching; the two are equal for substations,
transmission lines and plants. A failure's unmet demand is the rise over the unmet demand with nothing failed.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import pandas as pd
import pypsa

from energy.criticality import (
    DEFAULT_ISLAND_CARRIERS,
    HOURS_PER_DAY,
    TOLERANCE_MW,
    TransmissionModel,
    demand_by_supply_point,
    failure_assets,
    read_line_circuits,
    transmission_model,
)
from energy.demand import DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV

CONNECTOR_SOURCES = ("substation_anchor", "generator_anchor")
ASSET_TYPES = ("substation", "line", "plant", "distribution_line")
LEVELS = ("peak", "average")
QUANTITIES = (
    "unmet_mwh_per_day",
    "unmet_peak_mw",
    "unmet_after_switching_mwh_per_day",
    "unmet_after_switching_peak_mw",
)


@dataclass(frozen=True)
class ConnectivityOutputs:
    criticality: Path
    by_area: Path
    metadata: Path


def meshed_unmet_demand(
    model: TransmissionModel,
    demand: pd.Series,
    *,
    lines: tuple[str, ...] = (),
    bus: str | None = None,
    plants: tuple[str, ...] = (),
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> pd.Series:
    """Return the unmet demand at each supply point (MW) with ``lines``, ``bus`` and ``plants`` failed.

    Each part of the transmission network supplies its demand up to the capacity of its plants if it has a
    plant of an ``island_carriers`` type, and nothing otherwise; a shortfall is shared between the part's supply
    points in proportion to their demand. A failed bus takes its lines, plants and demand with it.
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
    point_bus = model.supply_points["bus"].reindex(demand.index)
    unmet = pd.Series(0.0, index=demand.index, name="unmet_mw")
    for part in nx.connected_components(graph):
        in_part = point_bus.isin(part).to_numpy()
        part_plants = live_plants[live_plants["bus"].isin(part)]
        capacity = float(part_plants["p_nom"].sum()) if part_plants["carrier"].isin(island_carriers).any() else 0.0
        total = float(demand[in_part].sum())
        if total > capacity:
            unmet[in_part] = demand[in_part] * (1.0 - capacity / total)
    if bus is not None:
        at_bus = point_bus.eq(bus).to_numpy()
        unmet[at_bus] = demand[at_bus]
    return unmet


def _transmission_failures(
    model: TransmissionModel, assets: pd.DataFrame, demand: pd.Series, island_carriers: tuple[str, ...]
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return the rise in unmet demand (MW) when each asset fails, at each supply point (rows: assets) and in
    total, and the unmet demand at each supply point with nothing failed."""
    base = meshed_unmet_demand(model, demand, island_carriers=island_carriers)
    rows, totals = {}, {}
    for asset in assets.itertuples(index=False):
        unmet = meshed_unmet_demand(
            model, demand, lines=asset.lines, bus=asset.bus, plants=asset.plants, island_carriers=island_carriers
        )
        rows[asset.asset_id] = (unmet - base).clip(lower=0.0)
        totals[asset.asset_id] = max(0.0, float(unmet.sum() - base.sum()))
    return pd.DataFrame(rows).T, pd.Series(totals, dtype=float), base


def distribution_lines(network: pypsa.Network, max_radial_line_voltage_kv: float) -> pd.DataFrame:
    """Return the lines at or below ``max_radial_line_voltage_kv``: ``bus0``, ``bus1``, ``length`` and
    ``connector`` (a connection the build adds, which fails with its substation or plant)."""
    lines = network.lines
    voltage0 = network.buses.loc[lines["bus0"], "v_nom"].astype(float).to_numpy()
    voltage1 = network.buses.loc[lines["bus1"], "v_nom"].astype(float).to_numpy()
    below = lines[(voltage0 <= max_radial_line_voltage_kv) & (voltage1 <= max_radial_line_voltage_kv)]
    source = below["source"].astype(str) if "source" in below else pd.Series("", index=below.index)
    return below[["bus0", "bus1", "length"]].assign(connector=source.isin(CONNECTOR_SOURCES).to_numpy())


def _area_failures(area_lines: pd.DataFrame, root: str, weight: pd.Series, capacity: pd.Series, carrier: pd.Series):
    """Return, for one service area, the share of the area's weight and the plant capacity cut off when each
    line fails, radial and after switching, and the weight of the nodes not joined to ``root``.

    ``weight`` is each node's share of its island's demand; ``capacity`` (MW) and ``carrier`` (whether one of its
    plants is of an ``island_carriers`` type) are indexed by node. Returns a frame indexed by line_id with
    ``weight``, ``capacity``, ``island_carrier``, ``radial`` and ``bridge`` (whether the line cuts off these nodes
    in radial operation and after switching), and the weight not joined.
    """
    graph = nx.Graph()
    graph.add_nodes_from([root, *weight.index])
    doubled = set()
    for line_id, bus0, bus1, length in zip(
        area_lines.index, area_lines["bus0"], area_lines["bus1"], area_lines["length"]
    ):
        if bus0 == bus1:
            continue
        if graph.has_edge(bus0, bus1):
            doubled.add(frozenset((bus0, bus1)))
            if length < graph[bus0][bus1]["weight"]:
                graph[bus0][bus1].update(weight=float(length), line=line_id)
        else:
            graph.add_edge(bus0, bus1, weight=float(length), line=line_id)
    parent_of, _ = nx.dijkstra_predecessor_and_distance(graph, root, weight="weight")
    children: dict[str, list[str]] = {}
    for node, parents in parent_of.items():
        if parents:
            children.setdefault(parents[0], []).append(node)
    order, queue = [], deque([root])
    while queue:
        node = queue.popleft()
        order.append(node)
        queue.extend(children.get(node, []))
    below_weight = weight.reindex(order, fill_value=0.0).to_dict()
    below_capacity = capacity.reindex(order, fill_value=0.0).to_dict()
    below_carrier = carrier.reindex(order, fill_value=False).to_dict()
    for node in reversed(order[1:]):
        parent = parent_of[node][0]
        below_weight[parent] += below_weight[node]
        below_capacity[parent] += below_capacity[node]
        below_carrier[parent] = below_carrier[parent] or below_carrier[node]
    bridges = {frozenset(edge) for edge in nx.bridges(graph)} - doubled
    rows = {}
    for node in order[1:]:
        parent = parent_of[node][0]
        rows[graph[parent][node]["line"]] = {
            "weight": below_weight[node],
            "capacity": below_capacity[node],
            "island_carrier": below_carrier[node],
            "radial": True,
            "bridge": frozenset((parent, node)) in bridges,
        }
    columns = {"weight": float, "capacity": float, "island_carrier": bool, "radial": bool, "bridge": bool}
    cut = pd.DataFrame.from_dict(rows, orient="index", columns=list(columns)).astype(columns)
    not_joined = float(weight.drop(index=order, errors="ignore").sum())
    return cut, not_joined


def distribution_failures(
    network: pypsa.Network,
    node_weights: pd.DataFrame,
    island_demand: dict[str, pd.Series],
    *,
    supplied_share: dict[str, pd.Series] | None = None,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    """Return the unmet demand (MW) when each distribution line fails, radial and after switching.

    ``node_weights`` is ``service_weights_nodes.csv``; ``island_demand`` maps each level to each island's demand
    (MW); ``supplied_share`` maps each level to the share of each area's demand (by ``area_bus_id``) supplied with
    nothing failed (one when not given). Returns one row per distribution line other than a connection the build
    adds, indexed by line_id, with ``area_bus_id`` (empty for a line between areas), ``island``,
    ``unmet_<level>_mw`` and ``unmet_after_switching_<level>_mw``; the demand of nodes not joined to their supply
    point, unmet with nothing failed (MW per level); and counts of lines by role.
    """
    lines = distribution_lines(network, max_radial_line_voltage_kv)
    nodes = node_weights.assign(bus_id=node_weights["bus_id"].astype(str)).set_index("bus_id")
    area = nodes["area_bus_id"].astype(str)
    plants = network.generators
    capacity = plants["p_nom"].astype(float).groupby(plants["bus"].astype(str)).sum()
    carrier = plants["carrier"].isin(island_carriers).groupby(plants["bus"].astype(str)).any()
    area0, area1 = lines["bus0"].map(area), lines["bus1"].map(area)
    line_area = area0.where(area0.eq(area1), "").fillna("")

    cut_parts, not_joined = [], {level: 0.0 for level in island_demand}
    for root, members in nodes.groupby(area):
        cut, weight_not_joined = _area_failures(
            lines[line_area.eq(root)],
            root,
            members["service_weight"].astype(float),
            capacity.drop(index=root, errors="ignore").reindex(members.index, fill_value=0.0),
            carrier.drop(index=root, errors="ignore").reindex(members.index, fill_value=False),
        )
        island = str(members["island"].iloc[0])
        for level, demand in island_demand.items():
            supplied = 1.0 if supplied_share is None else float(supplied_share[level].get(root, 1.0))
            not_joined[level] += weight_not_joined * float(demand[island]) * supplied
        cut_parts.append(cut.assign(area_bus_id=root, island=island))
    cut = pd.concat(cut_parts) if cut_parts else pd.DataFrame()

    table = pd.DataFrame(index=lines.index[~lines["connector"]])
    table["area_bus_id"] = line_area.reindex(table.index)
    table["island"] = (
        table["area_bus_id"]
        .map(nodes.groupby("area_bus_id")["island"].first())
        .fillna(lines["bus0"].map(nodes["island"]).reindex(table.index))
    )
    cut = cut.reindex(table.index)
    radial, bridge = cut["radial"].eq(True), cut["bridge"].eq(True)
    for level, demand in island_demand.items():
        supplied = table["area_bus_id"].map(supplied_share[level]).fillna(1.0) if supplied_share else 1.0
        cut_off = cut["weight"].fillna(0.0) * table["island"].map(demand).fillna(0.0) * supplied
        kept = cut["capacity"].fillna(0.0).where(cut["island_carrier"].eq(True), 0.0)
        unmet = (cut_off - kept).clip(lower=0.0)
        table[f"unmet_{level}_mw"] = unmet.where(radial, 0.0)
        table[f"unmet_after_switching_{level}_mw"] = unmet.where(bridge, 0.0)
    counts = {
        "distribution_lines": len(table),
        "between_areas": int(table["area_bus_id"].eq("").sum()),
        "in_radial_trees": int(radial.sum()),
        "bridges": int(bridge.sum()),
        "connections_added_by_the_build": int(lines["connector"].sum()),
    }
    return table, not_joined, counts


def _long(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Return a failures-by-supply-points table as rows of ``asset_id``, ``area_bus_id`` and ``name``."""
    frame = frame.rename_axis(index="asset_id", columns="area_bus_id").reset_index()
    return frame.melt(id_vars="asset_id", var_name="area_bus_id", value_name=name)


def _quantities(peak: pd.Series, average: pd.Series, peak_switched: pd.Series, average_switched: pd.Series):
    return pd.DataFrame(
        {
            "unmet_mwh_per_day": HOURS_PER_DAY * average,
            "unmet_peak_mw": peak,
            "unmet_after_switching_mwh_per_day": HOURS_PER_DAY * average_switched,
            "unmet_after_switching_peak_mw": peak_switched,
        }
    )


def connectivity_criticality(
    model: TransmissionModel,
    assets: pd.DataFrame,
    network: pypsa.Network,
    node_weights: pd.DataFrame,
    demand_levels: pd.DataFrame,
    supply_point_weights: pd.DataFrame,
    *,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Fail each substation, transmission line, plant and distribution line at peak and at average demand.

    Returns one row per asset with the four ``QUANTITIES``; the same by asset and service area (``area_bus_id``),
    where any is above zero; and a summary with the unmet demand with nothing failed and counts of distribution
    lines.
    """
    carriers = tuple(island_carriers)
    island_demand = {
        level: demand_levels[demand_levels["level"].eq(level)].set_index("island")["demand_mw"].astype(float)
        for level in LEVELS
    }
    transmission, supplied_share, intact = {}, {}, {}
    for level in LEVELS:
        demand = demand_by_supply_point(supply_point_weights, demand_levels, level)
        by_point, totals, base = _transmission_failures(model, assets, demand, carriers)
        transmission[level] = (by_point, totals)
        supplied_share[level] = (1.0 - base / demand.where(demand > 0)).fillna(1.0)
        intact[level] = float(base.sum())
    lines, not_joined, counts = distribution_failures(
        network,
        node_weights,
        island_demand,
        supplied_share=supplied_share,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        island_carriers=carriers,
    )

    upper = assets[["asset_id", "asset_type", "name", "island"]].assign(lines=assets["lines"].map(" ".join))
    peak, average = transmission["peak"][1], transmission["average"][1]
    upper = upper.join(_quantities(peak, average, peak, average), on="asset_id")
    lower = pd.DataFrame(
        {
            "asset_id": lines.index,
            "asset_type": "distribution_line",
            "name": lines.index,
            "island": lines["island"].to_numpy(),
            "lines": lines.index,
        }
    ).join(
        _quantities(
            lines["unmet_peak_mw"],
            lines["unmet_average_mw"],
            lines["unmet_after_switching_peak_mw"],
            lines["unmet_after_switching_average_mw"],
        ),
        on="asset_id",
    )
    summary = pd.concat([upper, lower], ignore_index=True)
    order = {asset_type: position for position, asset_type in enumerate(ASSET_TYPES)}
    summary = summary.sort_values(
        ["asset_type", "unmet_mwh_per_day", "unmet_peak_mw", "asset_id"],
        key=lambda column: column.map(order) if column.name == "asset_type" else column,
        ascending=[True, False, False, True],
    ).reset_index(drop=True)

    upper_by_area = _long(HOURS_PER_DAY * transmission["average"][0], "unmet_mwh_per_day").merge(
        _long(transmission["peak"][0], "unmet_peak_mw"), on=["asset_id", "area_bus_id"]
    )
    upper_by_area["unmet_after_switching_mwh_per_day"] = upper_by_area["unmet_mwh_per_day"]
    upper_by_area["unmet_after_switching_peak_mw"] = upper_by_area["unmet_peak_mw"]
    lower_by_area = lower.set_index("asset_id")[list(QUANTITIES)].assign(area_bus_id=lines["area_bus_id"])
    lower_by_area = lower_by_area[lower_by_area["area_bus_id"].ne("")].reset_index()
    by_area = pd.concat([upper_by_area, lower_by_area], ignore_index=True)[["asset_id", "area_bus_id", *QUANTITIES]]
    per_day = ["unmet_mwh_per_day", "unmet_after_switching_mwh_per_day"]
    at_peak = ["unmet_peak_mw", "unmet_after_switching_peak_mw"]
    hit = by_area[per_day].gt(HOURS_PER_DAY * TOLERANCE_MW).any(axis=1) | by_area[at_peak].gt(TOLERANCE_MW).any(axis=1)
    by_area = by_area[hit].sort_values(["asset_id", "area_bus_id"]).reset_index(drop=True)

    unmet_intact = {
        "unmet_mwh_per_day": HOURS_PER_DAY * (intact["average"] + not_joined["average"]),
        "unmet_peak_mw": intact["peak"] + not_joined["peak"],
    }
    return summary, by_area, {"unmet_with_nothing_failed": unmet_intact, "distribution_lines": counts}


def build_connectivity_criticality(
    *,
    network_path: Path,
    substation_weights_path: Path,
    node_weights_path: Path,
    demand_levels_path: Path,
    output_dir: Path,
    line_circuits_path: Path | None = None,
    substation_names_path: Path | None = None,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
    island_carriers: tuple[str, ...] = DEFAULT_ISLAND_CARRIERS,
) -> ConnectivityOutputs:
    """Write ``criticality.csv``, ``criticality_by_area.csv`` and ``metadata.json``.

    ``line_circuits_path`` and ``substation_names_path`` are optional, as for the power-flow analysis: the
    circuits table gives the routes that fail as one line, and the names table names the substations.
    """
    network = pypsa.Network(str(network_path))
    line_circuits = read_line_circuits(line_circuits_path) if line_circuits_path else None
    names = (
        pd.read_csv(substation_names_path, comment="#").set_index("substation")["name"]
        if substation_names_path
        else None
    )
    weights = pd.read_csv(substation_weights_path)
    node_weights = pd.read_csv(node_weights_path)
    levels = pd.read_csv(demand_levels_path, comment="#")
    model = transmission_model(
        network,
        weights.set_index("bus_id")["island"],
        line_circuits=line_circuits,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        rated=False,
    )
    assets = failure_assets(model, network, line_circuits, names)
    summary, by_area, notes = connectivity_criticality(
        model,
        assets,
        network,
        node_weights,
        levels,
        weights,
        max_radial_line_voltage_kv=max_radial_line_voltage_kv,
        island_carriers=tuple(island_carriers),
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = ConnectivityOutputs(
        criticality=output_dir / "criticality.csv",
        by_area=output_dir / "criticality_by_area.csv",
        metadata=output_dir / "metadata.json",
    )
    summary.round(3).to_csv(outputs.criticality, index=False)
    by_area.round(3).to_csv(outputs.by_area, index=False)
    metadata = {
        "network": str(network_path),
        "line_circuits": str(line_circuits_path) if line_circuits_path else None,
        "substation_names": str(substation_names_path) if substation_names_path else None,
        "substation_weights": str(substation_weights_path),
        "node_weights": str(node_weights_path),
        "demand_levels": str(demand_levels_path),
        "max_radial_line_voltage_kv": max_radial_line_voltage_kv,
        "island_carriers": list(island_carriers),
        "demand_mw": {level: round(float(demand_by_supply_point(weights, levels, level).sum()), 3) for level in LEVELS},
        "unmet_with_nothing_failed": {
            name: round(value, 3) for name, value in notes["unmet_with_nothing_failed"].items()
        },
        "distribution_lines": notes["distribution_lines"],
        "assets": summary["asset_type"].value_counts().to_dict(),
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return outputs
