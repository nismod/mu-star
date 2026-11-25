"""Unmet demand when one distribution line fails, from the connectivity of the network (N-1 criticality).

The distribution lines, those at or below ``max_radial_line_voltage_kv``, are run radially, as distribution networks
are in normal operation. Each supply point feeds the nodes of its service area (``area_bus_id`` in
``service_weights_nodes.csv``) along the shortest routes through the area's lines; the area's other lines and the
lines between areas are open (``in_service`` is false). A failed line cuts off the nodes beyond it until it is
repaired: switching, which could restore some of them through another route, is left out. A part cut off keeps supply
only from a plant of an ``island_carriers`` type (thermal, hydro) within it, up to the plant's capacity. The lines
that the build adds between a substation or plant and the nearest road (its anchors) fail with the substation or
plant, not alone.

For one failure without switching, a power flow would add nothing: the nodes beyond the failed line lose supply, and
the rest of the network only loses load. ``energy.criticality`` therefore uses this analysis for the distribution
lines, and a power flow for the meshed network above them.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd

# What a line in service cuts off when it fails: the nodes' share of their island's demand, the capacity of their
# plants (MW), and whether one of those plants can supply a part cut off (``island_carriers``).
CUT_COLUMNS = {"service_weight": float, "plant_capacity_mw": float, "has_island_carrier": bool}


def _area_failures(
    area_lines: pd.DataFrame,
    root: str,
    service_weight: pd.Series,
    plant_capacity_mw: pd.Series,
    has_island_carrier: pd.Series,
) -> tuple[pd.DataFrame, float]:
    """Return, for one service area, what each line in service cuts off when it fails, and the service weight of the
    nodes that the area's lines do not join to ``root``.

    The lines in service are those on the shortest routes from ``root`` through ``area_lines``; of two lines between
    the same nodes, the shorter. ``service_weight`` (each node's share of its island's demand), ``plant_capacity_mw``
    and ``has_island_carrier`` are indexed by node. Returns a frame indexed by the line_id of each line in service,
    with the ``CUT_COLUMNS`` of the nodes beyond it.
    """
    graph = nx.Graph()
    graph.add_nodes_from([root, *service_weight.index])
    for line_id, bus0, bus1, length in zip(
        area_lines.index, area_lines["bus0"], area_lines["bus1"], area_lines["length"]
    ):
        if bus0 != bus1 and (not graph.has_edge(bus0, bus1) or length < graph[bus0][bus1]["weight"]):
            graph.add_edge(bus0, bus1, weight=float(length), line=line_id)
    parent_of, _ = nx.dijkstra_predecessor_and_distance(graph, root, weight="weight")
    tree = nx.DiGraph((parents[0], node) for node, parents in parent_of.items() if parents)
    tree.add_node(root)
    beyond = pd.DataFrame(
        {
            "service_weight": service_weight.reindex(list(parent_of), fill_value=0.0),
            "plant_capacity_mw": plant_capacity_mw.reindex(list(parent_of), fill_value=0.0),
            "has_island_carrier": has_island_carrier.reindex(list(parent_of), fill_value=False),
        }
    ).to_dict("index")
    rows = {}
    for node in nx.dfs_postorder_nodes(tree, root):  # each node after the nodes beyond it
        if node == root:
            continue
        parent = parent_of[node][0]
        beyond[parent]["service_weight"] += beyond[node]["service_weight"]
        beyond[parent]["plant_capacity_mw"] += beyond[node]["plant_capacity_mw"]
        beyond[parent]["has_island_carrier"] |= beyond[node]["has_island_carrier"]
        rows[graph[parent][node]["line"]] = beyond[node]
    cut = pd.DataFrame.from_dict(rows, orient="index", columns=list(CUT_COLUMNS)).astype(CUT_COLUMNS)
    not_joined = float(service_weight.drop(index=list(parent_of), errors="ignore").sum())
    return cut, not_joined


def distribution_failures(
    lines: pd.DataFrame,
    plants: pd.DataFrame,
    node_weights: pd.DataFrame,
    island_demand: dict[str, pd.Series],
    *,
    island_carriers: tuple[str, ...],
    supplied_share: dict[str, pd.Series] | None = None,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    """Return the unmet demand (MW) when each distribution line fails, with the distribution lines run radially.

    ``lines`` are the network's distribution lines (``bus0``, ``bus1``, ``length`` and ``source``) and ``plants`` its
    generators (``bus``, ``p_nom`` and ``carrier``). A line whose ``source`` ends in ``_anchor`` is one the build adds
    between a substation or plant and the nearest road: it fails with the substation or plant, not alone.
    ``node_weights`` is ``service_weights_nodes.csv``; ``island_demand`` maps each level to each island's demand
    (MW); ``supplied_share`` maps each level to the share of each area's demand (by ``area_bus_id``) supplied with
    nothing failed (one where not given). Returns one row per distribution line other than an anchor, indexed by
    line_id, with ``area_bus_id`` (empty for a line between areas), ``island``, ``in_service`` (false for an open
    line, which cuts off nothing) and ``unmet_<level>_mw``; the demand of nodes not joined to their supply point,
    unmet with nothing failed (MW per level); and counts of lines.
    """
    supplied_share = supplied_share or {level: pd.Series(dtype=float) for level in island_demand}
    source = lines["source"].astype(str) if "source" in lines else pd.Series("", index=lines.index)
    anchor = source.str.endswith("_anchor")
    nodes = node_weights.assign(bus_id=node_weights["bus_id"].astype(str)).set_index("bus_id")
    area = nodes["area_bus_id"].astype(str)
    plant_capacity_mw = plants["p_nom"].astype(float).groupby(plants["bus"].astype(str)).sum()
    has_island_carrier = plants["carrier"].isin(island_carriers).groupby(plants["bus"].astype(str)).any()
    area0, area1 = lines["bus0"].map(area), lines["bus1"].map(area)
    line_area = area0.where(area0.eq(area1), "").fillna("")

    cut_parts, not_joined = [], {level: 0.0 for level in island_demand}
    for root, members in nodes.groupby(area):
        cut, weight_not_joined = _area_failures(
            lines[line_area.eq(root)],
            root,
            members["service_weight"].astype(float),
            plant_capacity_mw.drop(index=root, errors="ignore").reindex(members.index, fill_value=0.0),
            has_island_carrier.drop(index=root, errors="ignore").reindex(members.index, fill_value=False),
        )
        island = str(members["island"].iloc[0])
        for level, demand in island_demand.items():
            supplied = float(supplied_share[level].get(root, 1.0))
            not_joined[level] += weight_not_joined * float(demand[island]) * supplied
        cut_parts.append(cut)
    cut = pd.concat(cut_parts) if cut_parts else pd.DataFrame(columns=list(CUT_COLUMNS))

    table = pd.DataFrame(index=lines.index[~anchor])
    table["area_bus_id"] = line_area.reindex(table.index)
    table["island"] = lines["bus0"].map(nodes["island"]).reindex(table.index)
    cut = cut.reindex(table.index)
    table["in_service"] = cut["service_weight"].notna()
    for level, demand in island_demand.items():
        supplied = table["area_bus_id"].map(supplied_share[level]).fillna(1.0)
        cut_off = cut["service_weight"].fillna(0.0) * table["island"].map(demand).fillna(0.0) * supplied
        kept = cut["plant_capacity_mw"].fillna(0.0).where(cut["has_island_carrier"].eq(True), 0.0)
        table[f"unmet_{level}_mw"] = (cut_off - kept).clip(lower=0.0)
    counts = {
        "distribution_lines": len(table),
        "in_service": int(table["in_service"].sum()),
        "between_areas": int(table["area_bus_id"].eq("").sum()),
        "connections_added_by_the_build": int(anchor.sum()),
    }
    return table, not_joined, counts
