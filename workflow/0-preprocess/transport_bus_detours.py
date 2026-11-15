"""Bus detours, skipped stops and cut segments for each road edge that buses use.

One road edge fails at a time (method: src/transport/detours.py). Uses the
base config for the island: criticality.bus_detour and bus.speed_factor are
not varied by scenarios (transport.land.SCENARIO_FIXED), so every scenario's
multimodal network reads the same detours.

Output: detours.parquet, one row per failed road edge that buses use
(transport.detours.DETOUR_SCHEMA):
    road_edge_id    str
    changes         list of {segment_id: str, time_min: float, length_km: float}:
                    bus segments that stay, with their detoured time and length
    skipped_stops   list of {route_id: str, direction: int, stop_id: str}:
                    stops that lose boarding and alighting on that route direction
    cut_segments    list of str: segment ids removed (route cut there)
"""

import click
import geopandas
import pyarrow.parquet as pq

from transport.detours import bus_detours, detours_table
from transport.land import read_config


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--island", default=None)
@click.option("--road-edges", required=True)
@click.option("--bus-stops", required=True)
@click.option("--bus-segments", required=True)
@click.option("--output", required=True)
def main(config_path, island, road_edges, bus_stops, bus_segments, output):
    cfg = read_config(config_path, island=island)
    detour_cfg = cfg["criticality"]["bus_detour"]
    roads = geopandas.read_parquet(road_edges)
    stops = geopandas.read_parquet(bus_stops)
    bus = geopandas.read_parquet(bus_segments)

    bus_road_edges = sorted({e for ids in bus["road_edge_ids"] for e in ids})
    detours = bus_detours(
        roads,
        bus,
        stops.set_index("stop_id")["road_node"].to_dict(),
        bus_road_edges,
        speed_factor=cfg["bus"]["speed_factor"],
        max_skipped_stops=detour_cfg["max_skipped_stops"],
        max_detour_factor=detour_cfg["max_detour_factor"],
    )
    pq.write_table(detours_table(detours), output)
    cut = sum(bool(d.cut_segments) for d in detours.values())
    print(f"{len(detours)} road edges used by buses; {cut} cut a route")


if __name__ == "__main__":
    main()
