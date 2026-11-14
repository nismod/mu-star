"""Jobs per H3 cell from the spatial economic activity layer.

The activity layer is wide: one row per H3 cell, one output column per sector
(columns named <sector_prefix><sector>, e.g. gva_C). Cells finer than the
zoning resolution are rolled up to it.

    jobs_cell = sum over sectors of output_cell,sector / output_per_worker_sector

Without a productivity table, total output is used as relative
attractiveness. Either way, with jobs.scale_to_employed_residents, jobs are
scaled to the total of employed residents, so that jobs and employed residents
are in the same units (the radiation model adds them).

Productivity table (CSV): sector, output_per_worker.

Output columns: h3_index, output (total), jobs, plus jobs_<sector> per sector.
"""

import click
import h3
import pandas as pd

from transport.land import h3_resolution, read_config, read_table, require_columns


@click.command()
@click.option("--config", "config_path", required=True)
@click.option("--zoning", required=True)
@click.option("--activity", required=True)
@click.option("--residents", required=True)
@click.option("--output-per-worker", "productivity_path", default="")
@click.option("--output", required=True)
def main(config_path, zoning, activity, residents, productivity_path, output):
    cfg = read_config(config_path)
    columns = cfg["inputs"]["economic_activity_columns"]
    prefix = columns["sector_prefix"]
    resolution = h3_resolution(zoning)

    act = read_table(activity)
    require_columns(act, [columns["cell"]], "economic activity layer")
    sector_columns = [c for c in act.columns if c.startswith(prefix)]
    if not sector_columns:
        raise ValueError(f"No sector columns starting with {prefix!r} in the activity layer")
    act = act[[columns["cell"], *sector_columns]].rename(columns={columns["cell"]: "cell"})
    act.columns = ["cell"] + [c[len(prefix) :] for c in sector_columns]
    act = act.drop(columns=[s for s in cfg["jobs"]["exclude_sectors"] if s in act.columns])
    sectors = [c for c in act.columns if c != "cell"]

    cell_res = {h3.get_resolution(c) for c in act["cell"].unique()}
    if any(r < resolution for r in cell_res):
        raise ValueError(f"Activity layer resolution {sorted(cell_res)} is coarser than {zoning}")
    act["h3_index"] = [h3.cell_to_parent(c, resolution) for c in act["cell"]]
    out = act.groupby("h3_index")[sectors].sum()

    if productivity_path:
        productivity = pd.read_csv(productivity_path)
        require_columns(productivity, ["sector", "output_per_worker"], "output_per_worker table")
        per_worker = productivity.set_index("sector")["output_per_worker"]
        missing = sorted(set(sectors) - set(per_worker.index))
        if missing:
            raise ValueError(f"No output_per_worker for sectors: {missing}")
        jobs = out[sectors] / per_worker[sectors]
    else:
        jobs = out[sectors].copy()
    jobs.columns = [f"jobs_{s}" for s in sectors]

    cells = pd.DataFrame({"output": out[sectors].sum(axis=1), "jobs": jobs.sum(axis=1)}).join(jobs)
    if cfg["jobs"]["scale_to_employed_residents"]:
        factor = pd.read_parquet(residents)["employed_residents"].sum() / cells["jobs"].sum()
        job_columns = ["jobs", *jobs.columns]
        cells[job_columns] = cells[job_columns] * factor
    cells.reset_index().to_parquet(output, index=False)


if __name__ == "__main__":
    main()
