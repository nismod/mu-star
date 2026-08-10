"""
Process building footprints and their economic classification.
"""

rule label_building_category:
    """
    Apply a map from ISIC classification to building type.

    Test with:
    snakemake -c1 data/processed/building/building.gpq
    """
    input:
        raw = "{data}/incoming/Socio-economic/Mauritius_Building_Database ISIC Clasification/Mauritius_Building_Database ISIC Clasification.shp"
    output:
        proc = "{data}/processed/asset/building/building.gpq"
    run:
        import geopandas as gpd

        category_l_to_vis_category = {
            "Residential": "Residential",

            "Section O - Public Administration and Defence": "Institutional",
            "Section O - Public Administration and Defence; Compulsory Social Security": "Institutional",
            "Section P - Education": "Institutional",
            "Section Q - Human Health and Social Work Activities": "Institutional",
            "Assembly": "Institutional",

            "Section I - Accommodation and Food Service Activities": "Recreation",
            "Section R - Arts, Entertainment and Recreation": "Recreation",

            "Section G - Wholesale and Retail Trade; Repair of Motor Vehicles": "Commercial",
            "Section L - Real Estate Activities": "Commercial",
            "Section M - Professional, Scientific and Technical Activities": "Commercial",
            "Section N - Administrative and Support Service Activities": "Commercial",
            "Section K - Financial and Insurance Activities": "Commercial",
            "Section J - Information and Communication": "Commercial",

            "Section A - Agriculture, Forestry and Fishing": "Other",
            "Section B - Mining and Quarrying": "Other",
            "Section S - Other Service Activities": "Other",

            "Mixed Use": "Mixed Use",

            "Section C - Manufacturing": "Industrial",
            "Section D - Electricity, Gas, Steam and Air Conditioning Supply": "Industrial",
            "Section E - Water Supply; Sewerage, Waste Management and Remediation": "Industrial",
            "Section F - Construction": "Industrial",
            "Section H - Transportation and Storage": "Industrial",

            "Unmatched (no ISIC data yet)": "Unclassified",
            "Not Classified": "Unclassified",
            "": "Unclassified",
        }
        df = gpd.read_file(input.raw)
        df = df.drop_duplicates("geometry")
        # irv-standalone expects, area_sqm, name and building_type columns
        df["area_sqm"] = df.to_crs(df.estimate_utm_crs()).geometry.area
        df = df.rename(columns={"Building_N": "name"})
        df["building_type"] = "Unclassified"
        df["building_type"] = df["Category_L"].map(category_l_to_vis_category)
        df.to_parquet(output.proc)

