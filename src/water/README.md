# Water Allocation Model (Pywr)

This module implements the catchment water allocation model using official Pywr Cookbook components (`pywr.domains.river`, `read_dataframe`, and `Model` with the GLPK solver).

## Requirements
- Python 3.12+
- `pywr` and `pandas`

## Input Files (`src/water/data/`)
Place the following CSV files in `src/water/data/` (ignored by Git):

1. `costs.csv` — Priority costs driving water allocation (more negative = higher priority).
2. `basin_parameters.csv` — Per-basin physical settings:
   - `basin`: Catchment ID (A to Z, omitting O).
   - `reservoir_capacity_m3`: Maximum dam storage (`0.0` for run-of-river basins).
   - `dead_storage_m3`: Minimum unusable storage (`0.0` if no reservoir).
   - `hydropower_max_m3`: Maximum monthly turbine flow (`0.0` if no hydropower).
   - `mrf_target_m3`: Minimum Residual Flow ecological target (`0.0` if none).
   - `initial_storage_m3`: Starting storage volume at month 1 (`0.0` if no reservoir).
3. Monthly time-series files (wide format: `date` column in `YYYY-MM-DD` format, one column per basin):
   - `inflow_monthly_m3.csv`
   - `demand_municipal_monthly_m3.csv`
   - `demand_industrial_monthly_m3.csv`
   - `demand_irrigation_monthly_m3.csv`
4. `interbasin_transfers.csv` (optional) — Inter-basin transfer links (`from_basin`, `to_basin`, `link_name`, `max_capacity_m3`).

## Running the Model
From `src/water/`:

```bash
python model.py
```

Running `model.py` generates the calendar lookup (`data/calendar_days_in_month.csv`), builds the Pywr JSON network file (`Pywr_simplified_model.json`, with no data stored inside JSON), solves all basins and transfers simultaneously, and writes the summary CSV to `src/water/outputs/simplified_water_balance_summary.csv`. Generated JSON, data, and output files are ignored by Git.

