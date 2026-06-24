"""
South West Indian Ocean Risk Assessment and Financing Initiative (SWIO RAFI)
flood hazard data.

Raster data on ~900m / 30 arcsecond grid.

For more information on SWIO RAFI source data, see pg. 10 (12) of:
https://www.gfdrr.org/sites/default/files/mauritius.pdf

This file contains rules to transform raw flooding data into analysis ready
Zarr datasets.
"""


rule collate_SWIO_RAFI_ETC_flooding:
    """
    Collate SWIO RAFI extratropical cyclone (low/depression) induced flooding
    rasters, label with metadata and output as Zarr store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-extratropical-cyclone/swio-rafi.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/Flood Hazard/hzd-mus-fl-etc/"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-extratropical-cyclone/swio-rafi.zarr"),
    run:
        import glob
        from pathlib import Path

        import numpy as np
        import rioxarray
        import xarray as xr

        rp_to_path = sorted(
            [
                (int(Path(path).stem.split("_")[-1]), path)
                for path in glob.glob(str(Path(input.raster_dir) / "*.tif"))
            ],
            key=lambda x: x[0]
        )

        data = []
        for (rp, path) in rp_to_path:
            da = xr.open_dataset(path).band_data.squeeze().drop_vars("band")
            da = da.rename("inundation")
            da.attrs["long_name"] = "Flooding inundation depth, as caused by extratropical cyclone"
            da.attrs["source"] = "SWIO RAFI project, World Bank"
            # There's no metadata or documentation, but from a histogram I
            # think the source is in centimetres
            da = da / 100
            da.attrs["unit"] = "metres"
            da = da.assign_coords(rp=rp)
            data.append(da)

        da = xr.concat(data, dim="rp")
        da.rp.attrs["unit"] = "years"
        da.rp.attrs["long_name"] = "Return period"
        da = da.expand_dims(case=[0])
        da = da.assign_coords(scenario=("case", np.array(["baseline"], dtype=object)), epoch=("case", [2010]))
        da = da.rename({"y": "lat", "x": "lon"})
        da.lat.attrs["unit"] = "degrees"
        da.lon.attrs["unit"] = "degrees"
        da.lat.attrs["long_name"] = "Latitude"
        da.lon.attrs["long_name"] = "Longitude"
        da = da.transpose("case", "rp", "lat", "lon")
        da.to_dataset().to_zarr(output.cube)


rule collate_SWIO_RAFI_TC_flooding:
    """
    Collate SWIO RAFI tropical cyclone induced flooding rasters, label with
    metadata and output as Zarr store.

    Note that these maps have some areas on the mainland of very low inundation
    for RPs < 250 years. These lie on a ~15km wide swath from the SW to the NE.
    
    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-tropical-cyclone/swio-rafi.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/Flood Hazard/hzd-mus-fl-tcy/"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-tropical-cyclone/swio-rafi.zarr"),
    run:
        import glob
        from pathlib import Path

        import numpy as np
        import rioxarray
        import xarray as xr

        rp_to_path = sorted(
            [
                (int(Path(path).stem.split("_")[-1]), path)
                for path in glob.glob(str(Path(input.raster_dir) / "*.tif"))
            ],
            key=lambda x: x[0]
        )

        data = []
        for (rp, path) in rp_to_path:
            da = xr.open_dataset(path).band_data.squeeze().drop_vars("band")
            da = da.rename("inundation")
            da.attrs["long_name"] = "Flooding inundation depth, as caused by tropical cyclone"
            da.attrs["source"] = "SWIO RAFI project, World Bank"
            # There's no metadata or documentation, but from a histogram I
            # think the source is in centimetres
            da = da / 100
            da.attrs["unit"] = "metres"
            da = da.assign_coords(rp=rp)
            data.append(da)

        da = xr.concat(data, dim="rp")
        da.rp.attrs["unit"] = "years"
        da.rp.attrs["long_name"] = "Return period"
        da = da.expand_dims(case=[0])
        da = da.assign_coords(scenario=("case", np.array(["baseline"], dtype=object)), epoch=("case", [2010]))
        da = da.rename({"y": "lat", "x": "lon"})
        da.lat.attrs["unit"] = "degrees"
        da.lon.attrs["unit"] = "degrees"
        da.lat.attrs["long_name"] = "Latitude"
        da.lon.attrs["long_name"] = "Longitude"
        da = da.transpose("case", "rp", "lat", "lon")
        da.to_dataset().to_zarr(output.cube)


rule collate_SWIO_RAFI_storm_surge_flooding:
    """
    Read SWIO RAFI storm surge flooding shapefile, label with metadata and
    output as Zarr store.

    Test with:
    snakemake -c1 data/processed/hazard/rp/peril-flood/subperil-coastal/swio-rafi.zarr
    """
    input:
        raster_dir = "{data}/incoming/Hazards/Coastal Flooding/Coastal Flooding/hzd-mus-fl-fss-points.shp"
    output:
        cube = directory("{data}/processed/hazard/rp/peril-flood/subperil-coastal/swio-rafi.zarr"),
    run:
        import re 

        import geopandas as gpd
        import numpy as np
        import pandas as pd

        gdf = gpd.read_file(input.raster_dir)
        rp_cols = [c for c in gdf.columns if re.match(r"^\d+-year$", c)]
        return_periods = [int(c.split("-")[0]) for c in rp_cols]
        melted = gdf[["Lat", "Lon"] + rp_cols].melt(
            id_vars=["Lat", "Lon"],
            var_name="rp",
            value_name="inundation",
        )
        melted["rp"] = melted["rp"].str.replace("-year", "").astype(int)
        melted = melted.rename(columns={"Lat": "lat", "Lon": "lon"})
        
        da = melted.set_index(["rp", "lat", "lon"])["inundation"].to_xarray()
        da.attrs["long_name"] = "Flooding inundation depth, as caused by storm surge"
        da.attrs["source"] = "SWIO RAFI project, World Bank"
        da.attrs["unit"] = "metres"
        da.rp.attrs["unit"] = "years"
        da.rp.attrs["long_name"] = "Return period"

        da = da.expand_dims(case=[0])
        da = da.assign_coords(scenario=("case", np.array(["baseline"], dtype=object)), epoch=("case", [2010]))
        da.lat.attrs["unit"] = "degrees"
        da.lon.attrs["unit"] = "degrees"
        da.lat.attrs["long_name"] = "Latitude"
        da.lon.attrs["long_name"] = "Longitude"
        da = da.transpose("case", "rp", "lat", "lon")
        ds = da.to_dataset(name="inundation")
        ds.to_zarr(output.cube)

