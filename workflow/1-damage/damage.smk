"""
Calculate an estimate of direct damage (rehabilitation) costs to assets due to
various perils.
"""


rule rasterise_layer:
    """
    Split vector assets on raster grids.

    Test with:
    snakemake -c1 data/out/damage/road/rp/flood/fluvial/split.gpq
    """
    input:
        layer = "{data}/proc/asset/{layer}.gpq",
        hazard = "{data}/proc/hazard/rp/{peril}/{subperil}.zarr",
    output:
        split = "{data}/out/damage/{layer}/rp/{peril}/{subperil}/split.gpq",
    shell:
        """
        # TODO: Requires reworking snail to accept xarray spatial dimensions to split along.
        touch {output.split}
        """


checkpoint sample_sensitivity:
    """
    Sample from distributions of uncertain input parameters to generate direct
    damage ensemble member metadata.

    Test with:
    snakemake -c1 data/out/ensemble_set.csv
    """
    input:
        configuration = "config/config.yaml",  # Parameter distributions defined here
    output:
        ensemble_set = "{data}/out/ensemble_set.csv",
    shell:
        """
        touch {output.ensemble_set}
        """


rule damage:
    """
    Determine damage fractions wrought upon a given split layer, then calculate
    the resulting rehabilitation costs.

    Test with:
    snakemake -c1 data/out/damage/road/rp/flood/fluvial/ensemble-0/damage.zarr
    """
    input:
        ensemble_set = lambda wildcards: checkpoints.sample_sensitivity.get(**wildcards).output.ensemble_set,
        asset_curve_map = "config/damage_curve/asset_map.csv",
        split = rules.rasterise_layer.output.split,
    output:
        # To contain `fraction` and `monetary` variables
        split_damage = "{data}/out/damage/{layer}/rp/{peril}/{subperil}/{ensemble}/split_damage.zarr",
        damage = "{data}/out/damage/{layer}/rp/{peril}/{subperil}/{ensemble}/damage.zarr",
    shell:
        """
        touch {output.split_damage}
        touch {output.damage}
        """

