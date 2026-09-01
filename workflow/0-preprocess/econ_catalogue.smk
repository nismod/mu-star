"""Subset global catalogue data for the local study area."""

from economy import local_catalogue_root, processed_local_catalogue_root

rule econ_catalogue:
    """Subset point-based global catalogue layers to the configured country."""
    output:
        complete = directory(processed_local_catalogue_root),
        # complete = str(processed_local_catalogue_root) + "{catalogue_outputs}",
    params:
        catalogue_root = lambda wildcards: local_catalogue_root,
        output_root = lambda wildcards: processed_local_catalogue_root,
        country_code = config["local_econ"]["country_code"],
    # log:
    #     f"{LOCAL_CATALOGUE_COMPLETE_PATH}/{{catalogue_outputs}}.log",
    script:
        "catalogue.py"
