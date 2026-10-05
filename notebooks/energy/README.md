# Energy notebooks

Two notebooks for checking the energy build while working on `src/energy/`:

- `00-data-review/`: the inputs (CEB data, OpenStreetMap, night lights).
- `01-build-network/`: build the networks, then look at one of them.

They read the files the workflow writes; notebook 01 can also run the build.
Both import `_helpers.py` as `h`, which takes its paths from
`config/config.yaml` and `config/energy/energy.yaml`; its docstring lists the
helpers. Plotting code stays here, not in `src/energy/`. Maps for users are in
the separate viewer, [nismod/irv-standalone](https://github.com/nismod/irv-standalone).

Open the notebooks with Jupyter in the `mu-star` environment. For the one-time
setup (nbstripout, pre-commit) see [`../README.md`](../README.md).
