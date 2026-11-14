"""Spatial economic activity on the H3 grid.

STUB. The economics workflow (Bayesian downscaling of sectoral output, see
docs/src/economics.md) should produce this file. The land transport analysis
reads it as the jobs (destination) layer, through rules.economic_activity_h3.
"""


rule economic_activity_h3:
    """
    Sectoral economic output per H3 cell.

    Output schema (wide): one row per cell.
        h3_index   str, H3 cell id at the resolution in {zoning} (e.g. h3-r8)
        gva_<sector>  float, output (GVA) of each ISIC section, one column per
                   sector (e.g. gva_A, gva_C, ...), in the currency and year of
                   the national accounts used as constraints
        geometry   optional (cell polygon)

    Test with:
    snakemake -c1 data/processed/economy/h3-r8/economic-activity.parquet
    """
    output:
        activity="{data}/processed/economy/{zoning}/economic-activity.parquet",
    shell:
        """
        echo "TODO: economic_activity_h3 is a stub; wire it to the economics workflow" >&2
        exit 1
        """
