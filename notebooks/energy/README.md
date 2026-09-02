# Energy developer notebooks

Visual debugging for the energy network build while developing `src/energy/`.
These notebooks only read the pipeline's standard outputs (GeoParquet, PyPSA
NetCDF, `validation.json`); the packaged `energy` model stays visualisation-free.

See [`../README.md`](../README.md) for one-time setup (nbstripout / pre-commit).

## Layout

- `_helpers.py` — dev-only loaders/plotters, imported as `h` in each notebook
  (the leading `_` marks it internal; each notebook finds it by walking up to the
  nearest `_helpers.py`). Key functions:
  - `available_products()` / `list_products()` — which products are built.
  - `load_layers(name)` — `(nodes, edges)` GeoDataFrames for a product.
  - `load_validation(name)` / `load_pypsa(name)` / `summarise(name)`.
  - `explore_network(name)` — interactive Plotly map; `plot_network(name)` — static.
  - `MAURITIUS_BBOX` / `RODRIGUES_BBOX` — bbox constants for the `clip=` argument.
- `00-data-review/` — review inputs and built outputs at a glance.
- `01-build-network/` — the edit → `snakemake -c1 energy_network` → re-plot loop.

Build the products first via `01-build-network` (or `snakemake -c1 energy_network`).
