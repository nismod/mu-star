# Energy developer notebooks

Visual debugging for the energy network build while developing `src/energy/`.
These notebooks only read the pipeline's standard outputs (GeoParquet, PyPSA
NetCDF, `validation.json`); the packaged `energy` model stays visualisation-free.

See [`../README.md`](../README.md) for one-time setup (nbstripout / pre-commit).

## Layout

- `_helpers.py` — energy loaders/plotters, imported as `h` in each notebook.
- `00-data-review/` — review inputs and built outputs at a glance.
- `01-build-network/` — the edit → `snakemake -c1 energy_network` → re-plot loop.

Build the products first via `01-build-network` (or `snakemake -c1 energy_network`).
