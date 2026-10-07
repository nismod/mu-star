# Energy notebooks

Two notebooks for checking the energy build while working on `src/energy/`:

- `00_inputs.ipynb`: the inputs (CEB data, OpenStreetMap, night lights). It
  builds and downloads nothing; a cell whose file is missing prints the command
  that makes it.
- `01_networks.ipynb`: build the networks, from a cell or the terminal, then
  look at one of them: summary, map, node and edge tables, validation report
  and PyPSA network.

After editing `src/energy/`, rebuild with `snakemake -c1 build_energy_networks`
(or set `RUN_BUILD = True` in `01_networks.ipynb`) and rerun `01_networks.ipynb`.

Both import `_helpers.py` as `h`, which takes its paths from
`config/config.yaml` and `config/energy/energy.yaml`; its docstring lists the
helpers. Plotting code stays here, not in `src/energy/`. Maps for users are in
the separate viewer, [nismod/irv-standalone](https://github.com/nismod/irv-standalone).

Open the notebooks with Jupyter in the `mu-star` environment. How to get the
data is in "Data and running the model" in `docs/src/infrastructure-energy.md`;
for the one-time notebook setup (nbstripout, pre-commit) see
[`../README.md`](../README.md).
