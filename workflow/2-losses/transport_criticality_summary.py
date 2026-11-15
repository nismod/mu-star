"""Combine criticality batches into one ranked table per failed asset, with geometry.

Columns: scenario_id, asset_type, asset_id, road_class, baseline_flow, the
scenario-impact totals (see transport_criticality_batch.py), rank_cost and
rank_isolated, plus the same impacts as a share of the baseline totals.
"""

import json

import click
import geopandas
import pandas as pd

from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--failure-sets", required=True)
@click.option("--road-edges", required=True)
@click.option("--metro-segments", required=True)
@click.option("--metro-stations", required=True)
@click.option("--baseline-summary", required=True)
@click.option("--output-table", required=True)
@click.option("--output-spatial", required=True)
@click.argument("scenario_impacts", nargs=-1, required=True)
def main(
    config_path,
    failure_sets,
    road_edges,
    metro_segments,
    metro_stations,
    baseline_summary,
    output_table,
    output_spatial,
    scenario_impacts,
):
    cfg = read_config(config_path)
    impacts = pd.concat([pd.read_parquet(path) for path in scenario_impacts], ignore_index=True)
    # Batches without any mode shift have no shifted_to_<mode> columns.
    shift_columns = [c for c in impacts.columns if c.startswith("shifted_to_")]
    impacts[shift_columns] = impacts[shift_columns].fillna(0.0)
    sets = pd.read_parquet(failure_sets).drop(columns=["link_ids"])
    table = sets.merge(impacts, on="scenario_id", how="left")

    with open(baseline_summary) as fh:
        baseline = json.load(fh)
    base_cost = sum(mode["cost_total"] for mode in baseline["by_mode"].values())
    base_time = sum(mode["time_min_total"] for mode in baseline["by_mode"].values())
    table["delta_cost_share"] = table["delta_cost_total"] / base_cost
    table["delta_time_share"] = table["delta_time_min_total"] / base_time
    table["rank_cost"] = table["delta_cost_total"].rank(ascending=False, method="min")
    table["rank_isolated"] = table["isolated_flow"].rank(ascending=False, method="min")
    table["currency"] = cfg["cost"]["currency"]
    table = table.sort_values("rank_cost")
    table.to_csv(output_table, index=False)

    roads = geopandas.read_parquet(road_edges)[["edge_id", "geometry"]].rename(columns={"edge_id": "asset_id"})
    roads["asset_type"] = "road"
    metro = geopandas.read_parquet(metro_segments).query("direction == 0")[["segment_id", "geometry"]]
    metro = metro.rename(columns={"segment_id": "asset_id"}).assign(asset_type="metro")
    stations = geopandas.read_parquet(metro_stations).drop_duplicates("station_id")[["station_id", "geometry"]]
    stations = stations.rename(columns={"station_id": "asset_id"}).assign(asset_type="metro_station")
    shapes = pd.concat([roads, metro, stations], ignore_index=True)
    spatial = geopandas.GeoDataFrame(shapes.merge(table, on=["asset_type", "asset_id"]), crs=roads.crs)
    spatial.to_parquet(output_spatial)


if __name__ == "__main__":
    main()
