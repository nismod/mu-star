"""Run the global BHM model for a prepared Mauritius model run."""

from economy import CATALOGUE_ROOT, OUTPUT_ROOT, model_freeze

rule econ_modelling:
    """Run the BHM dispatcher for one tracked Mauritius model-run directory."""
    output:
        model_run=directory(f"{OUTPUT_ROOT}/{model_freeze}*"),
    params:
        output_root=OUTPUT_ROOT,
        catalogue_root=CATALOGUE_ROOT,
        model_name = model_freeze
    shell:
        "globdata-bhm {params.model_name} --no-pass --catalogue-path={params.catalogue_root} --output-root={params.output_root}"
