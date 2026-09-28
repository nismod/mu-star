# Energy developer notebooks

Two notebooks for looking at the energy network build while developing
`src/energy/`: one for the inputs, one for building and inspecting the products.
They only *read* the files the workflow writes; the packaged `energy` model stays
visualisation-free (production maps live in the separate viewer,
[nismod/irv-standalone](https://github.com/nismod/irv-standalone)).

See [`../README.md`](../README.md) for the one-time setup (nbstripout /
pre-commit). Open the notebooks with Jupyter from the `mu-star` environment; each
finds `_helpers.py` by walking up from its own folder.

## Layout

- `_helpers.py` — dev-only loaders and plotters, imported as `h` in both
  notebooks (the leading `_` marks it internal). It reads the data root from
  `config/config.yaml` and the region from `config/energy/energy.yaml`, so it
  always looks where the workflow writes:
  - constants: `DATA_ROOT`, `REGION`, `PROVIDED_DIR`, `OSM_CACHE_DIR`,
    `NIGHTLIGHT_DIR`, `NETWORKS_DIR`, `OUT_DIR`, plus `MAURITIUS_BBOX` /
    `RODRIGUES_BBOX` for the `clip=` argument;
  - inputs: `load_provided()`, `load_osm_cache()`, `nightlight_paths()`,
    `load_nightlight_targets()`, and `found(path, how)`, which prints the
    command that creates a missing file;
  - built products: `available_products()` / `list_products()`,
    `load_layers(name)`, `load_validation(name)`, `load_pypsa(name)`,
    `summarise(name)`;
  - maps: `explore_network(name)` (interactive Plotly), `plot_network(name)`
    (static), `quick_map(**layers)` (static, any GeoDataFrames).
- `00-data-review/` — the **inputs**: cleaned CEB data, OpenStreetMap cache,
  night lights. Runs on a fresh clone; a missing file prints the command that
  creates it.
- `01-build-network/` — **build, then inspect** one product: how the rule chain
  works, an optional in-notebook build, then summary, interactive map, node and
  edge tables, validation report and PyPSA view.

Typical loop: edit `src/energy/`, rebuild with
`snakemake -c1 build_energy_networks`, re-run
`01-build-network/01_build_network.ipynb`.
