import importlib
import json
import os

import pandas as pd
from pywr.dataframe_tools import read_dataframe
from pywr.model import Model

importlib.import_module("pywr.domains.river")

DEMANDS = ["municipal", "industrial", "irrigation"]
TS_FILES = {
    "inflow": "inflow_monthly_m3.csv",
    "demand_municipal": "demand_municipal_monthly_m3.csv",
    "demand_industrial": "demand_industrial_monthly_m3.csv",
    "demand_irrigation": "demand_irrigation_monthly_m3.csv",
}


def build_simplified_model(base_dir):
    reader = Model(path=base_dir)
    df_params = read_dataframe(
        reader,
        {"url": "data/basin_parameters.csv", "index_col": "basin", "encoding": "utf-8-sig"},
    )
    df_inflow = read_dataframe(
        reader,
        {
            "url": "data/inflow_monthly_m3.csv",
            "index_col": "date",
            "parse_dates": True,
            "encoding": "utf-8-sig",
        },
    )

    basins = sorted(df_inflow.columns.tolist())
    dates = df_inflow.index.strftime("%Y-%m-%d").tolist()
    annual_factor = 1.0 / ((len(dates) / 12.0) * 1e6)

    cal_path = os.path.join(base_dir, "data", "calendar_days_in_month.csv")
    pd.DataFrame(
        {"date": dates, "days_in_month": df_inflow.index.days_in_month.astype(float)}
    ).to_csv(cal_path, index=False, encoding="utf-8")

    tr_path = os.path.join(base_dir, "data", "interbasin_transfers.csv")
    df_tr = (
        read_dataframe(reader, {"url": "data/interbasin_transfers.csv", "encoding": "utf-8-sig"})
        if os.path.exists(tr_path)
        else pd.DataFrame()
    )

    tables = {
        "costs": {"url": "data/costs.csv", "index_col": "component", "encoding": "utf-8-sig"},
        "basin_parameters": {
            "url": "data/basin_parameters.csv",
            "index_col": "basin",
            "encoding": "utf-8-sig",
        },
        "calendar": {
            "url": "data/calendar_days_in_month.csv",
            "index_col": "date",
            "parse_dates": True,
            "encoding": "utf-8-sig",
        },
        **{
            k: {
                "url": f"data/{v}",
                "index_col": "date",
                "parse_dates": True,
                "encoding": "utf-8-sig",
            }
            for k, v in TS_FILES.items()
        },
    }
    if not df_tr.empty:
        tables["interbasin_transfers"] = {
            "url": "data/interbasin_transfers.csv",
            "index_col": "link_name",
            "encoding": "utf-8-sig",
        }

    parameters = {
        "days_in_month": {"type": "dataframe", "table": "calendar", "column": "days_in_month"}
    }
    used_costs = set()
    used_zero = False

    def cost_ref(comp):
        used_costs.add(comp)
        return f"cost_{comp}"

    nodes = []
    edges = []
    recorders = {}

    for b in basins:
        row = df_params.loc[b]
        has_res = float(row["reservoir_capacity_m3"]) > 0
        has_hydro = float(row["hydropower_max_m3"]) > 0
        has_mrf = float(row["mrf_target_m3"]) > 0
        store = f"reservoir_{b}" if has_res else f"junction_{b}"

        parameters[f"raw_inflow_{b}"] = {"type": "dataframe", "table": "inflow", "column": b}
        parameters[f"inflow_{b}"] = {
            "type": "division",
            "numerator": f"raw_inflow_{b}",
            "denominator": "days_in_month",
        }
        nodes.append({"name": f"catchment_{b}", "type": "catchment", "flow": f"inflow_{b}"})
        recorders[f"inflow_Mm3_{b}"] = {
            "type": "totalflownoderecorder",
            "node": f"catchment_{b}",
            "factor": annual_factor,
        }

        if has_res:
            nodes.append(
                {
                    "name": store,
                    "type": "reservoir",
                    "max_volume": {
                        "type": "constant",
                        "table": "basin_parameters",
                        "index": b,
                        "column": "reservoir_capacity_m3",
                    },
                    "min_volume": {
                        "type": "constant",
                        "table": "basin_parameters",
                        "index": b,
                        "column": "dead_storage_m3",
                    },
                    "initial_volume": {
                        "table": "basin_parameters",
                        "index": b,
                        "column": "initial_storage_m3",
                    },
                    "cost": cost_ref("reservoir"),
                }
            )
        else:
            nodes.append({"name": store, "type": "river"})
        edges.append([f"catchment_{b}", store])
        nodes.append({"name": f"river_{b}", "type": "river"})

        if has_hydro:
            station = f"hydropower_station_{b}"
            parameters[f"raw_hmax_{b}"] = {
                "type": "constant",
                "table": "basin_parameters",
                "index": b,
                "column": "hydropower_max_m3",
            }
            parameters[f"hydro_flow_{b}"] = {
                "type": "division",
                "numerator": f"raw_hmax_{b}",
                "denominator": "days_in_month",
            }
            nodes.append(
                {
                    "name": station,
                    "type": "piecewiselink",
                    "nsteps": 2,
                    "max_flows": [f"hydro_flow_{b}", None],
                    "costs": [cost_ref("hydropower"), 0.0],
                }
            )
            edges.extend([[store, station], [station, f"river_{b}"]])
            recorders[f"hydro_Mm3_{b}"] = {
                "type": "totalflownoderecorder",
                "node": f"{station} Sublink 0",
                "factor": annual_factor,
            }
        else:
            edges.append([store, f"river_{b}"])
            recorders[f"hydro_Mm3_{b}"] = {"type": "meanparameterrecorder", "parameter": "zero"}
            used_zero = True

        for d in DEMANDS:
            parameters[f"raw_demand_{d}_{b}"] = {
                "type": "dataframe",
                "table": f"demand_{d}",
                "column": b,
            }
            parameters[f"demand_{d}_{b}"] = {
                "type": "division",
                "numerator": f"raw_demand_{d}_{b}",
                "denominator": "days_in_month",
            }
            nodes.append(
                {
                    "name": f"demand_{d}_{b}",
                    "type": "output",
                    "max_flow": f"demand_{d}_{b}",
                    "cost": cost_ref(f"demand_{d}"),
                }
            )
            edges.append([f"river_{b}", f"demand_{d}_{b}"])
            recorders[f"{d}_Mm3_{b}"] = {
                "type": "totalflownoderecorder",
                "node": f"demand_{d}_{b}",
                "factor": annual_factor,
            }
            recorders[f"def_{d}_{b}"] = {
                "type": "deficitfrequencynoderecorder",
                "node": f"demand_{d}_{b}",
            }

        recorders[f"demands_Mm3_{b}"] = {
            "type": "aggregatedrecorder",
            "recorders": [f"{d}_Mm3_{b}" for d in DEMANDS],
            "agg_func": "sum",
        }

        parameters[f"raw_mrf_{b}"] = {
            "type": "constant",
            "table": "basin_parameters",
            "index": b,
            "column": "mrf_target_m3",
        }
        parameters[f"mrf_{b}"] = {
            "type": "division",
            "numerator": f"raw_mrf_{b}",
            "denominator": "days_in_month",
        }
        nodes.append(
            {
                "name": f"environmental_mrf_{b}",
                "type": "rivergauge",
                "mrf": f"mrf_{b}",
                "mrf_cost": cost_ref("environmental_mrf"),
            }
        )
        nodes.append({"name": f"sea_outlet_{b}", "type": "output", "cost": cost_ref("sea_outlet")})
        edges.extend(
            [[f"river_{b}", f"environmental_mrf_{b}"], [f"environmental_mrf_{b}", f"sea_outlet_{b}"]]
        )
        recorders[f"sea_Mm3_{b}"] = {
            "type": "totalflownoderecorder",
            "node": f"sea_outlet_{b}",
            "factor": annual_factor,
        }
        if has_mrf:
            recorders[f"def_mrf_{b}"] = {
                "type": "deficitfrequencynoderecorder",
                "node": f"environmental_mrf_{b} Sublink 0",
            }
        else:
            recorders[f"def_mrf_{b}"] = {"type": "meanparameterrecorder", "parameter": "zero"}
            used_zero = True

    tr_in = {b: [] for b in basins}
    tr_out = {b: [] for b in basins}
    for _, tr in df_tr.iterrows():
        src_b = tr["from_basin"].strip().upper()
        dst_b = tr["to_basin"].strip().upper()
        link_name = tr["link_name"].strip()
        node_name = f"transfer_{link_name}"
        tr_out[src_b].append(link_name)
        tr_in[dst_b].append(link_name)
        src_store = (
            f"reservoir_{src_b}"
            if float(df_params.loc[src_b, "reservoir_capacity_m3"]) > 0
            else f"junction_{src_b}"
        )
        dst_store = (
            f"reservoir_{dst_b}"
            if float(df_params.loc[dst_b, "reservoir_capacity_m3"]) > 0
            else f"junction_{dst_b}"
        )
        parameters[f"raw_{node_name}"] = {
            "type": "constant",
            "table": "interbasin_transfers",
            "index": link_name,
            "column": "max_capacity_m3",
        }
        parameters[f"max_flow_{node_name}"] = {
            "type": "division",
            "numerator": f"raw_{node_name}",
            "denominator": "days_in_month",
        }
        nodes.append(
            {
                "name": node_name,
                "type": "link",
                "max_flow": f"max_flow_{node_name}",
                "cost": cost_ref("interbasin_transfer"),
            }
        )
        edges.extend([[src_store, node_name], [node_name, dst_store]])
        recorders[f"tr_Mm3_{link_name}"] = {
            "type": "totalflownoderecorder",
            "node": node_name,
            "factor": annual_factor,
        }

    for b in basins:
        for side, links in [("transfer_in_Mm3", tr_in[b]), ("transfer_out_Mm3", tr_out[b])]:
            if links:
                recorders[f"{side}_{b}"] = {
                    "type": "aggregatedrecorder",
                    "recorders": [f"tr_Mm3_{ln}" for ln in links],
                    "agg_func": "sum",
                }
            else:
                recorders[f"{side}_{b}"] = {"type": "meanparameterrecorder", "parameter": "zero"}
                used_zero = True

    if used_zero:
        parameters["zero"] = {"type": "constant", "value": 0.0}
    for comp in sorted(used_costs):
        parameters[f"cost_{comp}"] = {
            "type": "constant",
            "table": "costs",
            "index": comp,
            "column": "cost",
        }

    schema = {
        "metadata": {"title": "Simplified Pywr water allocation model", "minimum_version": "1.0"},
        "timestepper": {"start": dates[0], "end": dates[-1], "timestep": "M"},
        "solver": {"name": "glpk"},
        "tables": tables,
        "parameters": parameters,
        "nodes": nodes,
        "edges": edges,
        "recorders": recorders,
    }
    return basins, schema


def run_simplified():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    basins, schema = build_simplified_model(base_dir)
    json_path = os.path.join(base_dir, "Pywr_simplified_model.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)

    model = Model.load(json_path)
    model.check()
    model.run()

    rec = model.recorders
    rows = [
        {
            "basin": b,
            "inflow_Mm3_yr": round(float(rec[f"inflow_Mm3_{b}"].aggregated_value()), 2),
            "transfer_in_Mm3_yr": round(float(rec[f"transfer_in_Mm3_{b}"].aggregated_value()), 2),
            "transfer_out_Mm3_yr": round(float(rec[f"transfer_out_Mm3_{b}"].aggregated_value()), 2),
            "demands_delivered_Mm3_yr": round(float(rec[f"demands_Mm3_{b}"].aggregated_value()), 2),
            "municipal_Mm3_yr": round(float(rec[f"municipal_Mm3_{b}"].aggregated_value()), 2),
            "industrial_Mm3_yr": round(float(rec[f"industrial_Mm3_{b}"].aggregated_value()), 2),
            "irrigation_Mm3_yr": round(float(rec[f"irrigation_Mm3_{b}"].aggregated_value()), 2),
            "sea_discharge_Mm3_yr": round(float(rec[f"sea_Mm3_{b}"].aggregated_value()), 2),
            "hydropower_Mm3_yr": round(float(rec[f"hydro_Mm3_{b}"].aggregated_value()), 2),
            "mrf_reliability_pct": round((1.0 - float(rec[f"def_mrf_{b}"].aggregated_value())) * 100.0, 1),
            "municipal_reliability_pct": round(
                (1.0 - float(rec[f"def_municipal_{b}"].aggregated_value())) * 100.0, 1
            ),
            "industrial_reliability_pct": round(
                (1.0 - float(rec[f"def_industrial_{b}"].aggregated_value())) * 100.0, 1
            ),
            "irrigation_reliability_pct": round(
                (1.0 - float(rec[f"def_irrigation_{b}"].aggregated_value())) * 100.0, 1
            ),
        }
        for b in basins
    ]

    out_dir = os.path.join(base_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, "simplified_water_balance_summary.csv")
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"Solved {len(basins)} basins -> {out_csv}")


if __name__ == "__main__":
    run_simplified()
