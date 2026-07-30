"""
Collate asset data to visualise in irv-standalone
"""

UID_RANGE = 100_000_000  # We do not expect more features than this per input file

# This dict has one key/value per row of irv-standalone::etl/network_layers.csv
# As processes to create processed networks are added to this repository, the
# paths in ASSET_SOURCES can be updated to the processed networks (rather than
# the raw ones).
ASSET_SOURCES = {
    # (sector, subsector, {node|edge|area}): (starting UID, path)
    # ("transport", "rail", "node"): (0 * UID_RANGE, "incoming/Infrastructure/Light Rail Station/LRT_station.shp"),
    # ("transport", "rail", "edge"): (1 * UID_RANGE, "incoming/Infrastructure/Light Rail/Lightrail.shp"),
    # ("transport", "airport", "area"): (2 * UID_RANGE, "incoming/Infrastructure/Airport/Airport_area.shp"),
    # ("transport", "port", "area"): (3 * UID_RANGE, "?"),
    # ("water", "irrigation", "node"): (4 * UID_RANGE, "?"),
    # ("water", "irrigation", "edge"): (5 * UID_RANGE, "?"),
    ("water", "potable", "node"): (6 * UID_RANGE, "processed/asset/water/potable/node.gpq"),
    ("water", "waste", "node"): (7 * UID_RANGE, "processed/asset/water/waste/node.gpq"),
    # N.B. Original irv-jamaica schema had one edge layer for both waste and potable water
    # ("water", "?", "edge"): (8 * UID_RANGE, "?"),
    ("transport", "road", "node"): (9 * UID_RANGE, "incoming/Infrastructure/Road Network/osm-open-gira/nodes.gpq"),
    ("transport", "road", "edge"): (10 * UID_RANGE, "incoming/Infrastructure/Road Network/osm-open-gira/edges.gpq"),
    ("energy", "transmission", "node"): (11 * UID_RANGE, "processed/asset/energy/inferred-data-mauritius-rodrigues/geoparquet/inferred-data-mauritius-rodrigues-nodes-with-gen-type.geoparquet"),
    ("energy", "transmission", "edge"): (12 * UID_RANGE, "processed/asset/energy/inferred-data-mauritius-rodrigues/geoparquet/inferred-data-mauritius-rodrigues-edges.geoparquet"),
    # ("buildings", "buildings", "area"): (13 * UID_RANGE, "?"),
}


def _read_geo(path: str):
    """Read a geospatial vector dataset, returning a GeoDataFrame."""
    import geopandas as gpd
    if str(path).endswith((".gpq", ".pq", ".geoparquet", ".parquet")):
        return gpd.read_parquet(path)
    return gpd.read_file(path)


rule copy_and_label_asset:
    """
    Rewrite infrastructure layer to GeoParquet, label with unique ID for database.

    Test with:
        snakemake -c1 data/visualise/asset/transport/airport/area.gpq
    """
    input:
        src = lambda wc: "{data}/{src}".format(data=wc.data, src=ASSET_SOURCES[(wc.sector, wc.layer, wc.geom_type)][1]),
    output:
        gpq = "{data}/visualise/asset/{sector}/{layer}/{geom_type}.gpq",
    wildcard_constraints:
        sector = r"transport|energy|water|buildings",
        layer = r"[\w-]+",
        geom_type = r"node|edge|area",
    run:
        import numpy as np 

        uid_start, path = ASSET_SOURCES[(wildcards.sector, wildcards.layer, wildcards.geom_type)]
        df = _read_geo(input.src)
        assert "uid" not in df.columns
        assert len(df) < 1 * UID_RANGE
        df["uid"] = np.arange(len(df)) + uid_start
        df.to_parquet(output.gpq)


rule copy_all_assets:
    """
    Convenience target: copy every visualisation asset layer.

    Test with:
        snakemake -c1 data/visualise/asset/.flag
    """
    input:
        expand(
            "{data}/visualise/asset/{sector}/{layer}/{geom_type}.gpq",
            zip,
            sector = [s for (s, l, g) in ASSET_SOURCES],
            layer = [l for (s, l, g) in ASSET_SOURCES],
            geom_type = [g for (s, l, g) in ASSET_SOURCES],
            allow_missing=True,
        )
    output:
        flag = "{data}/visualise/asset/.flag"
    shell:
        """
        touch {output.flag}
        """
