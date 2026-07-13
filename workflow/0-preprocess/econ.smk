"""Pre-process economic data for model constraints."""

RAW_LOCAL_ECON_DATA_PATH = config["paths"]["raw_local_econ_data"]
LOCAL_ECON_CATALOGUE_ROOT = config["paths"]["local_econ_catalogue_root"]
PROCESSED_LOCAL_ECON_DATA_PATH = config["paths"]["processed_local_econ_data"]


rule preprocess_local_econ:
    """Convert official national accounts into a geospatial economic constraint table."""
    input:
        accounts = RAW_LOCAL_ECON_DATA_PATH,
    output:
        table = PROCESSED_LOCAL_ECON_DATA_PATH,
    params:
        catalogue_root = lambda wildcards: LOCAL_ECON_CATALOGUE_ROOT,
        country_code = config["local_econ"]["country_code"],
        year = config["local_econ"]["year"],
        rupees_per_usd = config["local_econ"]["rupees_per_usd"],
    log:
        f"{PROCESSED_LOCAL_ECON_DATA_PATH}.log",
    script:
        "econ.py"
