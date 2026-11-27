"""Prepare a tracked configuration for one economic model run."""

from economy import OUTPUT_ROOT, model_freeze
from economy.model_setup import model_name_for

model_settings = {
    "model_nickname": "",
    "model_freeze": model_freeze,
    "region": ["Mauritius"],
    "final_res": 9,
    "entropy": True,
    "alpha" : 50,
    "memory_limit": "18GB",
    "max_temp_directory_size": "50GB",
    "verbose": True,
    "n_cores": 6,
    "samples": 1_000,
    "target_accept": 0.92,
    "max_treedepth": 14,
    "trace_frac": 0.3,
    "information_threshold": 0.98,
}

proxy_layers = ["jrc_s_nres_10", "jrc_pop_100"]
proxy_to_sector = {
    "B": ["maus_mining"],
    **{section: list(proxy_layers) for section in "CDEFGHIJKLMNOPQRSTU"},
}
constraint_file = (
    f"{config['local_econ']['country_code'].lower()}_gdp{config['local_econ']['year']}"
)
constraint_settings = {
    "constraint_file": constraint_file,
    "custom_sections_agg": {},
    "search_columns": ["gid_0", "country", "continent", "sub_continent"],
    "industries_to_predict": list("CDEFGHIJKLMNOPQRS"),
    "constraint_id_col": "gid_0",
    "proxy_layer_names": proxy_layers,
    "proxy_to_sector": proxy_to_sector,
    "uncertainty": 0.02,
    "z_score_tolerance": 1,
}
model_name = model_name_for(model_settings, constraint_settings)
model_directory = f"{OUTPUT_ROOT}/{model_name}"


rule econ_model_setup:
    """Create the model parameter and job-index files consumed by globdata-run."""
    input:
        constraint_table=rules.preprocess_local_econ.output.table,
        code="src/economy/model_setup.py",
    output:
        model_params=f"{model_directory}/model_params.yaml",
        job_index=f"{model_directory}/job_idx.csv",
    params:
        model_name=model_name,
        model_settings=model_settings,
        constraint_settings=constraint_settings,
    log:
        f"logs/{model_name}_setup.log",
    run:
        from economy.model_setup import prepare_model_setup

        prepare_model_setup(
            constraint_path=input.constraint_table,
            model_name=params.model_name,
            model_settings=params.model_settings,
            constraint_settings=params.constraint_settings,
            model_params_path=output.model_params,
            job_index_path=output.job_index,
            log_path=log[0],
        )
