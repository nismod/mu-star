"""Open Statistics Mauritius tables for the transport analysis.

Parsers for published Excel tables (read with ``header=None``, i.e. as raw cell
grids) and the logic that turns them into three small inputs:

1. Output per worker by ISIC section (data item 9): GVA by industry group at
   current basic prices (National Accounts) divided by employment by industry
   (2022 census, Table CA3).
2. School enrolment by district and level (Digest of Education Statistics).
3. Published inter-island passenger and freight traffic (Digest of
   International Travel and Tourism Statistics; Digest of Rodrigues Statistics).

The parsers are deliberately strict: they look for headers and labels, and
raise ``ValueError`` when a table does not look as expected, rather than
guessing. Nothing here touches the network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ISIC_SECTIONS = list("ABCDEFGHIJKLMNOPQRSTU")

# ---------------------------------------------------------------------------
# Industry groups -> ISIC Rev. 4 sections
# ---------------------------------------------------------------------------
# Statistics Mauritius uses NSIC Rev. 2, which follows ISIC Rev. 4 at section
# level. National Accounts publish 19 industry groups; each group is one
# section except "Other service activities", which also contains section T
# (activities of households as employers of domestic personnel): see
# Methodology of Annual National Accounts (Methodology_ANA_041126.pdf), s2.20.
# Section U (extraterritorial organisations) has no published GVA.
#
# Each entry: group key, regex on the normalised published label, sections
# covered (the first is the "primary" section that carries the group's GVA).
GVA_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("agriculture", r"^agriculture", ("A",)),
    ("mining", r"^mining", ("B",)),
    ("manufacturing", r"^manufacturing", ("C",)),
    ("electricity", r"^electricity", ("D",)),
    ("water", r"^water supply", ("E",)),
    ("construction", r"^construction", ("F",)),
    ("trade", r"^wholesale and retail trade; repair|^wholesale and retail trade and repair", ("G",)),
    ("transport", r"^transport", ("H",)),
    ("accommodation", r"^accomm?odation", ("I",)),
    ("ict", r"^information and communication", ("J",)),
    ("finance", r"^financial and insurance", ("K",)),
    ("real_estate", r"^real estate", ("L",)),
    ("professional", r"^professional", ("M",)),
    ("admin_support", r"^administrative and support", ("N",)),
    ("public_admin", r"^public administration", ("O",)),
    ("education", r"^education$", ("P",)),
    ("health", r"^human health", ("Q",)),
    ("arts", r"^arts", ("R",)),
    ("other_services", r"^other service", ("S", "T")),
]
GROUP_SECTIONS = {key: secs for key, _, secs in GVA_GROUPS}
SECTION_GROUP = {s: key for key, _, secs in GVA_GROUPS for s in secs}

# Sections for which no GVA is published, with the reason.
SECTIONS_WITHOUT_GVA = {
    "U": "extraterritorial organisations: no GVA group published; excluded (negligible employment)",
}

# Notes attached to sectors where output per worker needs care.
SECTOR_NOTES = {
    "G": "Published GVA includes Global Business Companies (GBC) trading and merchanting, which employ few "
    "people: use output_per_worker_adjusted (GBC removed) when locating jobs.",
    "K": "Published GVA includes GBC activity ('Other financial'), which employs few people: use "
    "output_per_worker_adjusted (GBC removed) when locating jobs.",
    "L": "Not meaningful for locating jobs: GVA is mostly imputed rent of owner-occupied dwellings (no workers). "
    "Use output_per_worker_adjusted (imputed rent removed) for the real-estate agency activity only, and "
    "do not allocate the imputed rent to job locations (it is a housing-stock quantity).",
    "M": "Published GVA includes GBC professional services, which employ few people: use "
    "output_per_worker_adjusted (GBC removed) when locating jobs.",
    "O": "Public administration GVA is mostly compensation of employees (non-market output); the ratio "
    "reflects wages, not productivity.",
    "S": "'Other service activities' also contains activities of households as employers of domestic "
    "personnel (section T). GVA and employment are therefore S+T combined; section T carries the same ratio.",
    "T": "No separate GVA published: included in 'Other service activities' (section S); same ratio as S.",
}


def normalise_label(label) -> str:
    """Lower-case, collapse whitespace, '&' -> 'and', drop footnote ellipses and punctuation noise."""
    text = str(label).replace("​", " ").replace("\xa0", " ").lower()
    text = text.replace("&", "and").replace("…", " ").replace("...", " ")
    text = re.sub(r"\s+", " ", text).strip(" .:")
    return text


def classify_group(label) -> str | None:
    """Return the GVA group key for a published industry-group label, or None."""
    text = normalise_label(label)
    # Remove trailing footnote markers such as "Other 5".
    for key, pattern, _ in GVA_GROUPS:
        if re.search(pattern, text):
            return key
    return None


def _first_year(value) -> int | None:
    """Year from a header cell such as 2022, 2022.0, '2022 1' or '2025 2'."""
    if isinstance(value, (int, float)) and not pd.isna(value) and 1900 < float(value) < 2200:
        return int(value)
    match = re.match(r"^\s*(\d{4})\b", str(value))
    return int(match.group(1)) if match else None


def read_sheet(path, sheet: str) -> pd.DataFrame:
    """Read one sheet as a raw grid; the sheet name is matched ignoring surrounding spaces."""
    xls = pd.ExcelFile(path)
    wanted = sheet.strip()
    matches = [s for s in xls.sheet_names if s.strip() == wanted]
    if not matches:
        raise ValueError(f"No sheet {sheet!r} in {path}; sheets: {xls.sheet_names[:12]}...")
    return xls.parse(matches[0], header=None)


def _tidy(x):
    """Whole floats as int, for readable CSV output."""
    return int(x) if isinstance(x, float) and x == int(x) else x


def _as_number(value) -> float | None:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    if isinstance(value, str):
        text = value.replace(",", "").replace("\xa0", " ").strip()
        if re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
            return float(text)
    return None


# ---------------------------------------------------------------------------
# 1. GVA by industry group (National Accounts)
# ---------------------------------------------------------------------------
def parse_gva_table(grid: pd.DataFrame, year: int) -> dict:
    """GVA at current basic prices by industry group for one year (MUR million).

    Works for the National Accounts historical series (Table 3) and the
    Digest of National Accounts (Table 4). Returns a dict with:
      groups:   {group_key: gva}
      gbc:      {group_key: GBC part of that group's GVA}  (where published)
      imputed:  {group_key: owner-occupied housing part}   (real estate)
      total:    published total GVA at basic prices
    """
    header_row = None
    year_col = None
    for r in range(min(len(grid), 15)):
        for c in range(grid.shape[1]):
            if _first_year(grid.iat[r, c]) == year and (c > 0):
                header_row, year_col = r, c
                break
        if header_row is not None:
            break
    if header_row is None:
        raise ValueError(f"Year {year} not found in the header of the GVA table")

    groups: dict[str, float] = {}
    gbc: dict[str, float] = {}
    imputed: dict[str, float] = {}
    total = None
    current = None
    for r in range(header_row + 1, len(grid)):
        label = grid.iat[r, 0]
        if not isinstance(label, str):
            continue
        text = normalise_label(label)
        value = _as_number(grid.iat[r, year_col])
        if re.match(r"^gross value added", text):
            total = value
            break
        if re.search(r"(^|\W)gbc$", text) and current is not None and value is not None:
            # "of which: GBC" / ": GBC" rows; accumulate (K has 'Other financial: of which GBC')
            gbc[current] = gbc.get(current, 0.0) + value
            continue
        if "owner" in text and "occupied" in text and current is not None and value is not None:
            imputed[current] = value
            continue
        key = classify_group(label)
        if key is not None and not text.startswith("of which") and value is not None:
            if key in groups:
                raise ValueError(f"Industry group {key!r} appears twice in the GVA table")
            groups[key] = value
            current = key
    if total is None:
        raise ValueError("No 'Gross Value Added' total row found in the GVA table")
    missing = [k for k, _, _ in GVA_GROUPS if k not in groups]
    if missing:
        raise ValueError(f"Industry groups missing from the GVA table: {missing}")
    return {"groups": groups, "gbc": gbc, "imputed": imputed, "total": total}


# ---------------------------------------------------------------------------
# 2. Employment by industry section (2022 census, Table CA3)
# ---------------------------------------------------------------------------
def parse_census_employment(grid: pd.DataFrame, area: str = "REPUBLIC OF MAURITIUS") -> dict:
    """Currently employed population by ISIC section, both sexes, for one area.

    Parses census Table CA3 (rows: area blocks with Both sexes / Male / Female;
    columns: Total, then sections A..U, then 'Not Stated'). Returns
    {"total": int, "sections": {letter: int}, "not_stated": int}.
    """
    header_row = None
    for r in range(len(grid)):
        cells = [str(v).strip() for v in grid.iloc[r, 2:].tolist()]
        if "A" in cells and "U" in cells:
            header_row = r
            break
    if header_row is None:
        raise ValueError("No section header row (A..U) found in the census table")
    col_name = {}
    for c in range(2, grid.shape[1]):
        name = str(grid.iat[header_row, c]).strip()
        if name in ISIC_SECTIONS:
            col_name[c] = name
        elif name.lower().replace(" ", "") == "notstated":
            col_name[c] = "not_stated"
    area_row = None
    for r in range(header_row + 1, len(grid)):
        if str(grid.iat[r, 0]).strip().upper() == area.upper():
            area_row = r
            break
    if area_row is None:
        raise ValueError(f"Area {area!r} not found in the census table")
    row = None
    for r in range(area_row + 1, min(area_row + 6, len(grid))):
        if str(grid.iat[r, 0]).strip().lower() == "both sexes":
            row = r
            break
    if row is None:
        raise ValueError(f"No 'Both sexes' row under {area!r}")
    sections = {}
    not_stated = 0
    for c, name in col_name.items():
        value = _as_number(grid.iat[row, c])
        if value is None:
            raise ValueError(f"Non-numeric census count in column {name!r}")
        if name == "not_stated":
            not_stated = int(value)
        else:
            sections[name] = int(value)
    missing = [s for s in ISIC_SECTIONS if s not in sections]
    if missing:
        raise ValueError(f"Census sections missing: {missing}")
    total = _as_number(grid.iat[row, 1])
    if total is None:
        raise ValueError("Non-numeric census total")
    return {"total": int(total), "sections": sections, "not_stated": not_stated}


def parse_labour_employment(grid: pd.DataFrame, year: int) -> dict:
    """Employment by industry group ('000, both sexes) from the Digest of Labour Statistics, Table 1.4.

    Source for this table is the Continuous Multi Purpose Household Survey
    plus the Survey of Employment and Earnings (foreign workers in large
    establishments are included). Returns {group_key: persons, "total": persons}.
    """
    header_row = None
    year_col = None
    for r in range(min(len(grid), 10)):
        for c in range(1, grid.shape[1]):
            if _first_year(grid.iat[r, c]) == year:
                header_row, year_col = r, c
                break
        if header_row is not None:
            break
    if header_row is None:
        raise ValueError(f"Year {year} not found in the labour table header")
    # Columns are Male, Female, Total per year: the total is two columns right.
    sub = [str(grid.iat[header_row + 1, c]).strip().lower() for c in range(year_col, year_col + 3)]
    if sub != ["male", "female", "total"]:
        raise ValueError(f"Unexpected sub-header under year {year}: {sub}")
    total_col = year_col + 2
    out: dict = {}
    for r in range(header_row + 2, len(grid)):
        label = grid.iat[r, 0]
        if not isinstance(label, str):
            continue
        text = normalise_label(label)
        value = _as_number(grid.iat[r, total_col])
        if text == "total":
            out["total"] = round(value * 1000)
            break
        if text.startswith("of which") or value is None:
            continue
        key = classify_group(label)
        if key is not None and key not in out:
            out[key] = round(value * 1000)
    return out


def build_output_per_worker(
    gva: dict,
    census: dict,
    year: int,
    source_gva: str,
    source_employment: str,
    expected_total: int | None = None,
    labour: dict | None = None,
    labour_year: int | None = None,
    gva_tolerance: float = 25.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Combine GVA and census employment into output per worker by ISIC section.

    Returns (table, reconciliation). The table has one row per ISIC section A..T
    that has a published GVA group (T shares the ratio of S; U has no GVA).
    The reconciliation lists every published group and the employment that
    could not be matched (section U, 'not stated'), with checks.
    """
    sections = census["sections"]
    total_emp = sum(sections.values()) + census["not_stated"]
    if total_emp != census["total"]:
        raise ValueError(f"Census sections plus 'not stated' sum to {total_emp}, not the published {census['total']}")
    if expected_total is not None and census["total"] != expected_total:
        raise ValueError(f"Census total {census['total']} differs from the expected {expected_total}")
    gva_sum = sum(gva["groups"].values())
    if abs(gva_sum - gva["total"]) > gva_tolerance:
        raise ValueError(f"Industry groups sum to {gva_sum}, published total GVA is {gva['total']}")

    rows = []
    for key, _, secs in GVA_GROUPS:
        primary = secs[0]
        emp = sum(sections[s] for s in secs)
        g = _tidy(gva["groups"][key])
        gbc = _tidy(gva["gbc"].get(key, 0.0))
        imp = _tidy(gva["imputed"].get(key, 0.0))
        opw = g * 1e6 / emp
        adjusted = (g - gbc - imp) * 1e6 / emp
        # one GVA group may cover several sections: the primary carries the numbers
        rows.append(
            {
                "sector": primary,
                "output_per_worker": round(opw),
                "gva_mur_million": g,
                "employment": emp,
                "year": year,
                "source_gva": source_gva,
                "source_employment": source_employment,
                "note": SECTOR_NOTES.get(primary, ""),
                "gva_gbc_mur_million": gbc if gbc else "",
                "gva_imputed_rent_mur_million": imp if imp else "",
                "output_per_worker_adjusted": round(adjusted),
            }
        )
        for extra in secs[1:]:
            rows.append(
                {
                    "sector": extra,
                    "output_per_worker": round(opw),
                    "gva_mur_million": "",
                    "employment": "",
                    "year": year,
                    "source_gva": source_gva,
                    "source_employment": source_employment,
                    "note": SECTOR_NOTES.get(extra, ""),
                    "gva_gbc_mur_million": "",
                    "gva_imputed_rent_mur_million": "",
                    "output_per_worker_adjusted": round(adjusted),
                }
            )
    table = pd.DataFrame(rows).sort_values("sector").reset_index(drop=True)

    rec = []
    for key, _, secs in GVA_GROUPS:
        emp = sum(sections[s] for s in secs)
        item = {
            "item": "+".join(secs),
            "published_group": key,
            "gva_mur_million": _tidy(gva["groups"][key]),
            "employment_census": emp,
            "employment_labour_survey": (labour or {}).get(key, ""),
            "labour_survey_year": labour_year if labour else "",
            "comment": "",
        }
        rec.append(item)
    rec.append(
        {
            "item": "U",
            "published_group": "(none)",
            "gva_mur_million": "",
            "employment_census": sections["U"],
            "employment_labour_survey": "",
            "labour_survey_year": "",
            "comment": SECTIONS_WITHOUT_GVA["U"],
        }
    )
    rec.append(
        {
            "item": "not_stated",
            "published_group": "(none)",
            "gva_mur_million": "",
            "employment_census": census["not_stated"],
            "employment_labour_survey": "",
            "labour_survey_year": "",
            "comment": "industry not stated in the census: not allocated",
        }
    )
    rec.append(
        {
            "item": "TOTAL",
            "published_group": "",
            "gva_mur_million": _tidy(gva["total"]),
            "employment_census": census["total"],
            "employment_labour_survey": (labour or {}).get("total", ""),
            "labour_survey_year": labour_year if labour else "",
            "comment": f"sum of groups {gva_sum} vs published GVA {gva['total']} (rounding); "
            f"census sections + U + not stated = {total_emp}",
        }
    )
    return table, pd.DataFrame(rec)


# ---------------------------------------------------------------------------
# 3. School enrolment by district (Digest of Education Statistics)
# ---------------------------------------------------------------------------
MAURITIUS_DISTRICTS = [
    "Port Louis",
    "Pamplemousses",
    "Riviere du Rempart",
    "Flacq",
    "Grand Port",
    "Savanne",
    "Plaines Wilhems",
    "Moka",
    "Black River",
]
EDUCATION_TABLES = {
    # level: (sheet, schools table title prefix, enrolment sheet, enrolment table title prefix)
    "primary": ("Table 120-121", "Table 120", "Table 122", "Table 122"),
    "secondary": ("Table 132-133", "Table 132", "Table 140-141", "Table 140"),
}


def parse_district_total(grid: pd.DataFrame, title_prefix: str) -> dict[str, float]:
    """First numeric value on each district row of the table whose title starts with ``title_prefix``.

    Returns {published row label: value} for the nine Mauritius districts and
    the 'Island of Mauritius', 'Island of Rodrigues' and 'Republic of Mauritius' rows.
    """
    start = None
    for r in range(len(grid)):
        cell = grid.iat[r, 0]
        if isinstance(cell, str) and re.match(rf"^\s*{re.escape(title_prefix)}\b", cell):
            start = r
            break
    if start is None:
        raise ValueError(f"Table {title_prefix!r} not found")
    wanted = {normalise_label(d): d for d in MAURITIUS_DISTRICTS}
    wanted.update(
        {
            "island of mauritius": "Island of Mauritius",
            "island of rodrigues": "Island of Rodrigues",
            "republic of mauritius": "Republic of Mauritius",
        }
    )
    out: dict[str, float] = {}
    for r in range(start + 1, len(grid)):
        cell = grid.iat[r, 0]
        if isinstance(cell, str) and re.match(r"^\s*Table \d+", cell):
            break
        if not isinstance(cell, str):
            continue
        key = normalise_label(cell)
        if key not in wanted:
            continue
        for c in range(1, grid.shape[1]):
            value = _as_number(grid.iat[r, c])
            if value is not None:
                out[wanted[key]] = value
                break
    missing = [n for n in [*MAURITIUS_DISTRICTS, "Island of Mauritius", "Island of Rodrigues"] if n not in out]
    if missing:
        raise ValueError(f"Rows missing from {title_prefix!r}: {missing}")
    return out


def build_enrolment(schools: dict[str, dict], enrolment: dict[str, dict], year: int, sources: dict[str, str]):
    """Tidy table from parsed schools/enrolment dicts per level, with consistency checks."""
    rows = []
    for level in ("primary", "secondary"):
        s, e = schools[level], enrolment[level]
        for island, names in (("mauritius", MAURITIUS_DISTRICTS), ("rodrigues", ["Island of Rodrigues"])):
            for name in names:
                rows.append(
                    {
                        "island": island,
                        "district": "Rodrigues" if island == "rodrigues" else name,
                        "level": level,
                        "schools": int(s[name]),
                        "enrolment": int(e[name]),
                        "year": year,
                        "source": sources[level],
                    }
                )
        mu_s = sum(s[n] for n in MAURITIUS_DISTRICTS)
        mu_e = sum(e[n] for n in MAURITIUS_DISTRICTS)
        if mu_s != s["Island of Mauritius"] or mu_e != e["Island of Mauritius"]:
            raise ValueError(
                f"{level}: districts sum to {mu_s}/{mu_e}, "
                f"published {s['Island of Mauritius']}/{e['Island of Mauritius']}"
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4. Inter-island traffic
# ---------------------------------------------------------------------------
def parse_rodrigues_passenger_blocks(grid: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Tables 2a and 2b of the Digest of International Travel and Tourism Statistics.

    2a: passenger traffic between the Islands of Mauritius and Rodrigues;
    2b: between Rodrigues and Reunion. Columns used: total arrivals in
    Rodrigues and total departures from Rodrigues. Returns
    {"2a": DataFrame(year, arrivals_in_rodrigues, departures_from_rodrigues), "2b": ...}.
    """
    blocks: dict[str, list] = {}
    current = None
    for r in range(len(grid)):
        cell = grid.iat[r, 0]
        if isinstance(cell, str):
            match = re.match(r"^\s*Table\s+(2[ab])\b", cell)
            if match:
                current = match.group(1)
                blocks[current] = []
                continue
        if current is None:
            continue
        if str(grid.iat[r, 1]).startswith("Arrivals in Rodrigues"):
            if not str(grid.iat[r, 4]).startswith("Departures from Rodrigues"):
                raise ValueError("Unexpected column layout in passenger traffic table")
            continue
        year = _first_year(cell)
        arr, dep = _as_number(grid.iat[r, 1]), _as_number(grid.iat[r, 4])
        if year is not None and arr is not None and dep is not None:
            blocks[current].append((year, int(arr), int(dep)))
    out = {}
    for key, values in blocks.items():
        if not values:
            raise ValueError(f"No data rows for Table {key}")
        out[key] = pd.DataFrame(values, columns=["year", "arrivals_in_rodrigues", "departures_from_rodrigues"])
    if set(out) != {"2a", "2b"}:
        raise ValueError(f"Expected Tables 2a and 2b, found {sorted(out)}")
    return out


def parse_rodrigues_mode_year(grid: pd.DataFrame) -> dict:
    """Monthly arrivals/departures by mode (Rodrigues Digest Tables 20 and 22): the YEAR row.

    Returns {"year": int, "air": int, "sea": int, "total": int} (both sexes).
    """
    year = None
    for r in range(min(len(grid), 4)):
        cell = grid.iat[r, 0]
        match = re.search(r"\b(20\d\d)\b", str(cell)) if isinstance(cell, str) else None
        if match and "Table" in str(cell):
            year = int(match.group(1))
    if year is None:
        raise ValueError("Reference year not found in the table title")
    header = None
    for r in range(min(len(grid), 8)):
        if [str(grid.iat[r, c]).strip() for c in (1, 4, 7)] == ["Air", "Sea", "Total"]:
            header = r
    if header is None:
        raise ValueError("Air / Sea / Total header not found")
    for r in range(header, len(grid)):
        if str(grid.iat[r, 0]).strip().upper() == "YEAR":
            air, sea = _as_number(grid.iat[r, 3]), _as_number(grid.iat[r, 6])
            total = _as_number(grid.iat[r, 9]) if grid.shape[1] > 9 else None
            if air is None or sea is None:
                raise ValueError("Non-numeric YEAR row")
            return {"year": year, "air": int(air), "sea": int(sea), "total": None if total is None else int(total)}
    raise ValueError("YEAR row not found")


def parse_rodrigues_exports(grid: pd.DataFrame) -> pd.DataFrame:
    """Rodrigues Digest Table 30: exports of selected commodities to Mauritius, annual columns.

    Returns long rows: commodity, unit, year, quantity (as published unit).
    Cells that are not numbers ('...', 'NA') are skipped (not published).
    """
    header = None
    for r in range(len(grid)):
        if str(grid.iat[r, 0]).strip() == "Commodity":
            header = r
            break
    if header is None:
        raise ValueError("'Commodity' header not found")
    years = {c: _first_year(grid.iat[header, c]) for c in range(2, grid.shape[1])}
    years = {c: y for c, y in years.items() if y is not None}
    rows = []
    for r in range(header + 1, len(grid)):
        name = grid.iat[r, 0]
        unit = grid.iat[r, 1]
        if not isinstance(name, str) or not isinstance(unit, str):
            continue
        for c, y in years.items():
            q = _as_number(grid.iat[r, c])
            if q is not None:
                rows.append({"commodity": name.strip(), "unit": unit.strip(), "year": y, "quantity": q})
    if not rows:
        raise ValueError("No commodity rows parsed")
    return pd.DataFrame(rows)


def to_tonnes(quantity: float, unit: str) -> float | None:
    """Convert a published quantity to tonnes, or None if the unit is not a weight."""
    unit = unit.lower().strip()
    if unit == "tonnes":
        return quantity
    if unit == "kg":
        return quantity / 1000.0
    return None


TRAFFIC_COLUMNS = [
    "from_island",
    "to_island",
    "mode",
    "year",
    "passengers",
    "freight_t",
    "commodity",
    "source",
    "table",
]


def build_traffic(
    tourism_grid: pd.DataFrame,
    arrivals_mode: dict,
    departures_mode: dict,
    exports: pd.DataFrame,
    sources: dict[str, str],
) -> pd.DataFrame:
    """Assemble ``traffic-published.csv`` from the parsed publications.

    Row meaning (see the ``table`` column of the output):
      - Mauritius <-> Rodrigues passengers, all passengers (residents and
        non-residents), air and sea combined (mode 'all': not split by mode
        in this table). Arrivals in Rodrigues are the Mauritius -> Rodrigues
        direction; departures from Rodrigues are Rodrigues -> Mauritius.
      - Rodrigues <-> Reunion passengers, same definition.
      - Rodrigues arrivals/departures by mode, ALL origins/destinations
        (Mauritius, Reunion, other countries), latest year only.
      - Rodrigues exports to Mauritius by commodity in tonnes (selected
        commodities; mode not stated; items counted in units are omitted).
    """
    blocks = parse_rodrigues_passenger_blocks(tourism_grid)
    rows = []
    spec = {
        "2a": ("mauritius", "Table 2a: passenger traffic between the Islands of Mauritius and Rodrigues"),
        "2b": ("reunion", "Table 2b: passenger traffic between the Islands of Rodrigues and Reunion"),
    }
    for key, (other, title) in spec.items():
        for rec in blocks[key].itertuples():
            base = {"mode": "all", "year": rec.year, "freight_t": "", "commodity": "all", "source": sources["tourism"]}
            rows.append(
                {
                    **base,
                    "from_island": other,
                    "to_island": "rodrigues",
                    "passengers": rec.arrivals_in_rodrigues,
                    "table": f"{title}; arrivals in Rodrigues; residents and non-residents; air and sea combined",
                }
            )
            rows.append(
                {
                    **base,
                    "from_island": "rodrigues",
                    "to_island": other,
                    "passengers": rec.departures_from_rodrigues,
                    "table": f"{title}; departures from Rodrigues; residents and non-residents; air and sea combined",
                }
            )
    for direction, data, tab in (
        ("arrivals", arrivals_mode, "Table 20"),
        ("departures", departures_mode, "Table 22"),
    ):
        for mode in ("air", "sea"):
            frm, to = ("all-origins", "rodrigues") if direction == "arrivals" else ("rodrigues", "all-destinations")
            rows.append(
                {
                    "from_island": frm,
                    "to_island": to,
                    "mode": mode,
                    "year": data["year"],
                    "passengers": data[mode],
                    "freight_t": "",
                    "commodity": "all",
                    "source": sources["rodrigues"],
                    "table": f"{tab}: monthly passenger {direction} by mode of transport, YEAR row, both sexes; "
                    "all passengers, ALL origins/destinations (Mauritius, Reunion, other countries)",
                }
            )
    for rec in exports.itertuples():
        tonnes = to_tonnes(rec.quantity, rec.unit)
        if tonnes is None:
            continue
        rows.append(
            {
                "from_island": "rodrigues",
                "to_island": "mauritius",
                "mode": "all",
                "year": rec.year,
                "passengers": "",
                "freight_t": round(tonnes, 3),
                "commodity": rec.commodity.lower(),
                "source": sources["rodrigues"],
                "table": "Table 30: export of selected commodities to the Island of Mauritius "
                f"(published in {rec.unit}, converted to tonnes); cargo manifests; air or sea not stated",
            }
        )
    return pd.DataFrame(rows)[TRAFFIC_COLUMNS]


def ensure_parent(path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
