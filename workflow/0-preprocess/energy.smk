rule assign_generator_types:
    """
    Join generator types (e.g. solar, hydro, thermal) to wider energy node set.

    Test with:
        snakemake -c1 data/processed/asset/energy/inferred-data-mauritius-rodrigues/geoparquet/inferred-data-mauritius-rodrigues-nodes-with-gen-type.geoparquet
    """
    input:
        all_nodes = "{data}/incoming/Infrastructure/Energy/inferred-distribution-mauritius-rodrigues/20260728/geoparquet/inferred-data-mauritius-rodrigues-nodes.geoparquet",
        generators = "{data}/incoming/Infrastructure/Energy/inferred-distribution-mauritius-rodrigues/20260728/generators.csv",
    output:
        join = "{data}/processed/asset/energy/inferred-data-mauritius-rodrigues/geoparquet/inferred-data-mauritius-rodrigues-nodes-with-gen-type.geoparquet"
    run:
        import geopandas as gpd
        import pandas as pd

        nodes = gpd.read_parquet(input.all_nodes).set_index("asset_id")
        gen = pd.read_csv(input.generators)
        gen["generator_id"] = gen["generator_id"].apply(lambda s: f"asset::{s}")
        # Overwrite any matching columns with `gen`
        nodes.update(gen.set_index("generator_id"))
        nodes.reset_index().to_parquet(output.join)
