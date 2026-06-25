"""
Hazard development and processing.
"""


import logging
from pathlib import Path

import numpy as np
import dask.array as dsa
import xarray as xr


# Global Warming Level (GWL) is not given for baseline flood maps, so we manually add it.
# "This period [1980-2010] corresponds to approximately 0.7–0.8°C of global warming
# relative to the pre-industrial (1850–1900)." -- JBA 20260625
JBA_BASELINE_FLOOD_GWL = 0.75

# Permitted hazard raster extent, a bounding box of Mauritius and Rodrigues
# Some rasters have a larger domain that we need
# e.g. FLSW baseline RP500 includes the Agalega Archipelago
# Clip these larger datasets
JBA_BBOX = (57.299726, -20.534446, 63.512227, -19.658890)

JBA_CODE_LONG_NAME = {
    "FLRF": "Fluvial flooding inundation depth",
    "FLSW": "Pluvial flooding inundation depth"
}


def collate_jba_flood_rasters(
    gwl_dirs: list[Path],
    output_zarr_path: Path,
    flood_code: str,
) -> None:
    """
    Read directories containing JBA flood rasters and compile them into a Zarr
    store.

    Args:
        gwl_dirs: Directory paths containing TIFFs
        output_zarr_path: Path to write consolidated Zarr store to
        flood_code: Categorical string comprising part of all of gwl_dirs folder names
    """

    logging.info("Read raster grid")
    minx, miny, maxx, maxy = JBA_BBOX
    sample = xr.open_dataset(sorted(gwl_dirs[0].glob("*.tif"))[0]).band_data.squeeze().drop_vars("band")
    sample = sample.rename({"y": "lat", "x": "lon"})
    sample = sample.sel(lat=slice(maxy, miny), lon=slice(minx, maxx))

    logging.info("Build metadata")
    gwl_coords = []
    rp_coords = []
    get_gwl = lambda p: float(p.split("_")[-1].replace("GWL", ""))
    for gwl_dir in gwl_dirs:
        gwl = JBA_BASELINE_FLOOD_GWL if gwl_dir.stem == flood_code else get_gwl(gwl_dir.stem)
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
    schema.inundation.attrs["long_name"] = JBA_CODE_LONG_NAME[flood_code]

    logging.info("Write metadata")
    schema.to_zarr(output_zarr_path, compute=False, mode="w")

    logging.info("Stream data")
    get_rp = lambda p: int(p.stem.split("_")[-4].replace("RP", ""))
    for gwl_idx, gwl_dir in enumerate(gwl_dirs):
        gwl = JBA_BASELINE_FLOOD_GWL if gwl_dir.stem == flood_code else get_gwl(gwl_dir.stem)
        raster_paths = sorted(gwl_dir.glob("*.tif"), key=get_rp)
        for rp_idx, path in enumerate(raster_paths):
            rp = get_rp(path)
            logging.info(f"{gwl}, {rp}")
            da = xr.open_dataset(path).band_data.squeeze().drop_vars("band").rename({"y": "lat", "x": "lon"})
            da = da.expand_dims(gwl=[gwl], rp=[rp]).transpose("gwl", "rp", "lat", "lon")
            # Some rasters are larger than the bounding box containing Mauritius and Rodrigues -- crop these
            da = da.sel(lat=slice(maxy, miny), lon=slice(minx, maxx))
            ds = da.to_dataset(name="inundation").drop_vars("spatial_ref")
            ds.to_zarr(
                output_zarr_path,
                region={
                    "gwl": slice(gwl_idx, gwl_idx + 1),
                    "rp": slice(rp_idx, rp_idx + 1),
                    "lat": slice(None),
                    "lon": slice(None)
                },
            )