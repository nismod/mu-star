"""Copy selected global catalogue layers into the local study catalogue."""

import json
from pathlib import Path, PurePosixPath

catalogue_include = [
    "jrc_s_nres_10",
    "jrc_pop_100",
    "jrc_h_agbh_100",
    "copernicus_builtup_100m_2018",
    "copernicus_cropland_100m_2018",
    "custom_pois",
    "custom_bounds",
    "maus_mining",
]
catalogue_layer_include = [
    layer for layer in catalogue_include if layer != "custom_bounds" and not layer.startswith("mus_gdp")
]

def catalogue_output_directories(catalogue, layer_names):
    """Return each catalogue key's exact processed-directory name from its source glob."""
    directories = {}
    for layer_name in layer_names:
        source_parts = PurePosixPath(catalogue[layer_name]).parts
        try:
            processed_index = source_parts.index("processed")
            directory_name = source_parts[processed_index + 1]
        except (ValueError, IndexError) as error:
            raise ValueError(f"Catalogue path for {layer_name!r} has no processed layer directory") from error
        if any(character in directory_name for character in "*?[]{}"):
            raise ValueError(f"Catalogue path for {layer_name!r} has a wildcard layer directory")
        if directory_name in directories.values():
            raise ValueError(f"Multiple catalogue layers resolve to processed directory {directory_name!r}")
        directories[layer_name] = directory_name
    return directories


def _write_source_manifest(manifest_path, layer_name, source_path, country_code):
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "layer": layer_name,
                "source": source_path,
                "country_code": country_code,
            },
            indent=2,
        )
        + "\n"
    )


def _write_local_layer(relation, manifest_path, layer_name, source_path, country_code):
    manifest_path = Path(manifest_path)
    output_directory = manifest_path.parent
    output_directory.mkdir(parents=True, exist_ok=True)
    relation.to_parquet_dir(
        str(output_directory),
        existing_data_behavior="overwrite_or_ignore",
    )
    _write_source_manifest(manifest_path, layer_name, source_path, country_code)


def preprocess_local_catalogue(catalogue, layer_names, country_code, output_manifests, log_path):
    """Subset selected layers from the configured catalogue into local catalogue paths."""
    import logging

    import scalenav.oop as snoo
    from ibis import _

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    layer_names = list(layer_names)
    if "custom_bounds" not in layer_names:
        raise ValueError("The catalogue include list must contain 'custom_bounds'")
    if len(layer_names) != len(set(layer_names)):
        raise ValueError("Catalogue include list contains duplicate layer names")

    missing_sources = set(layer_names) - set(catalogue)
    if missing_sources:
        raise KeyError(f"Source catalogue is missing requested layers: {sorted(missing_sources)}")
    unsupported_sources = [
        layer
        for layer in layer_names
        if "://" in catalogue[layer] and not catalogue[layer].startswith("s3://")
    ]
    if unsupported_sources:
        raise ValueError(f"Catalogue sources must be S3 or local filesystem paths: {unsupported_sources}")
    if set(output_manifests) != set(layer_names):
        raise ValueError("Output manifests must be provided for every included catalogue layer")

    connection = snoo.connect()
    bounds_source = catalogue["custom_bounds"]
    logging.info("Reading custom bounds from %s", bounds_source)
    bounds = connection.read_parquet(bounds_source).filter(_.gid_0.isin([country_code]))
    bounds_geometry = bounds.geometry.unary_union().execute()
    _write_local_layer(
        relation=bounds,
        manifest_path=output_manifests["custom_bounds"],
        layer_name="custom_bounds",
        source_path=bounds_source,
        country_code=country_code,
    )

    for layer_name in layer_names:
        if layer_name == "custom_bounds":
            continue
        if layer_name.startswith("mus_gdp"):
            raise ValueError(f"{layer_name!r} is produced by a separate workflow rule")

        source_path = catalogue[layer_name]
        logging.info("Subsetting catalogue layer %s from %s", layer_name, source_path)
        layer = connection.read_parquet(source_path)
        col_x, col_y = snoo.coords_columns(layer)
        subset = layer.filter(_[col_x].point(_[col_y]).intersects(bounds_geometry))
        _write_local_layer(
            relation=subset,
            manifest_path=output_manifests[layer_name],
            layer_name=layer_name,
            source_path=source_path,
            country_code=country_code,
        )

    logging.info("Wrote local catalogue layers: %s", ", ".join(layer_names))
    return layer_names
