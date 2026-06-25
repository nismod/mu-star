"""
Hazard development and processing.
"""


from pathlib import Path

import numpy as np
import dask.array as dsa
import xarray as xr


def collate_jba_flood_rasters(gwl_dirs: list[Path], output_zarr_path: Path, flood_type: str) -> None:
    """
    Read directories containing JBA flood rasters and compile them into a Zarr
    store.
    """

    print("Read raster grid")
    sample = xr.open_dataset(sorted(gwl_dirs[0].glob("*.tif"))[0]).band_data.squeeze().drop_vars("band")
    sample = sample.rename({"y": "lat", "x": "lon"})

    print("Build metadata")
    gwl_coords = []
    rp_coords = []
    for gwl_dir in gwl_dirs:
        gwl = 1.4 if gwl_dir.stem == flood_type else float(gwl_dir.stem.split("_")[-1].replace("GWL", ""))
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
    schema.inundation.attrs["long_name"] = "Fluvial flooding inundation depth"

    print("Write metadata")
    schema.to_zarr(output_zarr_path, compute=False, mode="w")

    print("Stream data")
    get_rp = lambda p: int(p.stem.split("_")[-4].replace("RP", ""))
    for gwl_idx, gwl_dir in enumerate(gwl_dirs):
        gwl = 1.4 if gwl_dir.stem == flood_type else float(gwl_dir.stem.split("_")[-1].replace("GWL", ""))
        raster_paths = sorted(gwl_dir.glob("*.tif"), key=get_rp)
        for rp_idx, path in enumerate(raster_paths):
            rp = get_rp(path)
            print(gwl, rp)
            da = xr.open_dataset(path).band_data.squeeze().drop_vars("band").rename({"y": "lat", "x": "lon"})
            da = da.expand_dims(gwl=[gwl], rp=[rp]).transpose("gwl", "rp", "lat", "lon")
            da.to_dataset(name="inundation").drop_vars("spatial_ref").to_zarr(
                output_zarr_path,
                region={
                    "gwl": slice(gwl_idx, gwl_idx + 1),
                    "rp": slice(rp_idx, rp_idx + 1),
                    "lat": slice(None),
                    "lon": slice(None)
                },
            )