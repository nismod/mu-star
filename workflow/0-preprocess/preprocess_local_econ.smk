"""Pre-process economic data for model constraints."""

from economy import local_econ_data_path, raw_local_econ_data_path

local_econ_country_code = config["local_econ"]["country_code"]
local_econ_year = config["local_econ"]["year"]
local_econ_table = local_econ_data_path(local_econ_country_code, local_econ_year)

rule preprocess_local_econ:
    """Convert official national accounts into a geospatial economic constraint table."""
    input:
        accounts=raw_local_econ_data_path,
        custom_bounds=rules.econ_catalogue.output.custom_bounds,
        code="src/economy/local_economics.py",
    output:
        table=local_econ_table,
    params:
        country_code=local_econ_country_code,
        year=local_econ_year,
        rupees_per_usd=config["local_econ"]["rupees_per_usd"],
        custom_bounds_path=globdata.CATALOGUE["custom_bounds"],
    log:
        f"{local_econ_table}.log",
    run:
        from economy.local_economics import preprocess_local_econ

        preprocess_local_econ(
            raw_data_path=input.accounts,
            custom_bounds_path=params.custom_bounds_path,
            output_path=output.table,
            country_code=params.country_code,
            year=params.year,
            rupees_per_usd=params.rupees_per_usd,
            log_path=log[0],
        )
