"""
JBA Risk Management flood hazard data.

Return period maps for pluvial, fluvial and coastal flooding, all at 1 arcsecond
/ 30m resolution.

See incoming/Hazards/JBA Flood Hazard/2026s0679_metadata_docs for JBA
methodology documentation and additional metadata.

This file contains rules to transform raw flooding data into analysis ready
datasets.
"""


rule collate_JBA_fluvial_flooding:
    """
    Collate JBA fluvial flooding rasters, label with metadata, write as Zarr
    store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-fluvial/jba.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-fluvial/jba.zarr"),
    run:
        from pathlib import Path

        import numpy as np
        import dask.array as dsa
        import xarray as xr

        gwl_dirs = sorted(Path(input.raster_dir).glob("*"))

        print("Read raster grid")
        sample = xr.open_dataset(sorted(gwl_dirs[0].glob("*.tif"))[0]).band_data.squeeze().drop_vars("band")
        sample = sample.rename({"y": "lat", "x": "lon"})

        print("Build metadata")
        gwl_coords = []
        rp_coords = []
        for gwl_dir in gwl_dirs:
            gwl = 1.4 if gwl_dir.stem == "FLRF" else float(gwl_dir.stem.split("_")[-1].replace("GWL", ""))
            gwl_coords.append(gwl)
            if not rp_coords:
                rp_coords = sorted([int(p.stem.split("_")[-4].replace("RP", "")) for p in gwl_dir.glob("*.tif")])
        shape = (len(gwl_coords), len(rp_coords), len(sample.lat), len(sample.lon))
        schema = xr.Dataset({"inundation": xr.DataArray(
            dsa.zeros(shape, dtype=np.float32, chunks=(1, 1, 2048, 2048)),
            dims=["gwl", "rp", "lat", "lon"],
            coords={"gwl": gwl_coords, "rp": rp_coords, "lat": sample.lat, "lon": sample.lon},
            attrs={"unit": "metres", "source": "JBA Risk Management"},
        )})
        schema.gwl.attrs["unit"] = "degrees Celsius"
        schema.gwl.attrs["long_name"] = "Global Warming Level"
        schema.rp.attrs["unit"] = "years"
        schema.rp.attrs["long_name"] = "Return period"
        schema.lat.attrs["unit"] = "degrees"
        schema.lat.attrs["long_name"] = "Latitude"
        schema.lon.attrs["unit"] = "degrees"
        schema.lon.attrs["long_name"] = "Longitude"

        print("Write metadata")
        schema.to_zarr(output.cube, compute=False, mode="w")

        print("Stream data")
        get_rp = lambda p: int(p.stem.split("_")[-4].replace("RP", ""))
        for gwl_idx, gwl_dir in enumerate(gwl_dirs):
            gwl = 1.4 if gwl_dir.stem == "FLRF" else float(gwl_dir.stem.split("_")[-1].replace("GWL", ""))
            raster_paths = sorted(gwl_dir.glob("*.tif"), key=get_rp)
            for rp_idx, path in enumerate(raster_paths):
                rp = get_rp(path)
                print(gwl, rp)
                da = xr.open_dataset(path).band_data.squeeze().drop_vars("band").rename({"y": "lat", "x": "lon"})
                da = da.expand_dims(gwl=[gwl], rp=[rp]).transpose("gwl", "rp", "lat", "lon")
                da.to_dataset(name="inundation").drop_vars("spatial_ref").to_zarr(
                    output.cube,
                    region={
                        "gwl": slice(gwl_idx, gwl_idx + 1),
                        "rp": slice(rp_idx, rp_idx + 1),
                        "lat": slice(None),
                        "lon": slice(None)
                    },
                )
