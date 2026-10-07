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
```

nbstripout then runs on every commit, removing outputs, kernel name and Python
version.
