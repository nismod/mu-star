# Contributing to mu-star

Thank you for working on Mauritius STAR. This guide is written primarily for
researchers collaborating on the project — whether you focus on a single sector
(power, water, transport) or a stage in the analysis (hazards, damages, losses,
adaptation).

The most important thing to know up front:

> Open a draft pull request, push unfinished work to it, don't wait until your
> code is "good enough", we can always discuss, review and iterate. See [Ways of
> working](#ways-of-working) below.

## Contents

- [First session](#first-session)
- [Ways of working](#ways-of-working)
- [Branches and pull requests](#branches-and-pull-requests)
- [Where your work goes](#where-your-work-goes)
- [Definition of done](#definition-of-done)
- [Getting data](#getting-data)
- [Issues and parcels of work](#issues-and-parcels-of-work)
- [Getting help](#getting-help)

## First session

The goal of your first session is to get the project running and open one quick
draft pull request. This should set you up well for more substantial
contributions later.

1. **Clone the repository:**

   ```shell
   git clone git@github.com:nismod/mu-star.git
   cd mu-star
   ```

2. **Create the environment** (once per machine; we recommend using
   [`micromamba`](https://mamba.readthedocs.io/en/latest/user_guide/micromamba.html)):

   ```shell
   micromamba create --file environment.yaml
   micromamba activate mu-star
   ```

3. **Run the tests** to confirm your setup works:

   ```shell
   python -m pytest src/
   ```

4. **Dry-run a small workflow target** to see how Snakemake looks for output
  files and selects workflow rules to run (dry-run means no rules will actually
  run):

   ```shell
   snakemake --dry-run --cores 1 \
     data//processed/networks/transport/airport-areas.geoparquet
   ```

5. **Open a draft pull request.**
  - [ ] Create a branch
  - [ ] Add yourself to the file `CITATION.cff`
  - [ ] (Optionally) Find a typo in the code or docs and fix it.
  - [ ] Push your branch
  - [ ] Open a **draft** pull request and when you're ready tag @tomalrussell
    and/or @thomas-fred for review.

  This is a rehearsal. It lets you practice using git and GitHub with super low
  stakes, but it also registers you as a contributor to the project, and maybe
  fixes a small typo, which is useful. See [Branches and pull
  requests](#branches-and-pull-requests) for more.

If any step fails, that is itself worth reporting — open an issue or ask (see
[Getting help](#getting-help)). A confusing setup step is a bug in this guide.

## Ways of working

We're aiming for **small, frequent, visible changes** over large, private,
polished ones.

- **Draft pull requests are the default.** Open one as soon as you start a
  parcel of work and push to it as you go. Nobody expects the first push to be
  finished or correct. Marking a pull request "ready for review" is a separate,
  later step.
- **Small pull requests get merged faster.** Aim for something a reviewer can
  read in under about 30 minutes. If a task is getting large, try to split it.
- **Review is a chance to share early.** A review is a colleague pairing with you on
  your code, catching things early, and sharing context — not a judgement. Ask
  for a review early and often.
- **There is super low risk of breaking anyone else's work.** Sectors and stages live in
  separate directories (see [Where your work goes](#where-your-work-goes)), so
  your changes are contained. It is very hard to break `development` for someone
  else.
- **Prefer working in the open.** A half-finished branch pushed to GitHub is
  more useful to the team than perfect code on your laptop, because others can
  see it, learn from it, and help.

If you are ever unsure whether something is "ready" to push — push it. That is
what the draft state is for.

## Branches and pull requests

The default branch is `development`. All work happens on branches and is merged
via pull request; nobody commits directly to `development`.

1. **Start from an up-to-date `development`:**

   ```shell
   git switch development
   git pull origin development
   ```

2. **Create a branch.** Use a short, descriptive name. A loose convention:
   `<feat-or-fix>/<sector-or-stage>-<short-description>`, e.g. `feat/power-pypsa-adapter`,
   `fix/losses-rerouting-cost`, `docs/data-access`.

   ```shell
   git switch -c feat/power-pypsa-adapter
   ```

3. **Commit small, logical chunks** with clear messages (imperative mood, e.g.
   "Add PyPSA network loader"). Push early:

   ```shell
   git push -u origin feat/power-pypsa-adapter
   ```

4. **Open a draft pull request** against `development` on the GitHub website (or
   with the `gh` CLI tool: `gh pr create --draft --fill`). Fill in the pull request
   template. Link the issue it addresses (e.g. "Closes #42").

5. **Keep pushing** as you work. When it's ready for a colleague to look at,
   mark it "Ready for review" and request a review from the relevant
   [code owner](.github/CODEOWNERS).

6. **Respond to review, then merge.** Once approved and CI is green, merge
   (squash-merge keeps history tidy) and delete the branch.

**Forks vs branches:** if you have write access to `nismod/mu-star`, work on a
branch in the repository — it's simpler and lets others push to your branch to
help. Use a fork only if you don't have write access.

## Where your work goes

The repository is laid out so that each sector and stage has its own space. If
you're a sector RA, you'll mostly touch files under your sector's name and
rarely need to edit shared files:

```
src/<sector>/                        # your model package (e.g. src/transport)
workflow/<stage>/<sector>.smk        # Snakemake rules that call your model
config/<sector>/                     # sector-specific parameters
docs/src/infrastructure-<sector>.md  # sector documentation
src/mu_star/                         # shared helper library (interfaces, utils)
```

Shared files (`workflow/Snakefile`, `config/config.yaml`, `src/mu_star/`,
`environment.yaml`) are edited more carefully and are owned by the
maintainers — see [`.github/CODEOWNERS`](.github/CODEOWNERS). Changes there are
welcome; just expect a closer review.

## Definition of done

A pull request is ready to merge when:

- [ ] It does one thing, described in the pull request title and description.
- [ ] Tests pass locally (`pytest src/mu_star`) and in GitHub Actions (green tick).
- [ ] There are new tests for new functionality (and bug fixes), or we discuss example outputs above or offline
- [ ] Python code passes automatic checks (`ruff format .` and `ruff check .`) ;
      these run via [pre-commit](https://pre-commit.com/) if you've installed it
      with `pre-commit install`).
- [ ] Documentation is updated if usage/functionality changed
- [ ] A code owner has approved.

This is a checklist to make "done" legible, not a bar to clear before you open a
pull request. Open the draft first; tick these off as you go.

## Getting data

Model input data and results are held **separately from this repository**, under
the relevant licence agreements — they are not in git. To run anything beyond
the tests you will need access to the shared data.

<!-- MAINTAINERS: please complete with the real location and request process. -->
> **How to get access:** ask a maintainer (see [Getting help](#getting-help))
> for access to the shared data store and where to place it locally. Document
> the location and request steps here so the next RA doesn't have to ask.

Locally, data follows this convention (referenced by the Snakemake paths):

```
data/incoming/    # source data, as downloaded — never edited by hand
data/processed/   # intermediate data produced by preprocessing rules
data/results/         # analysis results
```

## Issues and parcels of work

Work is organised as issues. A good issue is a self-contained parcel:

- **Use an issue template** when opening one (sector model task, bug, or data
  request) — the template prompts for the information a contributor needs.
- **State the deliverable concretely.** Because the workflow is target-driven,
  the clearest tasks name the file to produce, e.g. *"produce
  `data/results/loss/layer-power/.../losses.zarr` for the power network"*.
- **Label by sector and stage** (`sector:power`, `stage:loss`, …) so RAs can
  filter to their slice. `good-first-issue` marks approachable starting points.
- **Keep parcels small** — roughly what one person can land in a few days as one
  pull request. If it's bigger, break it into sub-issues.

## Getting help

- **Ask early.** A five-minute question is cheaper than a day stuck. Email is
  usually best for quick queries, we can follow up with a call.
- Open an issue for anything reproducible (a bug, a confusing doc, a setup
  failure) that needs fixing in the code or documentation.
- For design questions, open a draft pull request or a discussion and tag a
  maintainer.
- Maintainers: @tomalrussell, @thomas-fred.

Welcome aboard.
