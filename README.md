# mu-star

Mauritius - Systemic Tool for the Analysis of (infrastructure) Risk

This repository contains project-specific codes and configuration to run
climate-related risk and resilience analysis of infrastructure networks in
Mauritius.

## Setup and installation

Clone or download this repository from
[GitHub](https://github.com/nismod/mu-star):
    git clone git@github.com:nismod/mu-star.git

Next, install required dependencies. We recommend using
[`micromamba`](https://mamba.readthedocs.io/en/latest/user_guide/micromamba.html)
to install these into a conda environment.

Create a conda environment once (per machine/user):
    micromamba create --file environment.yaml

## Usage

The principal goal of this repository is to produce analysis results, (damages,
losses and adaptation options) that can be visualised in an online viewer akin
to [infra-risk-vis](https://github.com/nismod/infra-risk-vis.git).

The analysis is comprised of Python scripts wrapped in
[snakemake](https://snakemake.readthedocs.io/) rules. Snakemake is a workflow
management system that can be used to break up complex modelling chains and
improve the reproducibility of analyses. To invoke a rule, call `snakemake`
followed by the file you want to produce.

Some functionality is contained within a helper Python module, located in
`src/mu_star`.

### Environment

To make snakemake, the helper Python module and other software dependencies
available, activate the environment we previously created.
    micromamba activate mu-star

### Configuration

Analysis options can be configured using the `config/config.yaml` file. See it
for inline documentation.

### Invocation

We use `snakemake` to drive any analysis. It's best explained with an example.
To invoke the rule (and all necessary predecessor rules) to compute direct
damage (rehabiliation) costs to the road network due to fluvial flooding, we'd
run the following:
    snakemake --dry-run -c1 data/out/damage/layer-road/rp/peril-flood/subperil-fluvial/ensemble-0/damage.zarr

Note that the `--dry-run` flag asks `snakemake` to report on what work (if any)
is necessary. As jobs can be long running, this is useful to check beforehand.
To actually run the rules, remove the dry run flag.

The `--cores` flag indicates how many processors `snakemake` will use to execute
the rules. If rules do not depend on one another and enough processors are
available, they may execute simultaneously. Also, some rules invoke scripts that
are parallelised and can make use more than one processor themselves.

Parts of the requested file path are parameterisable, e.g. `layer-road`,
`peril-flood`, `subperil-fluvial`. By varying these different outputs can be
produced.

### Available rules

See `.smk` files in `workflow/` for available rules and their required input and
output files.

While this workflow is in development, some of the rules are placeholders.

### Testing

Test the helper library by activating the environment and running the following
from this directory:
    pytest src/mu_star

## Acknowledgements

Funded by the Foreign and Commonwealth Development Office (FCDO) of the United
Kingdom.
