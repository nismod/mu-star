"""Run the global BHM model for a prepared Mauritius model run."""

from economy import CATALOGUE_ROOT, OUTPUT_ROOT

rule econ_modelling:
    """Run the BHM dispatcher for one tracked Mauritius model-run directory."""
    output:
        model_run=directory(f"{OUTPUT_ROOT}/{{model_run}}"),
    params:
        output_root=OUTPUT_ROOT,
        catalogue_root=CATALOGUE_ROOT,
    shell:
        """
        CATALOGUE_ROOT="{params.catalogue_root}" \
        OUTPUT_ROOT="{params.output_root}" \
        globdata-bhm "{output.model_run}" --no-pass
        """
