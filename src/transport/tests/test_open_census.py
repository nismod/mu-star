"""Tests for transport.open_census: name normaliser / matcher and the block parsers.

Small in-memory fixtures shaped like the Statistics Mauritius workbooks.
"""

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from transport import open_census as oc

# --- Normaliser ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, code",
    [
        ("R/R", "13"),
        ("Pamp.", "12"),
        ("B/R", "19"),
        ("P/W", "17"),
        ("G/P", "15"),
        ("P/L", "11"),
        ("R. DU REMPART", "13"),
        ("Rivière Du Rempart District", "13"),
        ("Pample-\nmousses", "12"),
        ("Black   River", "19"),
        ("District of Plaines Wilhems", "17"),
        ("Rodrigues", "21"),
        ("Nowhere", None),
    ],
)
def test_district_code(name, code):
    assert oc.district_code(name) == code


def test_simplify_folds_accents_quotes_and_abbreviations():
    assert oc.simplify("Bel Air Riv. Sèche") == oc.simplify("Bel Air Rivière  Sèche")
    assert oc.simplify("Poudre d\u2019Or") == oc.simplify("Poudre d'Or")
    assert oc.simplify("Morcellement St. André") == oc.simplify("Morcellement Saint-André")


@pytest.mark.parametrize(
    "names, key",
    [
        (
            [
                "Town of Port Louis-Ward 2-North (South in Moka & West in B/R)",  # CA1
                # Appendix II
                "Town of Port-Louis - Ward 2 - North (South in Moka District & West in Black River District)",
                "Town of Port-Louis, Ward 2, North",  # OSM
            ],
            ("port louis", 2, "north"),
        ),
        (
            [
                "Town of B-Bassin/R-Hill-Ward 4-West (East in P/W)",
                "Town of Beau-Bassin/Rose-Hill - Ward 4 - West (East in Plaines Wilhems District)",
                "Town of Beau-Bassin / Rose Hill, Ward 4, West",
            ],
            ("beau bassin rose hill", 4, "west"),
        ),
        (
            ["Town of Vacoas /Phoenix-Ward 6-East (West in P/W)", "Town of Vacoas-Phoenix, Ward 6, East"],
            ("vacoas phoenix", 6, "east"),
        ),
        (
            ["Amaury VCA-North (South in Flacq)", "Amaury North VCA (South in Flacq District)", "Amaury VCA, North"],
            ("amaury", None, "north"),
        ),
        (
            [
                "Richelieu VCA East in District of Plaines Wilhems (West in Black River District)",
                "Richelieu VCA East",
            ],
            ("richelieu", None, "east"),
        ),
        (["Grand River South East VCA", "Grand River South East  VCA"], ("grand river south east", None, None)),
        (["Dubreuil VCA - Part (East in Flacq District)", "Dubreuil VCA, Part"], ("dubreuil", None, "part")),
        (["Region 3 - St. Gabriel ", "Saint Gabriel"], ("saint gabriel", None, None)),
        (
            ["Grand Baie VCA-West (East in R/R)", "Grand Baie VCA - West (East in Rivière du Rempart District)"],
            ("grand baie", None, "west"),
        ),
    ],
)
def test_parse_area_name_variants_share_a_key(names, key):
    assert {oc.parse_area_name(n).key for n in names} == {key}


def test_parse_area_name_kinds_and_other_parts():
    ward = oc.parse_area_name("Town of Port Louis-Ward 2-North (South in Moka & West in B/R)")
    assert ward.kind == "ward"
    assert set(ward.others) == {("south", "18"), ("west", "19")}
    assert oc.parse_area_name("Town of Curepipe").kind == "town"
    region = oc.parse_area_name("Region 6 - Grande Montagne")
    assert (region.kind, region.ward, region.base) == ("region", 6, "grande montagne")
    lalmatie = oc.parse_area_name("Lalmatie VCA - East (West  Moka District)")
    assert lalmatie.others == (("west", "18"),)


# --- Matcher ---------------------------------------------------------------------------

CODES = pd.DataFrame(
    [
        ("1207", "Grand Baie VCA - West (East in Rivière du Rempart District)", "12"),
        ("1305", "Grand Baie VCA -  East ( West in Pamplemousses District)", "13"),
        ("1812", "Nouvelle Decouvert  VCA", "18"),
        ("1815", "Dubreuil VCA - Part (East in Flacq District and West in Plaines Wilhems District )", "18"),
        ("1744", "Town of Curepipe - Ward 4 (East in Moka District)", "17"),
        ("1201", "Arsenal  VCA", "12"),
        ("2101", "Region 1 - La Ferme", "21"),
    ],
    columns=["id", "name", "district_code"],
)


def test_match_names_steps():
    left = pd.DataFrame(
        [
            ("Grand Baie VCA-West (East in R/R)", "12"),  # exact
            ("Grand Baie VCA-East (West in Pamp.)  ", "13"),  # exact, other district
            ("Nouvelle Decouverte VCA", "18"),  # fuzzy
            ("Dubreuil VCA (East in Flacq & West in P/W)", "18"),  # relaxed part (no part word)
            ("Town of Curepipe, Ward 4, West", "17"),  # relaxed part (none in the code list)
            ("La Ferme", "21"),  # Rodrigues region from OSM
            ("Somewhere Else VCA", "12"),  # unmatched
        ],
        columns=["name", "district_code"],
    )
    result = oc.match_names(left, CODES)
    assert result["match_id"].fillna("").tolist() == ["1207", "1305", "1812", "1815", "1744", "2101", ""]
    assert result["method"].tolist() == [
        "exact",
        "exact",
        "fuzzy",
        "relaxed_part",
        "relaxed_part",
        "exact",
        "unmatched",
    ]


def test_match_names_uses_the_district():
    left = pd.DataFrame([("Grand Baie VCA, West", "13")], columns=["name", "district_code"])
    # The West part is coded in district 12; within district 13 only East exists, so no match.
    assert oc.match_names(left, CODES)["method"].iloc[0] == "unmatched"


def test_match_names_ambiguous_duplicate_and_override():
    codes = pd.concat(
        [CODES, pd.DataFrame([("1299", "Arsenal VCA", "12")], columns=["id", "name", "district_code"])],
        ignore_index=True,
    )
    left = pd.DataFrame([("Arsenal VCA", "12")], columns=["name", "district_code"])
    result = oc.match_names(left, codes)
    assert result["method"].iloc[0] == "ambiguous"
    assert set(result["candidates"].iloc[0].split("|")) == {"1201", "1299"}

    twice = pd.DataFrame([("Arsenal VCA", "12"), ("ARSENAL  VCA", "12")], columns=["name", "district_code"])
    result = oc.match_names(twice, CODES)
    assert result["method"].tolist() == ["duplicate", "duplicate"]
    assert result["match_id"].isna().all()

    result = oc.match_names(left, codes, overrides={0: "1201"})
    assert (result["match_id"].iloc[0], result["method"].iloc[0]) == ("1201", "override")


def test_match_names_fuzzy_needs_a_margin():
    codes = pd.DataFrame(
        [("1", "Petit Bel Air VCA", "15"), ("2", "Petit Bel Aïr  VCA", "15")], columns=["id", "name", "district_code"]
    )
    left = pd.DataFrame([("Petit Bell Air VCA", "15")], columns=["name", "district_code"])
    assert oc.match_names(left, codes)["method"].iloc[0] == "ambiguous"


# --- Block parsers -----------------------------------------------------------------------


def sheet(rows, width):
    """A sheet as read with header=None: object cells, NaN where empty."""
    return pd.DataFrame([list(r) + [np.nan] * (width - len(r)) for r in rows], dtype=object)


def sex_rows(both, male):
    """Both sexes / Male / Female rows, values (total 16+, employed)."""
    female = tuple(b - m for b, m in zip(both, male))
    return [("Both sexes", *both), ("Male", *male), ("Female", *female)]


def ca1_sheet(break_total=False):
    w1, w2, w3, r1 = (100, 50), (200, 120), (30, 10), (40, 20)
    m1, m2, m3, mr = (48, 30), (90, 70), (14, 6), (20, 12)

    def add(a, b):
        return tuple(x + y for x, y in zip(a, b))

    pl = add(w1, w2)
    pl_m = add(m1, m2)
    island, island_m = add(pl, w3), add(pl_m, m3)
    rows = [
        ("Table CA1 - Resident population 16 years of age and over ...",),
        ("Country, Island, District ...", "Total", "Current activity status"),
        (np.nan, np.nan, "Currently active"),
        (np.nan, np.nan, "Total", "Employed"),
        ("REPUBLIC OF MAURITIUS",),
        *sex_rows(add(island, r1), add(island_m, mr)),
        ("ISLAND OF MAURITIUS",),
        *sex_rows(island, island_m),
        ("PORT LOUIS DISTRICT-Wholly Urban",),
        *sex_rows((pl[0] + (1 if break_total else 0), pl[1]), pl_m),
        ("Town of Port Louis-Ward 3",),
        *sex_rows(w1, m1),
        ("Town of Port Louis-Ward 4",),
        *sex_rows(w2, m2),
        ("PAMPLEMOUSSES DISTRICT-Wholly Rural",),
        *sex_rows(w3, m3),
        ("Arsenal VCA",),
        *sex_rows(w3, m3),
        ("ISLAND OF RODRIGUES-Wholly Rural",),
        *sex_rows(r1, mr),
        ("Region 1 - La Ferme",),
        *sex_rows(r1, mr),
        ("Source: 2022 Housing and Population Census",),
    ]
    # Columns: label, Total, Currently active Total, Employed.
    out = []
    for r in rows:
        if len(r) == 3 and r[0] in oc.SEXES:
            out.append((r[0], r[1], r[2] + 5, r[2]))
        else:
            out.append(r)
    return sheet(out, 5)


def test_parse_ca1_blocks_levels_and_totals():
    blocks = oc.parse_ca1(ca1_sheet())
    both = blocks[blocks["sex"] == "Both sexes"]
    assert both["level"].tolist() == [
        "republic",
        "island",
        "district",
        "area",
        "area",
        "district",
        "area",
        "district",
        "area",
    ]
    assert both["district_code"].tolist()[2:] == ["11", "11", "11", "12", "12", "21", "21"]
    assert both.loc[both["heading"] == "Arsenal VCA", "employed"].item() == 10
    assert both.loc[both["heading"] == "Region 1 - La Ferme", "segment"].item() == "rural"
    checks = oc.check_area_totals(blocks, ["population_16_plus", "employed"])
    assert checks and not oc.failed(checks)


def test_check_area_totals_finds_a_wrong_total():
    blocks = oc.parse_ca1(ca1_sheet(break_total=True))
    bad = oc.failed(oc.check_area_totals(blocks, ["population_16_plus", "employed"]))
    names = " ".join(c.name for c in bad)
    assert "district 11" in names


def test_parse_blocks_rejects_values_without_a_heading():
    bad = sheet([("Both sexes", 1, 2)], 3)
    with pytest.raises(ValueError):
        oc.parse_blocks(bad, {"a": 1, "b": 2}, 0)


def test_classify_headings_segments_and_towns():
    headings = [
        "PLAINES WILHEMS DISTRICT",
        "PLAINES WILHEMS DISTRICT - Urban",
        "Town of Curepipe",
        "Town of Curepipe-Ward 1",
        "PLAINES WILHEMS DISTRICT - Rural",
        "Midlands VCA",
    ]
    levels = [(h.level, h.district_code, h.segment) for h in oc.classify_headings(headings)]
    assert levels == [
        ("district", "17", "all"),
        ("district_segment", "17", "urban"),
        ("town", "17", "urban"),
        ("area", "17", "urban"),
        ("district_segment", "17", "rural"),
        ("area", "17", "rural"),
    ]


def ca27_sheet():
    workplaces = ["Port Louis", "Pample-\nmousses", "Riviere Du Rempart", "Flacq", "Grand   Port"]
    workplaces += ["Savanne", "Plaines Wilhems", "Moka", "Black   River", "Not stated"]
    header = [
        ("Table CA27 ...",),
        (
            "District of usual residence, Sex and Age",
            "Total",
            "Place of work",
            *[np.nan] * 9,
            "Rodrigues and Outer Islands",
            "Outside Mauritius",
        ),
        (np.nan, np.nan, *workplaces),
    ]

    def block(name, ages):  # ages: {age: [12 values]} for Male and Female
        rows = [(name,)]
        both_ages = {a: [m + f for m, f in zip(v["Male"], v["Female"])] for a, v in ages.items()}
        for sex in oc.SEXES:
            per_age = both_ages if sex == "Both sexes" else {a: v[sex] for a, v in ages.items()}
            total = [sum(col) for col in zip(*per_age.values())]
            rows.append((sex, sum(total), *total))
            rows += [(a, sum(v), *v) for a, v in per_age.items()]
        return rows

    def vec(i, j):
        v = [0] * 12
        v[i] = j
        return v

    districts = [
        f"{d.upper()} DISTRICT" for d in ("Port Louis", "Pamplemousses", "Riviere du Rempart", "Flacq", "Grand Port")
    ]
    districts += [f"{d.upper()} DISTRICT" for d in ("Savanne", "Plaines Wilhems", "Moka", "Black River")]
    per_district = [
        {"< 20": {"Male": vec(i, 1), "Female": vec(i, 2)}, "20 - 29": {"Male": vec(0, 3), "Female": vec(9, 1)}}
        for i in range(9)
    ]
    rodrigues = {
        "< 20": {"Male": vec(10, 5), "Female": vec(10, 4)},
        "20 - 29": {"Male": vec(11, 1), "Female": vec(0, 0)},
    }

    def total(blocks):
        return {
            a: {s: [sum(x) for x in zip(*(b[a][s] for b in blocks))] for s in ("Male", "Female")} for a in blocks[0]
        }

    island = total(per_district)
    rows = header + block("REPUBLIC OF MAURITIUS", total([island, rodrigues])) + block("ISLAND OF MAURITIUS", island)
    for name, ages in zip(districts, per_district):
        rows += block(name, ages)
    rows += block("ISLAND OF RODRIGUES", rodrigues)
    return sheet(rows, 14)


def test_parse_and_check_ca27():
    blocks = oc.parse_ca27(ca27_sheet())
    assert list(blocks.columns[blocks.columns.get_loc("Total") :][:13]) == ["Total", *oc.CA27_WORKPLACES]
    pl = blocks[
        (blocks["district_code"] == "11") & (blocks["sex"] == "Both sexes") & (blocks["age_group"] == oc.ALL_AGES)
    ]
    assert pl[["Port Louis", "Not stated", "Total"]].iloc[0].tolist() == [3 + 3, 1, 3 + 3 + 1]
    assert set(blocks["age_group"]) == {oc.ALL_AGES, "< 20", "20 - 29"}
    checks = oc.check_ca27(blocks)
    assert checks and not oc.failed(checks)
    blocks.loc[0, "Moka"] += 1
    assert oc.failed(oc.check_ca27(blocks))


def test_parse_appendix_ii():
    rows = [
        ("Appendix II",),
        ("Code", np.nan, "Region"),
        ("District of Port Louis (P/L)",),
        (1112, np.nan, "Town of Port-Louis - Ward 2 - North (South in Moka District)"),
        ("District of Riviere du Rempart (R/R)",),
        ("1301", np.nan, "Belle Vue Maurel VCA - South (North in Pamplemousses District)"),
    ]
    out = oc.parse_appendix_ii(sheet(rows, 3))
    assert out[["zone_id", "district_code"]].values.tolist() == [["1112", "11"], ["1301", "13"]]
    wrong = [*rows[:3], ("1301", np.nan, "Misplaced VCA")]
    with pytest.raises(ValueError):
        oc.parse_appendix_ii(sheet(wrong, 3))


def test_parse_appendix_iv_regions():
    names = [f"Region {i} - Name {i}" for i in range(1, 7)]
    rows = [("Appendix IV",), tuple(names), ("Anse A", "Anse B", "C", "D", "E", "F"), ("Anse G",)]
    out = oc.parse_appendix_iv_regions(sheet(rows, 6))
    assert out["zone_id"].tolist() == [f"21{i:02d}" for i in range(1, 7)]
    assert out["localities"].iloc[0] == "Anse A; Anse G"
    assert set(out["district_code"]) == {"21"}


def test_parse_d2_bands():
    rows = [
        ("Table D2 ...",),
        ("Country, District ...", "All ages", "<1", "1 - 4", "5 - 9"),
        ("REPUBLIC OF MAURITIUS",),
        ("Both sexes", 9, 1, 3, 5),
        ("Male", 4, 1, 1, 2),
        ("Female", 5, 0, 2, 3),
    ]
    blocks, bands = oc.parse_d2(sheet(rows, 5))
    assert bands == ["<1", "1-4", "5-9"]
    assert blocks.loc[blocks["sex"] == "Both sexes", "5-9"].item() == 5


def test_parse_d1_single_years():
    rows = [
        ("Table D1 ...",),
        ("Country ...", "Resident population"),
        (np.nan, "Both sexes", "Male", "Female"),
        ("REPUBLIC OF MAURITIUS",),
        ("All ages", 100, 50, 50),
        ("10 - 14", 15, 7, 8),
        ("Under 1", 2, 1, 1),
        ("10", 3, 1, 2),
        ("11", 4, 2, 2),
        ("MOKA DISTRICT",),
        ("12", 5, 2, 3),
    ]
    out = oc.parse_d1_single_years(sheet(rows, 4))
    rows = out.fillna({"district_code": ""})[["age", "population", "district_code"]].values.tolist()
    assert rows == [[0, 2, ""], [10, 3, ""], [11, 4, ""], [12, 5, "18"]]


def test_find_cell_errors_on_a_changed_layout():
    with pytest.raises(ValueError):
        oc.find_cell(sheet([("a", "b")], 2), "Employed")


# --- Small helpers -------------------------------------------------------------------------


@pytest.mark.parametrize("total, share", [(223, 0.375), (0, 0.4), (1, 0.5), (7, 1.0)])
def test_split_band_conserves_the_total(total, share):
    a, b = oc.split_band(total, share)
    assert a + b == total and a >= 0 and b >= 0


def test_split_by_line_area_share():
    square = box(0, 0, 10, 10)
    near, far = oc.split_by_line(square, Point(100, 5), 0.3)
    assert near.area == pytest.approx(30, rel=1e-3)
    assert far.area == pytest.approx(70, rel=1e-3)
    assert near.centroid.x > far.centroid.x  # the near part is on the side of the point


def test_overrides_are_consistent():
    assert not set(oc.CODES_WITHOUT_CA1_ROW) & set(oc.KNOWN_MISSING_GEOMETRY)
    assert all(code[:2] in oc.DISTRICTS for code in oc.CODES_WITHOUT_CA1_ROW)
    assert not set(oc.OSM_PARENTS) & set(oc.OSM_CODE_TAG_ERRATA)
