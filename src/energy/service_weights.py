"""Share demand between substations by the length of distribution lines nearest to each.

A line segment counts toward the substation nearest its centroid (EPSG:32740). With no lines, the
shares are equal. The lines only set the shares; they are not added to the network.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

METRIC_CRS = "EPSG:32740"


def build_service_weights(
    substations: gpd.GeoDataFrame,
    *,
    precomputed_lines: gpd.GeoDataFrame | None = None,
    osm_distribution_lines: gpd.GeoDataFrame | None = None,
) -> pd.DataFrame:
    """Return each substation's ``service_weight`` (shares adding to one) and its km of nearest lines by source."""
    buses = substations.to_crs(METRIC_CRS)
    sources: list[gpd.GeoDataFrame] = []
    for name, layer in (
        ("precomputed", precomputed_lines),
        ("osm", osm_distribution_lines),
    ):
        if layer is None or layer.empty:
            continue
        prepared = layer.to_crs(METRIC_CRS).explode(index_parts=False).copy()
        prepared = prepared[prepared.geometry.geom_type.eq("LineString")]
        prepared["source"] = name
        prepared["length_km"] = prepared.length / 1000
        sources.append(prepared[["source", "length_km", "geometry"]])

    if not sources:
        weight = 1 / len(buses) if len(buses) else np.nan
        return pd.DataFrame(
            {
                "bus_id": buses["bus_id"],
                "precomputed_km": 0.0,
                "osm_km": 0.0,
                "service_weight": weight,
                "method": "equal_no_distribution_proxy",
            }
        )

    segments = gpd.GeoDataFrame(pd.concat(sources, ignore_index=True), crs=METRIC_CRS)
    allocated: list[dict[str, object]] = []
    for _, segment in segments.iterrows():
        distances = buses.geometry.distance(segment.geometry.centroid)
        bus_index = distances.idxmin()
        allocated.append(
            {
                "bus_id": buses.at[bus_index, "bus_id"],
                "source": segment["source"],
                "length_km": segment["length_km"],
            }
        )

    lengths = (
        pd.DataFrame(allocated)
        .pivot_table(index="bus_id", columns="source", values="length_km", aggfunc="sum")
        .reindex(buses["bus_id"])
        .fillna(0.0)
    )
    for column in ("precomputed", "osm"):
        if column not in lengths:
            lengths[column] = 0.0
    total = lengths[["precomputed", "osm"]].sum(axis=1)
    if total.sum() == 0:
        weights = pd.Series(1 / len(total), index=total.index)
    else:
        weights = total / total.sum()
    return pd.DataFrame(
        {
            "bus_id": lengths.index,
            "precomputed_km": lengths["precomputed"].values,
            "osm_km": lengths["osm"].values,
            "service_weight": weights.values,
            "method": "distribution_line_length_proxy",
        }
    )
