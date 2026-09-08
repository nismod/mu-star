"""Pre-process economic data for model constraints."""

from economy import (
    CATALOGUE_ROOT,
    processed_local_econ_data_path,
    raw_local_econ_data_path,
)

rule preprocess_local_econ:
    """Convert official national accounts into a geospatial economic constraint table."""
    input:
        accounts = raw_local_econ_data_path,
    output:
        table = processed_local_econ_data_path,
    params:
        catalogue_root = CATALOGUE_ROOT,
        country_code = config["local_econ"]["country_code"],
        year = config["local_econ"]["year"],
        rupees_per_usd = config["local_econ"]["rupees_per_usd"],
    log:
        f"{processed_local_econ_data_path}.log",
    script:
        "economic.py"
