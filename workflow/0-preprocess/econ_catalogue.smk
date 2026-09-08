"""Subset global catalogue data for the local study area."""

from economy import CATALOGUE_ROOT
from globdata.catalogue import REMOTE_ROOT

rule econ_catalogue:
    """Subset point-based global catalogue layers to the configured country."""
    output:
        complete = directory(CATALOGUE_ROOT),
    params:
        catalogue_root = REMOTE_ROOT,
        output_root = CATALOGUE_ROOT,
        country_code = config["local_econ"]["country_code"],
    script:
        "catalogue.py"
