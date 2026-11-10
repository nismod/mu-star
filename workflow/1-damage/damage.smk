"""
Calculate an estimate of direct damage (rehabilitation) costs to assets due to
various perils.
"""


rule rasterise_layer:
    """
    Split vector assets on raster grids.

    Test with:
    snakemake -c1 data/results/damage/road/rp/flood/fluvial/split.gpq
    """
    input:
        layer = "{data}/processed/asset/{layer}.gpq",
        # dimensions: {rp, ...hazard_params}
        hazard = "{data}/processed/hazard/rp/{peril}/{subperil}.zarr",
    output:
        # columns: {asset_id, split_i, split_j, rp, ...hazard_params, hazard_value}
        # metadata: CRS, hazard grid definition (affine transform)?
        split = "{data}/results/damage/{layer}/rp/{peril}/{subperil}/split.gpq",
    shell:
        """
        # TODO: Requires reworking snail to accept xarray spatial dimensions to split along.
        touch {output.split}
        """


rule damage:
    """
    Determine damage fractions wrought upon a given split layer, then calculate
    the resulting rehabilitation costs.

    Test with:
    snakemake -c1 data/results/damage/road/rp/flood/fluvial/ensemble-0/damage.zarr
    """
    input:
        asset_curve_map = "config/damage_curve/asset_map.csv",
        split = rules.rasterise_layer.output.split,
    output:
        # To contain `fraction` and `monetary` variables
        # dimensions: {(asset_id, split_index), rp, ...hazard_params} # do multi-indexes work here? | glue as string? | write as table to pq
        split_damage = "{data}/results/damage/{layer}/rp/{peril}/{subperil}/split_damage.zarr",
        # dimensions: {asset_id, rp, ...hazard_params}
        damage = "{data}/results/damage/{layer}/rp/{peril}/{subperil}/damage.zarr",
    shell:
        """
        touch {output.split_damage}
        touch {output.damage}
        """
