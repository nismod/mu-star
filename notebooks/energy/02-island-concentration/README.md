# 02 · Island-wide distribution network concentration

Ranks Village Council Areas (VCAs) and municipal wards by distribution-line
density (km of line per km² of land), finds the five most concentrated units,
and optionally maps hotspots on a regular grid. Used for the energy section of
the multi-sector concentration analysis.

## Inputs

Place these under the mu-star data folder (`data_root` in
`config/config.yaml`), or edit the paths in the notebook's configuration cell.

| Setting | Default location | What it is |
| --- | --- | --- |
| `DIST_SOURCE` | `data/incoming/energy/gridfinder/MUguess.gpkg` | Simulated distribution lines. The default is the GridFinder output; any line file works. |
| `ADMIN_LAYER` | `data/incoming/admin/mauritius_vca_wards.geojson` | VCA and municipal ward polygons, exported from OpenStreetMap with overpass-turbo. |
| `LAND_BOUNDARY` | `data/incoming/admin/mauritius_land.geojson` | Mauritius island outline, used to clip coastal units. `None` skips clipping. |

To analyse a mu-star network instead of a file, set `DIST_SOURCE` to the name
of a built product, for example `"inferred-provided-mauritius-rodrigues"`
(build it first with `snakemake -c1 build_energy_networks`). The CEB
transmission backbone is dropped, so only distribution edges are counted.

## Outputs

Written to `data/out/energy/island_concentration/` (outside git):

- `admin_density_ranked.csv`, `admin_density.gpkg`: line km, land area and density per unit
- `top5_density.png`, `density_distribution.png`, `top5_total_km.png`: figures
- `island_grid.gpkg`: hotspot grid (when `RUN_GRID = True`)

## Notes

- Lengths and areas are computed in EPSG:32740 (UTM 40S); inputs and outputs are EPSG:4326.
- `MIN_AREA_KM2` (default 2 km²) stops very small units from topping the ranking on a few kilometres of line.
