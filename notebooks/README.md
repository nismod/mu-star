# Developer notebooks

Local, throwaway notebooks for **visual debugging** while developing the model
packages in `src/`. They read the pipeline's standard outputs and render them —
they are not part of any Snakemake rule, and no plotting code belongs in the
packages themselves. Production visualisation is the separate viewer,
[nismod/irv-standalone](https://github.com/nismod/irv-standalone).

## Layout

One subfolder per infrastructure system, mirroring `src/<system>/` and
`docs/src/infrastructure-<system>.md`:

- `energy/` — energy network build & review notebooks.
- _(future)_ `transport/`, `water/` — add alongside as those systems grow.

Each system folder owns its own `_helpers.py` (imported as `h`) so notebooks
stay decoupled across systems.

## Setup (once per clone)

Notebook outputs are stripped on commit so `.ipynb` files stay diff-clean:

```shell
micromamba activate mu-star
pre-commit install          # enables the nbstripout hook on commit
nbstripout --install        # also enables the git clean filter (see .gitattributes)
```

Then open a system's notebooks and run top-to-bottom.
