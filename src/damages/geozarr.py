"""Read and write grid metadata following the GeoZarr conventions.

Hazard data cubes are stored as Zarr groups with explicit ``y`` and ``x``
coordinate variables. We also record the grid definition as group attributes
using the `spatial`_ and `proj`_ Zarr conventions (as adopted by GeoZarr), so
that the grid can be read without decoding coordinates:

- ``spatial:dimensions`` - names of the ``[y, x]`` dimensions
- ``spatial:shape`` - ``[height, width]``
- ``spatial:transform`` - affine transform ``[a, b, c, d, e, f]`` in
  rasterio/Affine order, mapping (column, row) to (x, y)
- ``spatial:bbox`` - ``[xmin, ymin, xmax, ymax]``
- ``spatial:registration`` - ``"pixel"`` (cell-area registration)
- ``proj:code`` and ``proj:wkt2`` - coordinate reference system

.. _spatial: https://github.com/zarr-conventions/spatial
.. _proj: https://github.com/zarr-conventions/proj
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from pyproj import CRS
from snail.intersection import GridDefinition

SPATIAL_CONVENTION = {
    "uuid": "689b58e2-cf7b-45e0-9fff-9cfc0883d6b4",
    "name": "spatial",
    "description": "Spatial coordinate information",
    "schema_url": "https://raw.githubusercontent.com/zarr-conventions/spatial/refs/tags/v0.1/schema.json",
    "spec_url": "https://github.com/zarr-conventions/spatial/blob/v0.1/README.md",
}
PROJ_CONVENTION = {
    "uuid": "f17cb550-5864-4468-aeb7-f3180cfb622f",
    "name": "proj",
    "description": "Coordinate reference system information for geospatial data",
    "schema_url": "https://raw.githubusercontent.com/zarr-conventions/proj/refs/tags/v0.1/schema.json",
    "spec_url": "https://github.com/zarr-conventions/proj/blob/v0.1/README.md",
}


def geozarr_attrs(
    transform: Sequence[float],
    shape: Sequence[int],
    crs: Any,
    dimensions: Sequence[str] = ("y", "x"),
) -> dict[str, Any]:
    """Build GeoZarr ``spatial:`` and ``proj:`` attributes for a grid.

    Parameters
    ----------
    transform : sequence of float
        Affine transform, at least the six elements ``a, b, c, d, e, f``
        (for example ``tuple(rasterio_dataset.transform)``).
    shape : sequence of int
        Grid ``(height, width)``.
    crs : Any
        Anything accepted by ``pyproj.CRS.from_user_input``.
    dimensions : sequence of str
        Names of the ``(y, x)`` dimensions.
    """
    a, b, c, d, e, f = (float(v) for v in tuple(transform)[:6])
    height, width = (int(v) for v in shape)
    crs = CRS.from_user_input(crs)

    corners_x = [c, a * width + c, b * height + c, a * width + b * height + c]
    corners_y = [f, d * width + f, e * height + f, d * width + e * height + f]
    bbox = [min(corners_x), min(corners_y), max(corners_x), max(corners_y)]

    attrs: dict[str, Any] = {
        "zarr_conventions": [PROJ_CONVENTION, SPATIAL_CONVENTION],
        "spatial:dimensions": list(dimensions),
        "spatial:shape": [height, width],
        "spatial:transform": [a, b, c, d, e, f],
        "spatial:transform_type": "affine",
        "spatial:bbox": bbox,
        "spatial:registration": "pixel",
        "proj:wkt2": crs.to_wkt(),
    }
    authority = crs.to_authority()
    if authority is not None:
        attrs["proj:code"] = ":".join(authority)
    return attrs


def grid_from_geozarr_attrs(attrs: Mapping[str, Any]) -> GridDefinition:
    """Construct a snail ``GridDefinition`` from GeoZarr group attributes."""
    transform_type = attrs.get("spatial:transform_type", "affine")
    if transform_type != "affine":
        raise ValueError(f"Unsupported spatial:transform_type {transform_type!r}")
    registration = attrs.get("spatial:registration", "pixel")
    if registration != "pixel":
        raise ValueError(f"Unsupported spatial:registration {registration!r}, expected 'pixel'")
    try:
        transform = tuple(float(v) for v in attrs["spatial:transform"])
        height, width = (int(v) for v in attrs["spatial:shape"])
    except KeyError as err:
        raise KeyError(f"GeoZarr attribute {err} is required to define the hazard grid") from err
    if len(transform) != 6:
        raise ValueError("spatial:transform must have six elements")

    crs = None
    for key in ("proj:wkt2", "proj:code", "proj:projjson"):
        if key in attrs:
            crs = CRS.from_user_input(attrs[key])
            break
    if crs is None:
        raise KeyError("One of proj:wkt2, proj:code or proj:projjson is required")

    return GridDefinition(crs=crs, width=width, height=height, transform=transform)


def check_coords_match_grid(x: np.ndarray, y: np.ndarray, grid: GridDefinition, rtol: float = 1e-6) -> None:
    """Check that explicit cell-centre ``x``/``y`` coordinates agree with a grid.

    Only grids without rotation can be checked like this.
    """
    a, b, c, d, e, f = grid.transform
    if b != 0 or d != 0:
        return
    if len(x) != grid.width or len(y) != grid.height:
        raise ValueError(
            f"Coordinate lengths (y={len(y)}, x={len(x)}) do not match spatial:shape ({grid.height}, {grid.width})"
        )
    expected_x = c + a * (np.arange(grid.width) + 0.5)
    expected_y = f + e * (np.arange(grid.height) + 0.5)
    atol_x = abs(a) * rtol * 1e3
    atol_y = abs(e) * rtol * 1e3
    if not np.allclose(x, expected_x, rtol=rtol, atol=atol_x):
        raise ValueError("x coordinates do not match spatial:transform")
    if not np.allclose(y, expected_y, rtol=rtol, atol=atol_y):
        raise ValueError("y coordinates do not match spatial:transform")


def with_geozarr_attrs(ds, dimensions: Sequence[str] = ("y", "x")):
    """Return a copy of an xarray Dataset with GeoZarr grid attributes.

    The dataset must have a CRS and regularly-spaced ``y``/``x`` coordinates,
    readable through the rioxarray ``.rio`` accessor.
    """
    import rioxarray  # noqa: F401, registers the .rio accessor

    y_dim, x_dim = dimensions
    ds = ds.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim)
    crs = ds.rio.crs
    if crs is None:
        raise ValueError("Dataset has no CRS; set one with ds.rio.write_crs")
    attrs = geozarr_attrs(tuple(ds.rio.transform()), (ds.sizes[y_dim], ds.sizes[x_dim]), crs, dimensions)
    # Keep any CF "spatial_ref" grid mapping too, for GDAL/rioxarray readers
    ds = ds.copy()
    if "spatial_ref" in ds.data_vars:
        ds = ds.set_coords("spatial_ref")
    if "spatial_ref" in ds.coords:
        # Zarr writers leave a grid mapping out of the "coordinates" attribute, so plain xarray
        # readers would open spatial_ref as a data variable. List it explicitly.
        for name in ds.data_vars:
            variable = ds[name].variable
            if "spatial_ref" in (variable.attrs.get("grid_mapping"), variable.encoding.get("grid_mapping")):
                variable.attrs["coordinates"] = "spatial_ref"
    ds.attrs = {**ds.attrs, **attrs}
    return ds
