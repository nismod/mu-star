"""
JBA Risk Management flood hazard data.

Return period maps for pluvial, fluvial and coastal flooding, all at 1 arcsecond
/ 30m resolution.

See incoming/Hazards/JBA Flood Hazard/2026s0679_metadata_docs for JBA
methodology documentation and additional metadata.

This file contains rules to transform raw flooding data into analysis ready
datasets.
"""


rule collate_JBA_fluvial_flooding:
    """
    Collate JBA fluvial flood rasters, label with metadata, write as Zarr
    store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-fluvial/jba.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-fluvial/jba.zarr"),
    run:
        from pathlib import Path

        from mu_star.hazard import collate_jba_flood_rasters

        collate_jba_flood_rasters(sorted(Path(input.raster_dir).glob("*")), output.cube, "FLRF")


rule collate_JBA_pluvial_flooding:
    """
    Collate JBA pluvial flood rasters, label with metadata, write as Zarr
    store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-pluvial/jba.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-pluvial/jba.zarr"),
    run:
        from pathlib import Path

        from mu_star.hazard import collate_jba_flood_rasters

        collate_jba_flood_rasters(sorted(Path(input.raster_dir).glob("*")), output.cube, "FLSW")