"""Tests for transport.open_stats: tiny in-memory fixtures, no network."""

import numpy as np
import pandas as pd
import pytest

from transport import open_stats as os_


def grid(rows):
    width = max(len(r) for r in rows)
    return pd.DataFrame([list(r) + [np.nan] * (width - len(r)) for r in rows])


def test_group_mapping_covers_sections_once():
    sections = [s for _, _, secs in os_.GVA_GROUPS for s in secs]
    assert len(sections) == len(set(sections))
    assert set(sections) | set(os_.SECTIONS_WITHOUT_GVA) == set(os_.ISIC_SECTIONS)


@pytest.mark.parametrize(
    "label, key",
    [
        ("Agriculture, forestry and fishing", "agriculture"),
        ("Water supply; sewerage, waste management and remediation activities", "water"),
        ("Wholesale & retail trade; repair of motor vehicles and motorcycles", "trade"),
        ("Wholesale and retail trade; repair of motor vehicles and motorcycles", "trade"),
        ("Transport and storage", "transport"),
        ("Transportation and storage", "transport"),
        ("Administrative and support service activities                 ...", "admin_support"),
        ("Education", "education"),
        ("Other service activities", "other_services"),
        ("Other", None),
        ("of which: Wholesale and retail trade", None),
        ("Financial leasing and other credit granting", None),
        ("Monetary intermediation", None),
    ],
)
def test_classify_group(label, key):
    assert os_.classify_group(label) == key


def test_service_group_covers_section_t():
    assert os_.GROUP_SECTIONS["other_services"] == ("S", "T")
    assert os_.SECTION_GROUP["T"] == "other_services"


def gva_grid():
    rows = [["Back to table", None, None], ["Table 3 - GVA, 2021 - 2022", None, None], [None, None, None]]
    rows.append([None, "2021 1", "2022 1"])
    values = {key: i + 1 for i, (key, _, _) in enumerate(os_.GVA_GROUPS)}
    labels = {
        "agriculture": "Agriculture, forestry and fishing",
        "mining": "Mining and quarrying",
        "manufacturing": "Manufacturing",
        "electricity": "Electricity, gas, steam and air conditioning supply",
        "water": "Water supply, sewerage, waste management and remediation activities",
        "construction": "Construction",
        "trade": "Wholesale & retail trade; repair of motor vehicles and motorcycles",
        "transport": "Transportation and storage",
        "accommodation": "Accommodation and food service activities",
        "ict": "Information and communication",
        "finance": "Financial and insurance activities",
        "real_estate": "Real estate activities",
        "professional": "Professional, scientific and technical activities",
        "admin_support": "Administrative and support service activities   ...",
        "public_admin": "Public administration and defence; compulsory social security   ...",
        "education": "Education",
        "health": "Human health and social work activities",
        "arts": "Arts, entertainment and recreation",
        "other_services": "Other service activities",
    }
    for key, label in labels.items():
        rows.append([label, 0, values[key] * 10.0])
        if key == "trade":
            rows.append([": GBC", 0, 3.0])
        if key == "real_estate":
            rows.append(["of which: Owner-occupied housing services", 0, 5.0])
    rows.append(["Gross Value Added (GVA) at basic prices", 0, sum(values.values()) * 10.0])
    rows.append(["Gross Value Added (at basic prices) excluding GBCs", 0, 1.0])
    return grid(rows), values


def test_parse_gva_table():
    g, values = gva_grid()
    out = os_.parse_gva_table(g, 2022)
    assert out["groups"]["trade"] == values["trade"] * 10
    assert out["gbc"] == {"trade": 3.0}
    assert out["imputed"] == {"real_estate": 5.0}
    assert out["total"] == sum(values.values()) * 10
    with pytest.raises(ValueError):
        os_.parse_gva_table(g, 2030)


def test_parse_gva_table_rejects_missing_group():
    g, _ = gva_grid()
    g = g[~g[0].astype(str).str.startswith("Education")]
    with pytest.raises(ValueError, match="missing"):
        os_.parse_gva_table(g, 2022)


def census_grid(letters=os_.ISIC_SECTIONS, counts=None, not_stated=7):
    counts = counts or {s: 10 * (i + 1) for i, s in enumerate(os_.ISIC_SECTIONS)}
    total = sum(counts.values()) + not_stated
    header = ["Country", None, "Industry - (Section) 1"] + [None] * (len(letters) - 1) + [None]
    letter_row = [None, None, *letters, "Not Stated"]
    return (
        grid(
            [
                ["Table CA3 - ...", None],
                header,
                letter_row,
                ["REPUBLIC OF MAURITIUS"],
                ["Both sexes ", total, *[counts[s] for s in letters], not_stated],
                ["Male  ", 1],
                ["ISLAND OF MAURITIUS"],
                ["Both sexes ", 1],
            ]
        ),
        counts,
        total,
    )


def test_parse_census_employment():
    g, counts, total = census_grid()
    out = os_.parse_census_employment(g)
    assert out["total"] == total
    assert out["sections"] == counts
    assert out["not_stated"] == 7
    with pytest.raises(ValueError):
        os_.parse_census_employment(g, area="NOWHERE")


def test_build_output_per_worker():
    g, _ = gva_grid()
    gva = os_.parse_gva_table(g, 2022)
    cg, counts, total = census_grid()
    census = os_.parse_census_employment(cg)
    table, rec = os_.build_output_per_worker(gva, census, 2022, "gva src", "emp src", expected_total=total)
    assert list(table["sector"]) == list("ABCDEFGHIJKLMNOPQRST")
    # trade (G): GVA 70 million over 70 workers; GBC (3) removed in the adjusted column
    g_row = table.set_index("sector").loc["G"]
    assert g_row["output_per_worker"] == round(70 * 1e6 / counts["G"])
    assert g_row["output_per_worker_adjusted"] == round(67 * 1e6 / counts["G"])
    # S carries S+T employment; T has the same ratio and no numbers of its own
    s_row, t_row = table.set_index("sector").loc["S"], table.set_index("sector").loc["T"]
    assert s_row["employment"] == counts["S"] + counts["T"]
    assert t_row["output_per_worker"] == s_row["output_per_worker"]
    assert t_row["employment"] == ""
    # employment in the table + U + not stated = census total
    emp = pd.to_numeric(table["employment"], errors="coerce").sum()
    assert emp + counts["U"] + 7 == total
    assert rec.iloc[-1]["item"] == "TOTAL"
    with pytest.raises(ValueError, match="expected"):
        os_.build_output_per_worker(gva, census, 2022, "a", "b", expected_total=total + 1)


def test_build_output_per_worker_rejects_gva_mismatch():
    g, _ = gva_grid()
    gva = os_.parse_gva_table(g, 2022)
    gva["total"] += 1000
    cg, _, _ = census_grid()
    with pytest.raises(ValueError, match="published total GVA"):
        os_.build_output_per_worker(gva, os_.parse_census_employment(cg), 2022, "a", "b")


def test_parse_labour_employment():
    g = grid(
        [
            ["Table 1.4", None],
            [None, None],
            ["Industry", 2023, None, None],
            [None, "Male", "Female", "Total"],
            ["Agriculture, forestry and fishing", 1.0, 1.0, 2.0],
            ["of which sugarcane", 0.1, 0.1, 0.2],
            ["Other service activities", 1.0, 2.5, 3.5],
            ["Total", 2.0, 3.5, 5.5],
        ]
    )
    out = os_.parse_labour_employment(g, 2023)
    assert out == {"agriculture": 2000, "other_services": 3500, "total": 5500}


def edu_grid(title, rows):
    return grid([["Back"], [title], ["District", "Total"], *rows, ["Table 999 - next"]])


def test_parse_district_total_and_build_enrolment():
    rows = [[d, 2] for d in os_.MAURITIUS_DISTRICTS]
    rows += [["Island of Mauritius", 18], ["Island of Rodrigues", 3], ["Republic  of  Mauritius", 21]]
    schools = {
        lvl: os_.parse_district_total(edu_grid("Table 120 - x", rows), "Table 120") for lvl in ("primary", "secondary")
    }
    enrol_rows = [[d, 100] for d in os_.MAURITIUS_DISTRICTS] + [
        ["Island of Mauritius", 900],
        ["Island of Rodrigues", 50],
    ]
    enrol = {lvl: os_.parse_district_total(edu_grid("Table 122", enrol_rows), "Table 122") for lvl in schools}
    out = os_.build_enrolment(schools, enrol, 2025, {"primary": "p", "secondary": "s"})
    assert len(out) == 20
    assert out["enrolment"].sum() == 2 * 950
    assert set(out["island"]) == {"mauritius", "rodrigues"}
    enrol["primary"]["Island of Mauritius"] = 901
    with pytest.raises(ValueError, match="published"):
        os_.build_enrolment(schools, enrol, 2025, {"primary": "p", "secondary": "s"})


def test_parse_district_total_missing_district():
    with pytest.raises(ValueError, match="missing"):
        os_.parse_district_total(edu_grid("Table 120", [["Port Louis", 1]]), "Table 120")


def passenger_grid():
    head = ["Year", "Arrivals in Rodrigues", None, None, "Departures from Rodrigues"]
    return grid(
        [
            ["Back"],
            ["Table 2a - Passenger traffic between Islands of Mauritius and Rodrigues, 2014-2015"],
            head,
            ["2014", 10, 5, 5, 11],
            ["2015 2", 20, 10, 10, 19],
            ["1 Includes residents and non-residents"],
            ["Table 2b - Passenger traffic between Islands of Rodrigues and Reunion, 2015"],
            head,
            [2015, 2, 1, 1, 3],
        ]
    )


def test_parse_rodrigues_passenger_blocks():
    out = os_.parse_rodrigues_passenger_blocks(passenger_grid())
    assert out["2a"]["arrivals_in_rodrigues"].tolist() == [10, 20]
    assert out["2a"]["year"].tolist() == [2014, 2015]
    assert out["2b"]["departures_from_rodrigues"].tolist() == [3]


def mode_grid():
    return grid(
        [
            ["Back"],
            ["Table 20 - Monthly passenger arrivals by mode of transport and sex, 2025"],
            [None],
            ["Month", "Air", None, None, "Sea", None, None, "Total"],
            [None, "Male", "Female", "Both Sexes", "Male", "Female", "Both Sexes", "Male", "Female", "Both Sexes"],
            ["January", 1, 1, 2, 0, 0, 0, 1, 1, 2],
            ["YEAR", 5, 5, 10, 1, 1, 2, 6, 6, 12],
        ]
    )


def test_parse_rodrigues_mode_year():
    assert os_.parse_rodrigues_mode_year(mode_grid()) == {"year": 2025, "air": 10, "sea": 2, "total": 12}


def exports_grid():
    return grid(
        [
            ["Table 30 - Export ... 2024 - 2025"],
            [None],
            ["Commodity", "Unit", 2024, 2025.0],
            ["Cattle", "Unit", 5, 6],
            ["Frozen octopus", "Tonnes", 100, 200],
            ["Honey", "Kg", 2500, "…"],
            ["1 footnote"],
        ]
    )


def test_exports_and_tonnes():
    ex = os_.parse_rodrigues_exports(exports_grid())
    assert len(ex) == 5  # the '...' cell is skipped
    assert os_.to_tonnes(2500, "Kg") == 2.5
    assert os_.to_tonnes(3, "Tonnes") == 3
    assert os_.to_tonnes(3, "Unit") is None
    traffic = os_.build_traffic(
        passenger_grid(),
        os_.parse_rodrigues_mode_year(mode_grid()),
        os_.parse_rodrigues_mode_year(mode_grid()),
        ex,
        {"tourism": "t", "rodrigues": "r"},
    )
    assert list(traffic.columns) == os_.TRAFFIC_COLUMNS
    freight = traffic[traffic["freight_t"] != ""]
    assert set(freight["commodity"]) == {"frozen octopus", "honey"}  # cattle counted in units: omitted
    assert freight.loc[freight["commodity"] == "honey", "freight_t"].iloc[0] == 2.5
    pax = traffic[(traffic["from_island"] == "mauritius") & (traffic["to_island"] == "rodrigues")]
    assert pax["passengers"].tolist() == [10, 20]
