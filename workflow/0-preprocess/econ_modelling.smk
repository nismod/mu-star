"""Run the global BHM model for a prepared Mauritius model run."""

from economy import CATALOGUE_ROOT, OUTPUT_ROOT


rule econ_modelling:
    """Run the BHM dispatcher after model setup and catalogue inputs are ready."""
    input:
        model_params=rules.econ_model_setup.output.model_params,
        job_index=rules.econ_model_setup.output.job_index,
        constraint_table=rules.preprocess_local_econ.output.table,
        custom_bounds=rules.econ_catalogue.output.custom_bounds,
        catalogue_layers=rules.econ_catalogue.output.layers,
    output:
        complete=touch(f"{model_directory}/.globdata-run-complete"),
    params:
        output_root=OUTPUT_ROOT,
        catalogue_root=CATALOGUE_ROOT,
        model_name=model_name,
    log:
        f"logs/{model_name}_run.log",
    shell:
        "globdata-run {params.model_name:q} --no-pass "
        "--catalogue-root {params.catalogue_root:q} "
        "--output-root {params.output_root:q} > {log:q} 2>&1"
