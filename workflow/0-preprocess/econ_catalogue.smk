"""Subset global catalogue data for the local study area."""

LOCAL_CATALOGUE_ROOT : str = "../catalogue"
PROCESSED_LOCAL_CATALOGUE_ROOT = config["paths"]["processed_local_catalogue_root"]
LOCAL_CATALOGUE_COMPLETE_PATH = f"{PROCESSED_LOCAL_CATALOGUE_ROOT}/.logs"

rule econ_catalogue:
    """Subset point-based global catalogue layers to the configured country."""
    output:
        complete = str(PROCESSED_LOCAL_CATALOGUE_ROOT) + "{catalogue_outputs}",
    params:
        catalogue_root = lambda wildcards: LOCAL_CATALOGUE_ROOT,
        output_root = lambda wildcards: PROCESSED_LOCAL_CATALOGUE_ROOT,
        country_code = config["local_econ"]["country_code"],
    # log:
    #     f"{LOCAL_CATALOGUE_COMPLETE_PATH}/{{catalogue_outputs}}.log",
    script:
        "catalogue.py"
