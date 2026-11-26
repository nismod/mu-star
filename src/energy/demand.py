"""Estimate each substation's and each network node's share of its island's demand.

The method is PyPSA-Earth's ``build_demand_profiles.upsample`` (0.6 GDP + 0.4 population), with
VIIRS night-light radiance in place of GDP as the measure of economic activity: a GDP raster is
too coarse for a 50 km island.

1. Each substation serves the nodes nearer to it along the distribution lines (the lines at or below
   ``max_radial_line_voltage_kv``) than to any other substation. An island with no substation
   (Rodrigues) is served from its power stations. A node that the distribution lines do not join to
   any supply point gets no demand; it is counted in the area of the supply point nearest in a
   straight line. A substation's service area is the part of its island nearer to its nodes than to
   any other node.
2. Each raster cell goes to the nearest node joined to a supply point. An area's score is
   ``0.6 * radiance + 0.4 * population`` (default weights), each term being the area's fraction of the
   total over its island's areas. Scores are normalised again into shares that add to one on each
   island. Radiance is the VIIRS composite; population is the WorldPop raster.
3. Within an area, the same formula, using the raster cells nearest each node, splits the area's
   share between its nodes.

Peak and average demand of each island come from
``CEB Annual Report/ceb_demand_levels_by_island_2023_24.csv`` in the incoming energy data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.features import geometry_mask
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import MultiPoint

logger = logging.getLogger(__name__)

GEOGRAPHIC_CRS = "EPSG:4326"
DEFAULT_WEIGHTS = {"nightlights": 0.6, "population": 0.4}
# Lines at or below this voltage are distribution lines. IEC 60038 lists three-phase systems up to 35 kV
# apart from those above it; see config/energy/energy.yaml.
DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV = 35.0
MIN_LENGTH_KM = 1e-6  # for line segments of almost no length, so that every line counts as joined
# Below this share of nodes joined to a supply point by the distribution lines, road_areas warns.
MIN_JOINED_SHARE = 0.9
METHOD_NOTE = (
    "PyPSA-Earth's build_demand_profiles.upsample (0.6 GDP + 0.4 population), "
    "with night-light radiance in place of GDP."
)


@dataclass(frozen=True)
class DemandOutputs:
    service_areas: Path
    substation_weights: Path
    node_weights: Path
    demand_levels: Path
    metadata: Path


def _normalised(values: pd.Series) -> pd.Series:
    total = float(values.sum())
    return values / total if total > 0 else pd.Series(0.0, index=values.index)


def distribution_key(scores: pd.DataFrame, *, weights: dict[str, float] | None = None) -> pd.Series:
    """Combine indicator columns into shares that add to one, as PyPSA-Earth's ``upsample`` does.

    ``scores`` has one row per area or node and one column per indicator (``nightlights``,
    ``population``). Each column is normalised to sum to one, weighted, summed and normalised
    again. All-zero scores give equal shares.
    """
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    unknown = set(weights) - set(scores.columns)
    if unknown:
        raise ValueError(f"weights refer to indicators that are not present: {sorted(unknown)}")
    combined = pd.Series(0.0, index=scores.index)
    for indicator, weight in weights.items():
        combined = combined + float(weight) * _normalised(scores[indicator].astype(float).fillna(0.0))
    if combined.sum() <= 0:
        return pd.Series(1.0 / len(scores), index=scores.index) if len(scores) else combined
    return _normalised(combined)


def supply_points(nodes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the supply points: ``substation`` nodes, or an island's ``generator`` nodes if it has none.

    ``nodes`` needs ``bus_id``, ``kind`` and point geometry; ``region`` separates the islands (one
    island, ``all``, if absent) and is returned as ``island``. Nodes at the same point (a station and
    its rooftop PV) count once.
    """
    kind = nodes["kind"].astype(str)
    region = _islands(nodes)
    chosen = []
    for name, index in nodes.groupby(region, sort=True).groups.items():
        members = nodes.loc[index]
        candidates = members[kind.loc[index].eq("substation").to_numpy()]
        if candidates.empty:
            candidates = members[kind.loc[index].eq("generator").to_numpy()]
        if candidates.empty:
            raise ValueError(f"{name}: no substation or generator node to serve demand from")
        chosen.append(candidates[["bus_id", "geometry"]].assign(island=name))
    points = gpd.GeoDataFrame(pd.concat(chosen), geometry="geometry", crs=nodes.crs)
    points["bus_id"] = points["bus_id"].astype(str)
    return points[~points.geometry.duplicated()].reset_index(drop=True)


def _islands(nodes: gpd.GeoDataFrame) -> pd.Series:
    return nodes["region"].astype(str) if "region" in nodes else pd.Series("all", index=nodes.index)


def road_areas(
    nodes: gpd.GeoDataFrame,
    edges: pd.DataFrame,
    points: gpd.GeoDataFrame,
    *,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
) -> pd.DataFrame:
    """Return the supply point of each node: the one nearest along the distribution lines.

    The distribution lines are the ``edges`` at or below ``max_radial_line_voltage_kv`` (``model_v_nom_kv``),
    measured by ``length_km``. A node that they do not join to any of the supply ``points`` takes the supply
    point nearest in a straight line on its island. One row per node, indexed by ``bus_id``: ``area_bus_id``
    and ``reached`` (whether the distribution lines join the node to its supply point). Warns when they join fewer
    than ``MIN_JOINED_SHARE`` of the nodes, as when the setting is below the voltage of the distribution lines.
    """
    bus_ids = nodes["bus_id"].astype(str).to_numpy()
    position = pd.Series(np.arange(len(bus_ids)), index=bus_ids)
    lines = edges[edges["model_v_nom_kv"].astype(float) <= max_radial_line_voltage_kv]
    lines = lines[lines["bus0"].astype(str).isin(position.index) & lines["bus1"].astype(str).isin(position.index)]
    end0 = position[lines["bus0"].astype(str)].to_numpy()
    end1 = position[lines["bus1"].astype(str)].to_numpy()
    pairs = pd.DataFrame(
        {
            "low": np.minimum(end0, end1),
            "high": np.maximum(end0, end1),
            "km": np.maximum(lines["length_km"].astype(float).to_numpy(), MIN_LENGTH_KM),
        }
    )
    pairs = pairs[pairs["low"] != pairs["high"]].groupby(["low", "high"], as_index=False)["km"].min()
    graph = coo_matrix((pairs["km"], (pairs["low"], pairs["high"])), shape=(len(bus_ids), len(bus_ids))).tocsr()
    sources = position[points["bus_id"].astype(str)].to_numpy()
    distance, _, source = dijkstra(graph, directed=False, indices=sources, min_only=True, return_predecessors=True)
    reached = np.isfinite(distance)
    if len(reached) and reached.mean() < MIN_JOINED_SHARE:
        logger.warning(
            f"The lines at or below max_radial_line_voltage_kv ({max_radial_line_voltage_kv:g} kV) join only "
            f"{reached.mean():.0%} of the nodes to a supply point; the others get no demand. Keep the setting at or "
            "above the voltage of the distribution lines."
        )
    areas = pd.DataFrame(
        {"area_bus_id": np.where(reached, bus_ids[np.maximum(source, 0)], ""), "reached": reached}, index=bus_ids
    )

    if (~reached).any():
        metric_crs = points.estimate_utm_crs()
        island_of_node = pd.Series(_islands(nodes).to_numpy(), index=bus_ids)
        located = nodes.assign(bus_id=bus_ids).to_crs(metric_crs).set_index("bus_id").geometry
        supply = points.to_crs(metric_crs)
        for island, members in supply.groupby("island"):
            lost = areas.index[~areas["reached"] & island_of_node.eq(island).to_numpy()]
            if len(lost):
                tree = shapely.STRtree(members.geometry.to_numpy())
                nearest = tree.query_nearest(located[lost].to_numpy(), all_matches=False)[1]
                areas.loc[lost, "area_bus_id"] = members["bus_id"].to_numpy()[nearest]
    return areas.rename_axis("bus_id")


def service_areas(nodes: gpd.GeoDataFrame, node_areas: pd.DataFrame, outlines: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the service area of each supply point (EPSG:4326): the part of its island nearer to its nodes.

    Each node joined to its supply point (``reached`` in ``node_areas``) owns the part of the island nearer to
    it than to any other such node (its Voronoi cell); a service area joins the cells of its nodes. One row
    per supply point, as ``bus_id``.
    """
    reached = nodes[nodes["bus_id"].astype(str).map(node_areas["reached"]).fillna(False).to_numpy(dtype=bool)]
    metric_crs = reached.estimate_utm_crs()
    points = reached.to_crs(metric_crs)
    points = points.assign(area_bus_id=points["bus_id"].astype(str).map(node_areas["area_bus_id"]).to_numpy())
    points = points[~points.geometry.duplicated()]
    land = shapely.union_all(outlines.to_crs(metric_crs).geometry.to_numpy())
    if len(points) == 1:
        cells = gpd.GeoDataFrame({"area_bus_id": points["area_bus_id"].to_numpy()}, geometry=[land], crs=metric_crs)
    else:
        polygons = np.asarray(shapely.voronoi_polygons(MultiPoint(list(points.geometry)), extend_to=land).geoms)
        point_index, cell_index = shapely.STRtree(polygons).query(points.geometry.to_numpy(), predicate="within")
        cells = gpd.GeoDataFrame(
            {"area_bus_id": points["area_bus_id"].to_numpy()[point_index]},
            geometry=polygons[cell_index],
            crs=metric_crs,
        )
    rows = [
        {"bus_id": area_bus_id, "geometry": shapely.intersection(shapely.union_all(block.geometry.to_numpy()), land)}
        for area_bus_id, block in cells.groupby("area_bus_id", sort=True)
    ]
    areas = gpd.GeoDataFrame(rows, geometry="geometry", crs=metric_crs).to_crs(GEOGRAPHIC_CRS)
    return areas[~areas.geometry.is_empty].reset_index(drop=True)


def _raster_cells(raster_path: Path, outlines: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Return the centre point and value of every positive raster cell (inside ``outlines`` if given)."""
    with rasterio.open(raster_path) as source:
        values = np.ma.filled(source.read(1, masked=True).astype("float64"), 0.0)
        if outlines is not None:
            shapes = list(outlines.to_crs(source.crs).geometry)
            inside = ~geometry_mask(shapes, out_shape=values.shape, transform=source.transform, invert=False)
            values = np.where(inside, values, 0.0)
        rows, cols = np.nonzero(values > 0)
        xs, ys = rasterio.transform.xy(source.transform, rows, cols)
        cells = gpd.GeoDataFrame({"value": values[rows, cols]}, geometry=gpd.points_from_xy(xs, ys), crs=source.crs)
    return cells.to_crs(GEOGRAPHIC_CRS)


def _sum_by_nearest(cells: gpd.GeoDataFrame, targets: gpd.GeoDataFrame) -> np.ndarray:
    """Return one total per ``targets`` row: the sum of the cell values nearest to that point."""
    totals = np.zeros(len(targets))
    if cells.empty or targets.empty:
        return totals
    metric_crs = targets.estimate_utm_crs()
    tree = shapely.STRtree(targets.to_crs(metric_crs).geometry.to_numpy())
    nearest = tree.query_nearest(cells.to_crs(metric_crs).geometry.to_numpy(), all_matches=False)[1]
    np.add.at(totals, nearest, cells["value"].to_numpy())
    return totals


def node_scores(
    nodes: gpd.GeoDataFrame, node_areas: pd.DataFrame, rasters: dict[str, Path], outlines: gpd.GeoDataFrame
) -> pd.DataFrame:
    """Return each node's raster sums from the cells nearest to it, and the service area it is in.

    One row per node, indexed by ``bus_id``: one column per raster and ``area_bus_id``. Each positive cell
    inside ``outlines`` goes to the nearest node joined to its supply point (``reached``); the other nodes
    score zero.
    """
    points = nodes.to_crs(GEOGRAPHIC_CRS)[["bus_id", "geometry"]].copy()
    points["bus_id"] = points["bus_id"].astype(str)
    scores = pd.DataFrame(
        {"area_bus_id": points["bus_id"].map(node_areas["area_bus_id"]).to_numpy()}, index=points["bus_id"].to_numpy()
    )
    reached = points[points["bus_id"].map(node_areas["reached"]).fillna(False).to_numpy(dtype=bool)]
    for indicator, path in rasters.items():
        totals = pd.Series(0.0, index=scores.index)
        totals.loc[reached["bus_id"].to_numpy()] = _sum_by_nearest(_raster_cells(path, outlines), reached)
        scores[indicator] = totals.to_numpy()
    return scores


def area_scores(node_score_frame: pd.DataFrame, supply_point_ids) -> pd.DataFrame:
    """Return each service area's raster sums, the sums of its nodes: one row per supply point bus_id."""
    indicators = [column for column in node_score_frame.columns if column != "area_bus_id"]
    sums = node_score_frame.groupby("area_bus_id")[indicators].sum()
    return sums.reindex(pd.Index(supply_point_ids, dtype=str)).fillna(0.0)


def node_shares(
    node_score_frame: pd.DataFrame,
    area_shares: pd.Series,
    *,
    weights: dict[str, float] | None = None,
    area_islands: pd.Series | None = None,
) -> pd.Series:
    """Split each area's share between its nodes with :func:`distribution_key`.

    The result adds to one on each island of ``area_islands`` (area bus_id to island), or over all
    nodes if it is not given.
    """
    indicators = [column for column in node_score_frame.columns if column != "area_bus_id"]
    shares = pd.Series(0.0, index=node_score_frame.index)
    for area_bus_id, block in node_score_frame.groupby("area_bus_id"):
        area_share = float(area_shares.get(str(area_bus_id), 0.0))
        shares.loc[block.index] = distribution_key(block[indicators], weights=weights) * area_share
    if area_islands is None:
        return _normalised(shares)
    islands = node_score_frame["area_bus_id"].astype(str).map(area_islands)
    return shares.groupby(islands.to_numpy()).transform(_normalised)


def _read_outlines(path: Path) -> gpd.GeoDataFrame:
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def build_demand_shares(
    *,
    nodes_path: Path,
    edges_path: Path,
    aoi_path: Path,
    population_path: Path,
    nightlights_path: Path,
    demand_levels_path: Path,
    output_dir: Path,
    weights: dict[str, float] | None = None,
    max_radial_line_voltage_kv: float = DEFAULT_MAX_RADIAL_LINE_VOLTAGE_KV,
) -> DemandOutputs:
    """Write one network's service areas, demand shares and demand levels to ``output_dir``.

    ``nodes_path`` and ``edges_path`` are the network's node and edge GeoParquet layers.
    ``demand_levels_path`` has ``level``, ``island`` and ``demand_mw`` for every island. Writes
    ``service_areas.geoparquet``, ``service_weights_substations.csv``, ``service_weights_nodes.csv``,
    ``demand_levels.csv`` (a copy of ``demand_levels_path``) and ``metadata.json``. Shares add to one on
    each island.
    """
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    nodes = gpd.read_parquet(nodes_path)
    if "bus_id" not in nodes or "kind" not in nodes:
        raise ValueError("nodes layer must contain bus_id and kind")
    edges = pd.read_parquet(edges_path, columns=["bus0", "bus1", "model_v_nom_kv", "length_km"])
    rasters = {"population": Path(population_path), "nightlights": Path(nightlights_path)}
    levels = pd.read_csv(demand_levels_path, comment="#")
    missing_columns = {"level", "island", "demand_mw"} - set(levels.columns)
    if missing_columns:
        raise ValueError(f"{demand_levels_path}: missing columns {sorted(missing_columns)}")

    points = supply_points(nodes)
    area_islands = points.set_index("bus_id")["island"]
    missing_islands = set(area_islands) - set(levels["island"].astype(str))
    if missing_islands:
        raise ValueError(f"{demand_levels_path}: no demand levels for {sorted(missing_islands)}")
    outlines = _read_outlines(aoi_path)
    node_areas = road_areas(nodes, edges, points, max_radial_line_voltage_kv=max_radial_line_voltage_kv)
    areas = service_areas(nodes, node_areas, outlines)
    areas["island"] = areas["bus_id"].map(area_islands)
    scores = node_scores(nodes, node_areas, rasters, outlines)
    score_frame = area_scores(scores, points["bus_id"])
    area_key = pd.concat(
        distribution_key(block, weights=weights)
        for _, block in score_frame.groupby(score_frame.index.map(area_islands).to_numpy())
    ).reindex(score_frame.index)
    reached = node_areas["reached"].reindex(scores.index).to_numpy(dtype=bool)
    shares = node_shares(scores[reached], area_key, weights=weights, area_islands=area_islands)
    shares = shares.reindex(scores.index).fillna(0.0)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = DemandOutputs(
        service_areas=output_dir / "service_areas.geoparquet",
        substation_weights=output_dir / "service_weights_substations.csv",
        node_weights=output_dir / "service_weights_nodes.csv",
        demand_levels=output_dir / "demand_levels.csv",
        metadata=output_dir / "metadata.json",
    )
    area_weight = area_key.rename("service_weight")
    areas.merge(area_weight, left_on="bus_id", right_index=True).to_parquet(outputs.service_areas)
    area_table = area_weight.rename_axis("bus_id").reset_index()
    area_table.insert(1, "island", area_table["bus_id"].map(area_islands))
    area_table.to_csv(outputs.substation_weights, index=False)
    node_table = pd.DataFrame(
        {
            "bus_id": shares.index,
            "service_weight": shares.to_numpy(),
            "area_bus_id": scores["area_bus_id"].to_numpy(),
            "island": scores["area_bus_id"].astype(str).map(area_islands).to_numpy(),
        }
    )
    node_table.to_csv(outputs.node_weights, index=False)
    levels.to_csv(outputs.demand_levels, index=False)
    metadata = {
        "method": "road_distance_service_areas_with_population_and_nightlight_key",
        "method_note": METHOD_NOTE,
        "weights": weights,
        "max_radial_line_voltage_kv": max_radial_line_voltage_kv,
        "nodes": str(nodes_path),
        "edges": str(edges_path),
        "aoi": str(aoi_path),
        "population_raster": str(population_path),
        "nightlights_raster": str(nightlights_path),
        "demand_levels": levels.to_dict("records"),
        "service_areas": len(areas),
        "nodes_joined_to_a_supply_point": int(node_areas["reached"].sum()),
        "nodes_not_joined": int((~node_areas["reached"]).sum()),
        "nodes_with_share": int((shares > 0).sum()),
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return outputs
