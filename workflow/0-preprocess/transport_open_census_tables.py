"""Census tables (2022) from the Statistics Mauritius workbooks, in the
transcription schemas of docs/src/transport-data.md (items 1, 2, 4, 5, 6).

Inputs
    Volume VII Economic Characteristics (HPC_TR_Vol7_Economic_Yr22_301224.xls):
        CA1 (population 16+ and employed by area), CA27 (employed by residence
        and workplace district), Appendix II (area codes), Appendix IV
        (Rodrigues regions).
    Volume II Demography (HPC_TR_Vol2_Demography_Yr22.xlsx):
        D2 (population by area, sex and 5-year age band), D1 (by district and
        single year of age).

Outputs
    CA1-employment-by-area.csv: one row per printed CA1 block (both sexes):
        area_name (the official Appendix II-IV name on area rows, so that
        transport_census_zones.py matches it with no overrides; the printed
        heading on aggregate rows), printed_name (as printed), district (config name; blank for national
        rows), row_type (area | aggregate), employed, population (D2, all ages,
        both sexes), plus district_code, level, segment, employed_male,
        employed_female, population_16_plus, zone_id, zone_name,
        match_method, source, population_source.
    appendix-ii-iv-area-codes.csv: zone_id, zone_name, district_code, district,
        island_code, in_ca1, note, source. Rodrigues regions get the synthetic
        codes 2101-2106 (see the note column).
    CA27-residence-by-workplace-district.csv: residence_district, sex,
        age_group (All ages on the sex total rows), Total, nine districts,
        Not stated, Rodrigues and Outer Islands, Outside Mauritius, plus
        residence_district_code, row_type (district | aggregate), source.
    children-by-area-age.csv: area_name (official Appendix name for area rows,
        so that transport_school_children.py matches it), printed_name,
        district, row_type, zone_id, age_band, children, method, source.
        Printed D2 bands <1, 1-4, 5-9, 10-14, 15-19, plus 10-11 and 12-14
        derived by splitting each area's 10-14 by its district's single-year
        shares (D1).
    matches CSV: every CA1 area row with its code and how it was matched.
    validation JSON: every aggregate check and the key totals.
    review CSV (log): anything unmatched, ambiguous or failing a check. The
        script fails if it is not empty.
"""

import json
from pathlib import Path

import click
import pandas as pd

from transport import open_census as oc

CHILD_BANDS = ["<1", "1-4", "5-9", "10-14", "15-19"]


def read_sheets(path, sheets):
    return pd.read_excel(path, sheet_name=sheets, header=None)


def district_name(code):
    if code is None or pd.isna(code):
        return ""
    return oc.DISTRICTS.get(code, oc.RODRIGUES_NAME if code == oc.RODRIGUES_DISTRICT_CODE else code)


@click.command()
@click.option("--vol7", required=True, help="Vol VII Economic Characteristics workbook (.xls)")
@click.option("--vol2", required=True, help="Vol II Demography workbook (.xlsx)")
@click.option("--out-ca1", required=True)
@click.option("--out-codes", required=True)
@click.option("--out-ca27", required=True)
@click.option("--out-children", required=True)
@click.option("--out-matches", required=True)
@click.option("--out-validation", required=True)
@click.option("--review", required=True, help="review CSV (written even when the script fails)")
def main(vol7, vol2, out_ca1, out_codes, out_ca27, out_children, out_matches, out_validation, review):
    v7 = read_sheets(vol7, ["CA1", "CA27", "APPENDIX II", "APPENDIX IV"])
    v2 = read_sheets(vol2, ["D1", "D2"])
    vol7_name, vol2_name = Path(vol7).name, Path(vol2).name
    issues = []

    def issue(kind, item, detail):
        issues.append({"kind": kind, "item": item, "detail": detail})

    # --- Parse and check the totals ------------------------------------------------
    ca1 = oc.parse_ca1(v7["CA1"])
    d2, bands = oc.parse_d2(v2["D2"])
    d1 = oc.parse_d1_single_years(v2["D1"])
    ca27 = oc.parse_ca27(v7["CA27"])
    appendix = oc.parse_appendix_ii(v7["APPENDIX II"])
    regions = oc.parse_appendix_iv_regions(v7["APPENDIX IV"])

    checks = {
        "CA1": oc.check_area_totals(ca1, ["population_16_plus", "employed"]),
        "D2": oc.check_area_totals(d2, ["all_ages", *bands]),
        "CA27": oc.check_ca27(ca27),
    }
    d2_rows = d2.copy()
    d2_rows["band_sum"] = d2_rows[bands].sum(axis=1)
    bad = int(((d2_rows["band_sum"] - d2_rows["all_ages"]).abs() >= 0.5).sum())
    checks["D2"].append(oc.Check(f"all ages = sum of age bands (rows failing: {bad})", 0, bad))

    # CA27 district totals against CA1 district employed (same population: employed 16+).
    ca1_district = ca1[ca1["level"] == "district"].set_index(["district_code", "sex"])["employed"]
    ca27_district = ca27[(ca27["age_group"] == oc.ALL_AGES) & ca27["district_code"].notna()]
    for r in ca27_district.itertuples():
        checks["CA27"].append(
            oc.Check(
                f"CA27 {r.heading} {r.sex} Total = CA1 district employed",
                ca1_district[(r.district_code, r.sex)],
                r.Total,
            )
        )
    # D1 single years against D2 bands, per district.
    d2_district = d2[(d2["level"].isin(["district", "island", "republic"])) & (d2["segment"] == "all")]
    d2_district = d2_district[d2_district["sex"] == "Both sexes"]
    for _, r in d2_district.iterrows():
        singles = d1[
            (d1["level"] == r["level"])
            & (d1["district_code"].fillna("") == ("" if pd.isna(r["district_code"]) else r["district_code"]))
        ]
        for band, ages in (("5-9", range(5, 10)), ("10-14", range(10, 15)), ("15-19", range(15, 20))):
            value = singles[singles["age"].isin(ages)]["population"].sum()
            checks["D2"].append(oc.Check(f"D1 single years {band} = D2 {r['heading']}", r[band], value))
    for table, items in checks.items():
        for c in oc.failed(items):
            issue("check_failed", table, f"{c.name}: published {c.expected}, sum {c.actual}")

    # --- D2 blocks aligned with CA1 blocks -------------------------------------------
    ca1_both = ca1[ca1["sex"] == "Both sexes"].reset_index(drop=True)
    d2_both = d2[d2["sex"] == "Both sexes"].reset_index(drop=True)
    if len(ca1_both) != len(d2_both):
        issue("alignment", "CA1/D2", f"{len(ca1_both)} CA1 blocks but {len(d2_both)} D2 blocks")
    else:
        for i, (a, b) in enumerate(zip(ca1_both.itertuples(), d2_both.itertuples())):
            same_name = a.level != "area" or oc.simplify(a.heading) == oc.simplify(b.heading)
            if (a.level, a.district_code, a.segment) != (b.level, b.district_code, b.segment) or not same_name:
                issue("alignment", f"block {i}", f"CA1 {a.heading!r} vs D2 {b.heading!r}")

    # --- Codes ----------------------------------------------------------------------
    codes = pd.concat([appendix, regions], ignore_index=True)
    areas = ca1_both[ca1_both["level"] == "area"]
    left = pd.DataFrame(
        {"name": areas["heading"].to_numpy(), "district_code": areas["district_code"].to_numpy()}, index=areas.index
    )
    overrides = {
        i: oc.CA1_CODE_OVERRIDES[(oc.clean_label(n), d)]
        for i, n, d in zip(left.index, left["name"], left["district_code"])
        if (oc.clean_label(n), d) in oc.CA1_CODE_OVERRIDES
    }
    right = codes.rename(columns={"zone_id": "id", "zone_name": "name"})
    matches = oc.match_names(left, right, overrides=overrides)
    name_of = codes.set_index("zone_id")["zone_name"]
    report = left.join(matches)
    report["zone_name"] = report["match_id"].map(name_of)
    report["parsed_ca1"] = report["name"].map(lambda n: str(oc.parse_area_name(n).key))
    report["parsed_code"] = report["zone_name"].map(
        lambda n: str(oc.parse_area_name(n).key) if isinstance(n, str) else ""
    )
    # Cross-check: the other parts listed in brackets name the same districts.
    other_ca1 = report["name"].map(lambda n: sorted({d for _, d in oc.parse_area_name(n).others}))
    other_code = report["zone_name"].map(
        lambda n: sorted({d for _, d in oc.parse_area_name(n).others}) if isinstance(n, str) else []
    )
    report["other_parts_agree"] = [(not a or not b) or a == b for a, b in zip(other_ca1, other_code)]
    for r in report[report["match_id"].isna()].itertuples():
        issue(f"ca1_{r.method}", r.name, f"district {r.district_code}; candidates {r.candidates}")
    for r in report[~report["other_parts_agree"]].itertuples():
        issue("ca1_other_parts", r.name, f"matched {r.match_id} {r.zone_name!r}: bracketed districts differ")
    matched = set(report["match_id"].dropna())
    for code in codes["zone_id"]:
        if code not in matched and code not in oc.CODES_WITHOUT_CA1_ROW:
            issue("code_without_ca1_row", code, name_of[code])
    for code in oc.CODES_WITHOUT_CA1_ROW:
        if code in matched:
            issue("stale_override", code, "listed in CODES_WITHOUT_CA1_ROW but matched to a CA1 row")
        if code not in set(codes["zone_id"]):
            issue("stale_override", code, "listed in CODES_WITHOUT_CA1_ROW but not in Appendix II")
    report.rename(columns={"name": "area_name", "match_id": "zone_id"}).to_csv(out_matches, index_label="block")

    # --- CA1 table --------------------------------------------------------------------
    by_sex = ca1.pivot_table(index="heading_row", columns="sex", values="employed", aggfunc="sum")
    out = pd.DataFrame(
        {
            # Official Appendix II-IV name on area rows (so that match_codes in
            # transport_census_zones.py matches it exactly); printed heading on
            # aggregate rows. The printed name is kept in printed_name.
            "area_name": report["zone_name"].reindex(ca1_both.index).fillna(ca1_both["heading"].map(oc.clean_label)),
            "printed_name": ca1_both["heading"].map(oc.clean_label),
            "district": ca1_both["district_code"].map(district_name),
            "district_code": ca1_both["district_code"].fillna(""),
            "row_type": ca1_both["level"].map(lambda v: "area" if v == "area" else "aggregate"),
            "level": ca1_both["level"],
            "segment": ca1_both["segment"],
            "employed": ca1_both["employed"].astype(int),
            "employed_male": ca1_both["heading_row"].map(by_sex["Male"]).astype(int),
            "employed_female": ca1_both["heading_row"].map(by_sex["Female"]).astype(int),
            "population_16_plus": ca1_both["population_16_plus"].astype(int),
            "population": d2_both["all_ages"].astype(int) if len(d2_both) == len(ca1_both) else pd.NA,
            "zone_id": report["match_id"].reindex(ca1_both.index),
            "zone_name": report["zone_name"].reindex(ca1_both.index),
            "match_method": report["method"].reindex(ca1_both.index),
            "source": ca1_both["heading_row"].map(lambda r: f"{vol7_name} sheet CA1, row {r + 1}"),
            "population_source": d2_both["heading_row"].map(lambda r: f"{vol2_name} sheet D2, row {r + 1}"),
        }
    )
    ratio = out["population_16_plus"] / out["population"]
    for r in out[(out["row_type"] == "area") & ((ratio < 0.6) | (ratio > 0.95))].itertuples():
        issue("implausible_16_plus_share", r.printed_name, f"16+ {r.population_16_plus} of population {r.population}")
    out.to_csv(out_ca1, index=False)

    # --- Codes table ------------------------------------------------------------------
    codes_out = codes[["zone_id", "zone_name", "district_code"]].copy()
    codes_out["district"] = codes_out["district_code"].map(district_name)
    codes_out["island_code"] = codes_out["zone_id"].str[0]
    codes_out["in_ca1"] = codes_out["zone_id"].isin(matched)
    codes_out["note"] = codes_out["zone_id"].map(
        lambda c: oc.RODRIGUES_CODE_NOTE if c.startswith("21") else oc.CODES_WITHOUT_CA1_ROW.get(c, "")
    )
    codes_out["source"] = [
        f"{vol7_name} sheet APPENDIX IV (region headings)"
        if c.startswith("21")
        else f"{vol7_name} sheet APPENDIX II, row {int(r) + 1}"
        for c, r in zip(codes["zone_id"], codes["sheet_row"].fillna(-1))
    ]
    codes_out.to_csv(out_codes, index=False)

    # --- CA27 table -------------------------------------------------------------------
    ca27_out = pd.DataFrame(
        {
            "residence_district": [
                {"republic": "Republic of Mauritius", "island": "Island of Mauritius"}.get(lvl) or district_name(d)
                for d, lvl in zip(ca27["district_code"], ca27["level"])
            ],
            "residence_district_code": ca27["district_code"].fillna(""),
            "row_type": ca27["level"].map(lambda v: "district" if v == "district" else "aggregate"),
            "sex": ca27["sex"],
            "age_group": ca27["age_group"],
            "Total": ca27["Total"].astype(int),
            **{c: ca27[c].astype(int) for c in oc.CA27_WORKPLACES},
            "source": ca27["sheet_row"].map(lambda r: f"{vol7_name} sheet CA27, row {r + 1}"),
        }
    )
    ca27_out.to_csv(out_ca27, index=False)

    # --- Children ---------------------------------------------------------------------
    share_10_11 = {}
    for (level, district), rows in d1.groupby(["level", d1["district_code"].fillna("")]):
        ages = rows.set_index("age")["population"]
        share_10_11[(level, district)] = ages.loc[[10, 11]].sum() / ages.loc[10:14].sum()
    children = []
    for i, r in d2_both.iterrows():
        printed = oc.clean_label(r["heading"])
        is_area = r["level"] == "area"
        zone_id = report["match_id"].get(i) if is_area else None
        name = report["zone_name"].get(i) if is_area else printed
        if r["level"] in ("republic", "island"):
            key = (r["level"], "")
        else:
            key = ("district", r["district_code"])
        base = {
            "area_name": name,
            "printed_name": printed,
            "district": district_name(r["district_code"]),
            "row_type": "area" if is_area else "aggregate",
            "zone_id": zone_id,
            "source": f"{vol2_name} sheet D2, row {int(r['heading_row']) + 1}",
        }
        for band in CHILD_BANDS:
            children.append({**base, "age_band": band, "children": int(r[band]), "method": "printed (D2)"})
        first, second = oc.split_band(int(r["10-14"]), share_10_11[key])
        method = (
            f"derived: D2 10-14 split by single-year shares of {key[1] or key[0]} "
            f"(D1; 10-11 share {share_10_11[key]:.4f})"
        )
        children.append({**base, "age_band": "10-11", "children": first, "method": method})
        children.append({**base, "age_band": "12-14", "children": second, "method": method})
    pd.DataFrame(children).to_csv(out_children, index=False)

    # --- Validation summary -------------------------------------------------------------
    area_rows = out[out["row_type"] == "area"]
    mauritius = area_rows[area_rows["district_code"].isin(oc.DISTRICTS)]
    od = ca27_out[(ca27_out["row_type"] == "district") & (ca27_out["sex"] == "Both sexes")]
    od = od[(od["age_group"] == oc.ALL_AGES) & od["residence_district_code"].isin(oc.DISTRICTS)]
    summary = {
        "areas": {
            "ca1_area_rows": len(area_rows),
            "mauritius": len(mauritius),
            "rodrigues": int((area_rows["district_code"] == oc.RODRIGUES_DISTRICT_CODE).sum()),
            "appendix_ii_codes": len(appendix),
            "codes_without_ca1_row": sorted(set(codes["zone_id"]) - matched),
            "match_methods": report["method"].value_counts().to_dict(),
            "fuzzy_matches": report[report["method"] == "fuzzy"][["name", "match_id", "zone_name", "score"]].to_dict(
                "records"
            ),
        },
        "totals": {
            "employed_republic": int(
                out.loc[out["level"].eq("republic") & out["segment"].eq("all"), "employed"].iloc[0]
            ),
            "employed_island_of_mauritius": int(
                out.loc[out["level"].eq("island") & out["segment"].eq("all"), "employed"].iloc[0]
            ),
            "employed_sum_of_areas": int(area_rows["employed"].sum()),
            "employed_sum_of_mauritius_areas": int(mauritius["employed"].sum()),
            "population_republic": int(
                out.loc[out["level"].eq("republic") & out["segment"].eq("all"), "population"].iloc[0]
            ),
            "population_sum_of_areas": int(area_rows["population"].sum()),
            "population_sum_of_mauritius_areas": int(mauritius["population"].sum()),
            "ca27_9x9_total": int(od[list(oc.DISTRICTS.values())].to_numpy().sum()),
            "ca27_mauritius_residents_by_other_workplace": {c: int(od[c].sum()) for c in oc.CA27_WORKPLACES[9:]},
        },
        "checks": {table: oc.summarise_checks(items) for table, items in checks.items()},
        "issues": len(issues),
    }
    with open(out_validation, "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)

    pd.DataFrame(issues, columns=["kind", "item", "detail"]).to_csv(review, index=False)
    if issues:
        raise click.ClickException(f"{len(issues)} issues to review in {review}")


if __name__ == "__main__":
    main()
