"""Subset global catalogue data for the local study area."""

LOCAL_CATALOGUE_ROOT = config["paths"]["local_econ_catalogue_root"]
PROCESSED_LOCAL_CATALOGUE_ROOT = config["paths"]["processed_local_catalogue_root"]
LOCAL_CATALOGUE_COMPLETE_PATH = f"{PROCESSED_LOCAL_CATALOGUE_ROOT}/.local_catalogue_complete"


rule preprocess_local_catalogue:
    """Subset point-based global catalogue layers to the configured country."""
    output:
        complete = LOCAL_CATALOGUE_COMPLETE_PATH,
    params:
        catalogue_root = lambda wildcards: LOCAL_CATALOGUE_ROOT,
        output_root = lambda wildcards: PROCESSED_LOCAL_CATALOGUE_ROOT,
        country_code = config["local_econ"]["country_code"],
    log:
        f"{LOCAL_CATALOGUE_COMPLETE_PATH}.log",
    script:
        "catalogue.py"
