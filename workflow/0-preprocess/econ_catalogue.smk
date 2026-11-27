"""Subset the configured source catalogue into the local catalogue."""

from economy import CATALOGUE_PROCESSED_ROOT, CUSTOM_BOUNDS_PATH
from economy.catalogue import catalogue_include, catalogue_layer_include, catalogue_output_directories

catalogue_directories = catalogue_output_directories(SOURCE_CATALOGUE, catalogue_include)
catalogue_layer_directories = [catalogue_directories[layer] for layer in catalogue_layer_include]


rule econ_catalogue:
    """Fetch custom bounds and subset every configured source layer for the country."""
    input:
        code="src/economy/catalogue.py",
    output:
        custom_bounds=f"{CUSTOM_BOUNDS_PATH}/.catalogue-source.json",
        layers=expand(
            f"{CATALOGUE_PROCESSED_ROOT}/{{catalogue_outputs}}/.catalogue-source.json",
            catalogue_outputs=catalogue_layer_directories,
        ),
    params:
        source_root=CATALOGUE_SOURCE_ROOT,
        source_catalogue=SOURCE_CATALOGUE,
        layer_names=catalogue_include,
        country_code=config["local_econ"]["country_code"],
    log:
        f"logs/econ_catalogue_{config['local_econ']['country_code']}.log",
    run:
        import globdata

        from economy.catalogue import catalogue_layer_include, preprocess_local_catalogue
        from globdata.catalogue import load_catalogue

        output_manifests = {"custom_bounds": output.custom_bounds}
        output_manifests.update(
            dict(zip(catalogue_layer_include, output.layers, strict=True))
        )
        current_catalogue = globdata.get_catalogue()
        try:
            source_catalogue = (
                load_catalogue(root=params.source_root, include_=params.layer_names)
                if params.source_root is not None
                else params.source_catalogue
            )
            globdata.set_catalogue(source_catalogue)
            preprocess_local_catalogue(
                catalogue=globdata.CATALOGUE,
                layer_names=params.layer_names,
                country_code=params.country_code,
                output_manifests=output_manifests,
                log_path=log[0],
            )
        finally:
            globdata.set_catalogue(current_catalogue)
