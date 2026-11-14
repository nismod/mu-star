"""Jobs per H3 cell from the spatial economic activity layer (GVA by sector).

jobs_cell = sum over sectors of GVA_cell,sector / GVA_per_worker_sector

If no GVA-per-worker table is given, total GVA is used as relative
attractiveness. Either way, with jobs.scale_to_employed_residents the total is
scaled to the total employed residents, so that jobs and employed residents
are in the same units (the radiation model mixes them).

Expected inputs (TODO: confirm with the economics workflow):
- activity: long table with cell, sector and value columns (names in config);
  cells at the zoning resolution or finer.
- gva_per_worker: CSV with columns sector, gva_per_worker (same currency/year as activity).

Output columns: h3_index, gva, jobs, plus jobs_<sector> per sector.
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
@click.option("--gva-per-worker", default="")
@click.option("--output", required=True)
def main(config_path, zoning, activity, residents, gva_per_worker, output):
    cfg = read_config(config_path)
    columns = cfg["inputs"]["economic_activity_columns"]
    resolution = h3_resolution(zoning)

    act = read_table(activity)
    require_columns(act, columns.values(), "economic activity layer")
    act = act.rename(columns={v: k for k, v in columns.items()})[["cell", "sector", "value"]]
    act = act[~act["sector"].isin(cfg["jobs"]["exclude_sectors"])]

    cell_res = {h3.get_resolution(c) for c in act["cell"].unique()}
    if any(r < resolution for r in cell_res):
        raise ValueError(f"Activity layer resolution {sorted(cell_res)} is coarser than {zoning}")
    act["h3_index"] = [h3.cell_to_parent(c, resolution) for c in act["cell"]]

    if gva_per_worker:
        productivity = pd.read_csv(gva_per_worker)
        require_columns(productivity, ["sector", "gva_per_worker"], "gva_per_worker table")
        act = act.merge(productivity, on="sector", how="left")
        missing = act.loc[act["gva_per_worker"].isna(), "sector"].unique()
        if len(missing):
            raise ValueError(f"No gva_per_worker for sectors: {sorted(missing)}")
        act["jobs"] = act["value"] / act["gva_per_worker"]
    else:
        act["jobs"] = act["value"]

    by_sector = act.pivot_table(index="h3_index", columns="sector", values="jobs", aggfunc="sum", fill_value=0)
    by_sector.columns = [f"jobs_{sector}" for sector in by_sector.columns]
    cells = act.groupby("h3_index").agg(gva=("value", "sum"), jobs=("jobs", "sum")).join(by_sector)

    if cfg["jobs"]["scale_to_employed_residents"]:
        total_workers = pd.read_parquet(residents)["employed_residents"].sum()
        factor = total_workers / cells["jobs"].sum()
        job_columns = ["jobs", *list(by_sector.columns)]
        cells[job_columns] = cells[job_columns] * factor

    cells.reset_index().to_parquet(output, index=False)


if __name__ == "__main__":
    main()
