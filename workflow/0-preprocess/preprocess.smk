"""
Transform incoming input data into analysis ready data.
"""


rule download_SWIO_RAFI_flooding:
    """
    Download South West Indian Ocean Risk Assessment and Financing Initiative
    (SWIO RAFI) raster flood data. Specifically pluvial flooding due to
    extratropical cyclones.

    The data catalogue page for this dataset is here:
    https://datacatalog.worldbank.org/search/dataset/0038588/Mauritius-flood-hazard

    There are datasets available for flooding induced by both tropical and
    extratropical cyclones. At a quick glance the tropical cyclone data appears
    to have large blank areas.

    Test with:
    snakemake -c1 data/incoming/hazard/rp/peril-flood/subperil-pluvial/swio-rafi-extratropical
    """
    output:
        directory("{data}/incoming/hazard/rp/peril-flood/subperil-pluvial/swio-rafi-extratropical")
    shell:
        """
        mkdir -p {output}
        cd {output}
        wget https://datacatalogfiles.worldbank.org/ddh-published/0038588/1/DR0054314/hzd-mus-fl-etc.zip
        unzip hzd-mus-fl-etc.zip
        rm hzd-mus-fl-etc.zip
        """

rule collate_SWIO_RAFI_flooding:
    """
    Collate SWIO RAFI flood rasters, label with metadata and output as Zarr store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-pluvial.zarr
    """
    input:
        raster_dir = "{data}/incoming/hazard/rp/peril-flood/subperil-pluvial/swio-rafi-extratropical",
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-pluvial.zarr"),
    run:
        import glob
        from pathlib import Path

        import numpy as np
        import rioxarray
        import xarray as xr

        rp_to_path = sorted(
            [
                (int(Path(path).stem.split("_")[-1]), path)
                for path in glob.glob(str(Path(input.raster_dir) / "*"))
            ],
            key=lambda x: x[0]
        )

        data = []
        for (rp, path) in rp_to_path:
            da = xr.open_dataset(path).band_data.squeeze().drop_vars("band")
            da = da.rename("inundation")
            da.attrs["long_name"] = "flooding inundation depth"
            # There's no metadata or documentation, but from a histogram I
            # think the source is in centimetres
            da = da / 100
            da.attrs["unit"] = "metres"
            da = da.assign_coords(return_period=rp)
            da = da.expand_dims({"scenario": np.array(['baseline'], dtype=object)})
            data.append(da)

        da = xr.concat(data, dim="return_period")
        da.return_period.attrs["unit"] = "years"

        da = da.transpose("scenario", "return_period", "y", "x")

        da.to_dataset().to_zarr(output.cube)
