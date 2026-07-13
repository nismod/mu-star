"""Subset the global data catalogue to Mauritius."""

import logging
from pathlib import Path


def preprocess_local_catalogue(catalogue_root, output_root, country_code):
    """Write local subsets of catalogue layers that contain point coordinates."""
    import scalenav.oop as snoo
    from globdata.parameters import load_catalogue
    from ibis import _

    catalogue_root = str(catalogue_root)
    if not catalogue_root.endswith("/"):
        catalogue_root = f"{catalogue_root}/"

    catalogue = load_catalogue(local=True, root=catalogue_root)
    conn = snoo.connect()
    region_codes = [country_code]

    region = conn.read_parquet(catalogue["custom_bounds"]).filter(_.gid_0.isin(region_codes))
    region_bound = region.geometry.execute().set_crs("epsg:4326")[0]

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    missed_layers = []

    for name, path in catalogue.items():
        print("Processing:", name)
        layer = conn.read_parquet(path)

        try:
            col_x, col_y = snoo.coords_columns(layer)
            output_folder = output_root / f"{country_code}_{name}"
            output_folder.mkdir(parents=True, exist_ok=True)
            output_path = output_folder / f"{name}.parquet"
            layer.filter(_[col_x].point(_[col_y]).intersects(region_bound)).to_parquet(
                str(output_path),
                overwrite=True,
            )
        except Exception:
            missed_layers.append(name)

    print("Missed layers:", missed_layers)
    return missed_layers


def run_from_snakemake(snakemake):
    """Pass workflow settings to the catalogue preprocessing function."""
    log_path = Path(str(snakemake.log[0]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    preprocess_local_catalogue(
        catalogue_root=snakemake.params.catalogue_root,
        output_root=snakemake.params.output_root,
        country_code=snakemake.params.country_code,
    )
    Path(str(snakemake.output.complete)).touch()


if __name__ == "__main__":
    run_from_snakemake(globals()["snakemake"])
