"""
Preprocess/clean water data prior to analysis.
"""


rule label_water_nodes:
    """
    Add an asset_type and node_id fields to water asset nodes.

    Test with:
    snakemake -c1 data/processed/asset/water/potable/node.gpq
    """
    input:
        res = "{data}/incoming/Infrastructure/Reservoir/Reservoir.shp",
        wtw = "{data}/incoming/Infrastructure/Water Treatment/WaterTreatment.shp",
        wwtw = "{data}/incoming/Infrastructure/Wastewater Treatment Plant/WWTreatmentP.shp",
    output:
        res = "{data}/processed/asset/water/potable/area.gpq",
        wtw = "{data}/processed/asset/water/potable/node.gpq",
        wwtw = "{data}/processed/asset/water/waste/node.gpq",
    run:
        import geopandas as gpd
        import pandas as pd

        labels = {
            "res": "reservoir",
            "wtw": "water_treatment_works",
            "wwtw": "waste_water_treatment_works",
        }
        data = {}
        for short_code in dict(input).keys():
            df = gpd.read_file(input[short_code])
            df["asset_type"] = labels[short_code]
            df["id"] = [f"{short_code}_{i}" for i in range(len(df))]
            df.to_parquet(output[short_code])
