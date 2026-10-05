"""Estimate each substation's and each network node's share of system demand.

The method is PyPSA-Earth's ``build_demand_profiles.upsample`` (0.6 GDP + 0.4 population), with
VIIRS night-light radiance in place of GDP as the measure of economic activity: a GDP raster is
too coarse for a 50 km island.

1. Each substation serves the area nearer to it than to any other substation (a Voronoi cell
   clipped to the island outline). An island with no substation (Rodrigues) is served from its
   power stations; an island with one supply point is one area.
2. An area's score is ``0.6 * radiance + 0.4 * population`` (default weights), each term being the
   area's fraction of the total over all areas. Scores are normalised again into shares that add to
   one. Radiance is the VIIRS composite; population is the WorldPop raster.
3. Within an area, the same formula, using the raster cells nearest each node, splits the area's
   share between its nodes.

Peak and average system demand come from ``CEB Annual Report/ceb_demand_levels_2023_24.csv`` in
the incoming energy data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.features import geometry_mask
from shapely.geometry import MultiPoint

GEOGRAPHIC_CRS = "EPSG:4326"
DEFAULT_WEIGHTS = {"nightlights": 0.6, "population": 0.4}
METHOD_NOTE = (
    "Adapted from PyPSA-Earth build_demand_profiles.upsample (0.6 GDP + 0.4 population); "
    "night-light radiance replaces GDP."
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
    island if absent). Nodes at the same point (a station and its rooftop PV) count once.
    """
    kind = nodes["kind"].astype(str)
    region = nodes["region"].astype(str) if "region" in nodes else pd.Series("all", index=nodes.index)
    chosen = []
    for name, index in nodes.groupby(region, sort=True).groups.items():
        members = nodes.loc[index]
        candidates = members[kind.loc[index].eq("substation").to_numpy()]
        if candidates.empty:
            candidates = members[kind.loc[index].eq("generator").to_numpy()]
        if candidates.empty:
            raise ValueError(f"{name}: no substation or generator node to serve demand from")
        chosen.append(candidates[["bus_id", "geometry"]])
    points = gpd.GeoDataFrame(pd.concat(chosen), geometry="geometry", crs=nodes.crs)
    return points[~points.geometry.duplicated()].reset_index(drop=True)


def service_areas(supply_points: gpd.GeoDataFrame, outlines: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return the Voronoi cell of each supply point, clipped to its island outline (EPSG:4326).

    ``outlines`` are the island polygons of the OSM area of interest. A point outside every
    outline belongs to the nearest one; an island with one supply point is a single area.
    """
    points = supply_points.to_crs(GEOGRAPHIC_CRS)
    island_shapes = list(outlines.to_crs(GEOGRAPHIC_CRS).geometry)
    nearest_island = shapely.STRtree(island_shapes).query_nearest(points.geometry.to_numpy(), all_matches=False)[1]
    rows = []
    for island_index in sorted(set(nearest_island.tolist())):
        island = island_shapes[island_index]
        members = points.iloc[np.flatnonzero(nearest_island == island_index)]
        if len(members) == 1:
            rows.append({"bus_id": str(members["bus_id"].iloc[0]), "geometry": island})
            continue
        cells = list(shapely.voronoi_polygons(MultiPoint(list(members.geometry)), extend_to=island).geoms)
        cell_tree = shapely.STRtree(cells)
        for bus_id, point in zip(members["bus_id"].astype(str), members.geometry, strict=True):
            hits = cell_tree.query(point, predicate="within")
            cell_index = int(hits[0]) if len(hits) else int(cell_tree.query_nearest(point)[0])
            rows.append({"bus_id": bus_id, "geometry": cells[cell_index].intersection(island)})
    areas = gpd.GeoDataFrame(rows, geometry="geometry", crs=GEOGRAPHIC_CRS)
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


def area_scores(areas: gpd.GeoDataFrame, rasters: dict[str, Path]) -> pd.DataFrame:
    """Sum each raster over each service area: one column per indicator, one row per bus_id."""
    scores = pd.DataFrame(index=areas["bus_id"].astype(str))
    for indicator, path in rasters.items():
        cells = _raster_cells(path, areas)
        joined = gpd.sjoin(cells, areas[["bus_id", "geometry"]], how="inner", predicate="within")
        sums = joined.groupby(joined["bus_id"].astype(str))["value"].sum()
        scores[indicator] = sums.reindex(scores.index).fillna(0.0)
    return scores


def node_scores(nodes: gpd.GeoDataFrame, areas: gpd.GeoDataFrame, rasters: dict[str, Path]) -> pd.DataFrame:
    """Return each node's raster sums from the cells nearest to it, and the service area it is in.

    One row per node, indexed by ``bus_id``: one column per raster and ``area_bus_id``. A node
    outside every area joins the nearest one.
    """
    points = nodes.to_crs(GEOGRAPHIC_CRS)[["bus_id", "geometry"]].copy()
    points["bus_id"] = points["bus_id"].astype(str)
    area_lookup = areas[["bus_id", "geometry"]].rename(columns={"bus_id": "area_bus_id"})
    located = gpd.sjoin(points, area_lookup, how="left", predicate="within")
    located = located[~located.index.duplicated(keep="first")]
    outside = located["area_bus_id"].isna()
    if outside.any():
        metric_crs = areas.estimate_utm_crs()
        tree = shapely.STRtree(areas.to_crs(metric_crs).geometry.to_numpy())
        outside_points = located.loc[outside].to_crs(metric_crs).geometry.to_numpy()
        nearest = tree.query_nearest(outside_points, all_matches=False)[1]
        located.loc[outside, "area_bus_id"] = areas["bus_id"].astype(str).to_numpy()[nearest]
    scores = pd.DataFrame(
        {"area_bus_id": located["area_bus_id"].astype(str).to_numpy()},
        index=located["bus_id"].to_numpy(),
    )
    # Each cell goes to the nearest node in its own service area, not one across the boundary.
    area_lookup = areas[["bus_id", "geometry"]].rename(columns={"bus_id": "area_bus_id"})
    for indicator, path in rasters.items():
        cells = gpd.sjoin(_raster_cells(path, areas), area_lookup, how="inner", predicate="within")
        totals = pd.Series(0.0, index=scores.index)
        for area_bus_id, area_cells in cells.groupby("area_bus_id"):
            area_nodes = located[located["area_bus_id"].astype(str).eq(str(area_bus_id))]
            if area_nodes.empty:
                continue
            totals.loc[area_nodes["bus_id"].to_numpy()] += _sum_by_nearest(area_cells, area_nodes)
        scores[indicator] = totals.to_numpy()
    return scores


def node_shares(
    node_score_frame: pd.DataFrame,
    area_shares: pd.Series,
    *,
    weights: dict[str, float] | None = None,
) -> pd.Series:
    """Split each area's share between its nodes with :func:`distribution_key`; the result adds to one."""
    indicators = [column for column in node_score_frame.columns if column != "area_bus_id"]
    shares = pd.Series(0.0, index=node_score_frame.index)
    for area_bus_id, block in node_score_frame.groupby("area_bus_id"):
        area_share = float(area_shares.get(str(area_bus_id), 0.0))
        shares.loc[block.index] = distribution_key(block[indicators], weights=weights) * area_share
    return _normalised(shares) if shares.sum() > 0 else shares


def _read_outlines(path: Path) -> gpd.GeoDataFrame:
    path = Path(path)
    if path.suffix.lower() in {".parquet", ".geoparquet"}:
        return gpd.read_parquet(path)
    return gpd.read_file(path)


def build_demand_shares(
    *,
    nodes_path: Path,
    aoi_path: Path,
    population_path: Path,
    nightlights_path: Path,
    demand_levels_path: Path,
    output_dir: Path,
    weights: dict[str, float] | None = None,
) -> DemandOutputs:
    """Write one network's service areas, demand shares and demand levels to ``output_dir``.

    ``nodes_path`` is the network's node GeoParquet layer. Writes ``service_areas.geoparquet``,
    ``service_weights_substations.csv``, ``service_weights_nodes.csv``, ``demand_levels.csv`` (copied
    from ``demand_levels_path``) and ``metadata.json``.
    """
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    nodes = gpd.read_parquet(nodes_path)
    if "bus_id" not in nodes or "kind" not in nodes:
        raise ValueError("nodes layer must contain bus_id and kind")
    rasters = {"population": Path(population_path), "nightlights": Path(nightlights_path)}

    areas = service_areas(supply_points(nodes), _read_outlines(aoi_path))
    area_key = distribution_key(area_scores(areas, rasters), weights=weights)
    scores = node_scores(nodes[["bus_id", "geometry"]], areas, rasters)
    shares = node_shares(scores, area_key, weights=weights)
    levels = pd.read_csv(demand_levels_path, comment="#")

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
    area_weight.rename_axis("bus_id").reset_index().to_csv(outputs.substation_weights, index=False)
    node_table = pd.DataFrame(
        {"bus_id": shares.index, "service_weight": shares.to_numpy(), "area_bus_id": scores["area_bus_id"].to_numpy()}
    )
    node_table.to_csv(outputs.node_weights, index=False)
    levels.to_csv(outputs.demand_levels, index=False)
    metadata = {
        "method": "voronoi_service_areas_with_population_and_nightlight_key",
        "method_note": METHOD_NOTE,
        "weights": weights,
        "nodes": str(nodes_path),
        "aoi": str(aoi_path),
        "population_raster": str(population_path),
        "nightlights_raster": str(nightlights_path),
        "demand_levels": levels.to_dict("records"),
        "service_areas": len(areas),
        "nodes_with_share": int((shares > 0).sum()),
    }
    outputs.metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    return outputs
