"""Journeys to school per level, with the radiation model (draft).

Origins: enrolled school-age children by H3 cell (transport_school_children).
Destinations: school places (enrolment, or capacity) per H3 cell, from the
island's schools file: school_id, name, level (primary|secondary),
enrolment, geometry. Schools outside the island's zone cells are moved to
the nearest zone.

Per level, children are distributed to school places with
transport_flow_model.radiation_flows (row-normalised: every child makes one
trip), on the same road skim as journeys to work, with
school.radiation.max_impedance and include_self (a school in the child's own
cell). School places are scaled to the number of children per level.

Output: origin_zone, destination_zone, level, trips (between cells; trips
within a cell are reported in the summary, not assigned).
"""

import json

import click
import geopandas
import h3
import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from transport_flow_model import radiation_flows

from transport.land import h3_resolution, read_config

HEXAGON_MEAN_DISTANCE = 0.8255


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", required=True)
@click.option("--zoning", required=True)
@click.option("--zones", required=True)
@click.option("--children", "children_path", required=True)
@click.option("--schools", required=True)
@click.option("--skim", required=True)
@click.option("--output", required=True)
@click.option("--output-summary", required=True)
def main(config_path, island, zoning, zones, children_path, schools, skim, output, output_summary):
    cfg = read_config(config_path, island=island)
    school = cfg["school"]
    zones = geopandas.read_parquet(zones)[["zone_id", "geometry"]]
    ids = pd.Index(zones["zone_id"])
    z = len(ids)

    skim = pd.read_parquet(skim)
    impedance = np.full((z, z), np.inf)
    impedance[ids.get_indexer(skim.origin_zone), ids.get_indexer(skim.destination_zone)] = skim["time_min"]
    within_km = HEXAGON_MEAN_DISTANCE * h3.average_hexagon_edge_length(h3_resolution(zoning), unit="km")
    np.fill_diagonal(impedance, within_km / cfg["connectors"]["car_speed_kph"] * 60.0)

    children = pd.read_parquet(children_path)
    children = children[children["h3_index"].isin(ids)]

    places = geopandas.read_file(schools).to_crs(cfg["crs_metric"])
    places["places"] = pd.to_numeric(places.get("enrolment"), errors="coerce").fillna(
        pd.to_numeric(places.get("capacity"), errors="coerce")
    )
    to_wgs84 = Transformer.from_crs(cfg["crs_metric"], "EPSG:4326", always_xy=True)
    lon, lat = to_wgs84.transform(places.geometry.x.to_numpy(), places.geometry.y.to_numpy())
    places["zone_id"] = [h3.latlng_to_cell(y, x, h3_resolution(zoning)) for x, y in zip(lon, lat)]
    outside = ~places["zone_id"].isin(ids)
    if outside.any():
        tree = cKDTree(np.c_[zones.geometry.x, zones.geometry.y])
        nearest = tree.query(np.c_[places.geometry.x[outside], places.geometry.y[outside]])[1]
        places.loc[outside, "zone_id"] = ids[nearest]

    rows, summary = [], {}
    for level in school["levels"]:
        kids = children[children.level == level].set_index("h3_index")["children"].reindex(ids).fillna(0.0).to_numpy()
        seats = places[places.level == level].groupby("zone_id")["places"].sum().reindex(ids).fillna(0.0).to_numpy()
        if kids.sum() == 0 or seats.sum() == 0:
            summary[level] = {"children": float(kids.sum()), "places": float(seats.sum()), "note": "no trips"}
            continue
        seats = seats * kids.sum() / seats.sum()  # same units as the origin mass
        trips = radiation_flows(
            impedance,
            kids,
            kids,
            seats,
            max_impedance=school["radiation"]["max_impedance"],
            normalisation="row",
            include_self=school["radiation"]["include_self"],
        )
        within = float(np.trace(trips))
        np.fill_diagonal(trips, 0.0)
        o, d = np.nonzero(trips)
        rows.append(
            pd.DataFrame({"origin_zone": ids[o], "destination_zone": ids[d], "level": level, "trips": trips[o, d]})
        )
        summary[level] = {
            "children": float(kids.sum()),
            "places": float(places[places.level == level]["places"].sum()),
            "within_cell_trips": within,
            "between_cell_trips": float(trips.sum()),
        }
    od = (
        pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(columns=["origin_zone", "destination_zone", "level", "trips"])
    )
    od.to_parquet(output, index=False)
    with open(output_summary, "w") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
