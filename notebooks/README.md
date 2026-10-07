# Notebooks

Notebooks for looking at the workflow's files while working on the code in
`src/`. No Snakemake rule runs them. Maps for users are in the separate viewer,
[nismod/irv-standalone](https://github.com/nismod/irv-standalone).

There is one folder per infrastructure system, with its own `_helpers.py`.

## Setup

Once per clone, in the `mu-star` environment:

```shell
pre-commit install
nbstripout --install
git config filter.nbstripout.extrakeys "metadata.kernelspec metadata.language_info"
```

Git then stores notebooks without their outputs, kernel name or Python version,
so running a notebook does not show up as a change.
