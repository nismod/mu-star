"""
Transform raw input data into analysis ready data.
"""


rule collate_flooding:
    """
    Take flood rasters, resample to common grid if necessary and output as Zarr
    store.

    Test with:
    snakemake -c1 data/proc/hazard/rp/flood/fluvial.zarr
    """
    input:
        # TODO: Might want an input function to select the appropriate files
        raster = "{data}/raw/hazard/rp/{peril}/{subperil}/",
    output:
        cube = "{data}/proc/hazard/rp/{peril}/{subperil}.zarr",
    shell:
        """
        touch {output.cube}
        """
