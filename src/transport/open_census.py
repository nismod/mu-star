"""Open 2022 census inputs for the land transport workflow.

Statistics Mauritius publishes the 2022 Housing and Population Census volumes
as Excel workbooks, and OpenStreetMap has the census area boundaries
(Municipal Wards, Village Council Areas and the Rodrigues regions). This
module turns them into the hand-transcription schemas the workflow expects
(docs/src/transport-data.md, items 1-6), with strong validation:

- block parsers for the printed table layout (an area heading row followed
  by ``Both sexes`` / ``Male`` / ``Female`` rows, and, in CA27 / D1, age rows);
- a name normaliser and matcher for census area names, which differ between
  CA1, Appendix II and OSM in punctuation, abbreviations (``R/R``, ``P/W``,
  ``Pamp.``), suffixes (``VCA``), ward formats and split descriptions
  ("North (South in Moka ...)");
- aggregate checks (areas sum to the published district / island totals);
- polygon assembly and checks for the OSM boundaries.

Everything that is not matched automatically must be listed in one of the
explicit override tables below, with a reason. Anything else is an error.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

# --- Districts ---------------------------------------------------------------------

#: 2022 census district codes and the names used in config (transport_land.districts).
DISTRICTS = {
    "11": "Port Louis",
    "12": "Pamplemousses",
    "13": "Rivière du Rempart",
    "14": "Flacq",
    "15": "Grand Port",
    "16": "Savanne",
    "17": "Plaines Wilhems",
    "18": "Moka",
    "19": "Black River",
}

#: Rodrigues is island 2. The census volumes give no district digit or area codes
#: for its six regions (Methodology Report s3.4 defines the hierarchy for
#: Municipal Wards and Village Council Areas only), so we use the synthetic
#: district code 21 and area codes 2101-2106 (region number).
RODRIGUES_DISTRICT_CODE = "21"
RODRIGUES_NAME = "Rodrigues"
RODRIGUES_CODE_NOTE = (
    "synthetic code: no official 4-digit code is published for the Rodrigues regions "
    "(Vol VII Appendix IV lists the regions and their localities only); 2 = island, "
    "1 = synthetic district, 0N = region number"
)

#: Spellings and abbreviations of district names in the census tables (normalised).
DISTRICT_ALIASES = {
    "port louis": "11",
    "p l": "11",
    "pamplemousses": "12",
    "pamp": "12",
    "pample mousses": "12",
    "riviere du rempart": "13",
    "r r": "13",
    "r du rempart": "13",
    "riviere du rempart district": "13",
    "flacq": "14",
    "grand port": "15",
    "g p": "15",
    "savanne": "16",
    "plaines wilhems": "17",
    "p w": "17",
    "moka": "18",
    "black river": "19",
    "b r": "19",
    "riviere noire": "19",
    "rodrigues": RODRIGUES_DISTRICT_CODE,
    "island of rodrigues": RODRIGUES_DISTRICT_CODE,
}

SEXES = ("Both sexes", "Male", "Female")
ALL_AGES = "All ages"
DIRECTIONS = ("north", "south", "east", "west", "part")

#: Area names whose last word is a direction but not a split-part marker.
NAMES_ENDING_IN_DIRECTION = {"grand river south east"}

#: Canonical names of the five towns (Municipal Council Areas), by keyword.
TOWNS = (
    ("port louis", ("port louis",)),
    ("beau bassin rose hill", ("beau bassin", "bassin", "rose hill", "r hill")),
    ("quatre bornes", ("quatre bornes",)),
    ("vacoas phoenix", ("vacoas", "phoenix")),
    ("curepipe", ("curepipe",)),
)

# --- Explicit overrides (reviewed by hand; every entry needs a reason) -----------------

#: CA1 printed area name (stripped) and district code -> Appendix II code, for
#: rows the matcher cannot place. Empty: every CA1 row matches automatically.
CA1_CODE_OVERRIDES: dict[tuple[str, str], str] = {}

#: Appendix II codes with no row in CA1 (nor D2). For every one of them the
#: published district and urban/rural totals equal the sum of the printed rows
#: (checked), so these parts of split areas have no tabulated residents.
CODES_WITHOUT_CA1_ROW = {
    "1191": "Port Louis Ward 1 East (P/L part): not printed; CA1 has the B/R part only (1924)",
    "1192": "Beau Bassin/Rose Hill Ward 6 North (P/L part): not printed; CA1 has the P/W part only (1716)",
    "1221": "Amitie-Gokhoola West (Pamp. part): not printed; CA1 has the R/R part only (1315)",
    "1222": "Belle Vue Maurel North (Pamp. part): not printed; CA1 has the R/R part only (1301)",
    "1316": "Brisee Verdiere North (R/R part): not printed; CA1 has the Flacq part only (1424)",
    "1425": "Dubreuil East (Flacq part): not printed; CA1 has the Moka part only (1815)",
    "1428": "Villebague South (Flacq part): not printed; CA1 has the Pamp. and R/R parts (1216, 1321)",
    "1430": "Roches Noires South (Flacq part): not printed; CA1 has the R/R part only (1312)",
    "1616": "Chamarel East (Savanne part): not printed; CA1 has the B/R part only (1904)",
    "1705": "Cascavelle East (P/W part): not printed; CA1 has the B/R part only (1902)",
    "1706": "Richelieu East (P/W part): not printed; CA1 has the B/R part only (1913)",
    "1707": "Dubreuil West (P/W part): not printed; CA1 has the Moka part only (1815)",
    "1817": "St. Julien West (Moka part): not printed; CA1 has the Flacq part only (1419)",
    "1819": "Lalmatie West (Moka part): not printed; CA1 has the Flacq part only (1426)",
    "1842": "Curepipe Ward 3 East (Moka part): not printed; CA1 has the P/W part only (1743)",
    "1843": "Curepipe Ward 4 East (Moka part): not printed; CA1 has the P/W part only (1744)",
}

#: OSM boundary relations that are not census areas: parents of census areas
#: (towns, a VCA with its parts also mapped) and duplicates. Relation id -> reason.
#: Parents are used for the residual check (parent minus its mapped parts).
OSM_PARENTS = {
    13476400: "Vacoas-Phoenix (town): parent of its wards",
    13476401: "Quatre Bornes (town): parent of its wards",
    13476402: "Beau Bassin / Rose Hill (town): parent of its wards",
    13476403: "Curepipe (town): parent of its wards",
    15395767: "St. Julien d'Hotman VCA (no part): same polygon as its East part (18589270)",
}

#: OSM relation id -> code, where the name and district do not give the code.
OSM_CODE_OVERRIDES: dict[int, str] = {
    # Quatre Soeurs VCA (tagged code=1417, Flacq) lies wholly inside the OSM
    # Grand Port district polygon: the OSM district line differs from the census one.
    15512577: "1417",
}

#: OSM `code` tags that are known to be wrong (relation id -> correct code);
#: any other disagreement between the tag and the matched code is an error.
OSM_CODE_TAG_ERRATA = {
    18466022: "1222",  # Belle Vue Maurel VCA, North is tagged 1211 (the code of Piton VCA, West)
    18509857: "1810",  # Town of Port-Louis, Ward 2, South is tagged 1112 (the code of the North part)
}

#: OSM polygons that contain another census part (outer code, inner code):
#: the outer polygon is mapped whole. The inner part is cut out of it.
KNOWN_NESTED = (
    ("1216", "1428"),  # Villebague VCA, West contains Villebague VCA, South (Flacq part)
    ("1420", "1813"),  # St. Julien d'Hotman VCA, East is the whole VCA (as the unsuffixed relation)
    ("1426", "1819"),  # Lalmatie VCA, East contains Lalmatie VCA, West (Moka part)
    ("1901", "1926"),  # Bambous VCA contains Beau Bassin/Rose Hill Ward 4, West (B/R part)
    ("1607", "1517"),  # Riviere du Poste VCA, West (Savanne) contains Riviere du Poste VCA, East (G/P part)
)

#: Coded areas (with CA1 rows) that OSM does not map. Each group is covered by
#: the residual of an OSM parent polygon (parent minus its mapped parts), which
#: cannot be divided without other information. Code -> (parent relation, reason).
KNOWN_MISSING_GEOMETRY = {
    "1735": (13476400, "Vacoas-Phoenix Ward 5 is not mapped in OSM"),
    "1736": (13476400, "Vacoas-Phoenix Ward 6 West is not mapped in OSM"),
}

# --- Text normalisation ------------------------------------------------------------


def fold(text) -> str:
    """Lower case ASCII: accents removed, typographic quotes and dashes unified."""
    text = str(text).replace("\u2019", "'").replace("\u2018", "'").replace("`", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


ABBREVIATIONS = {"st": "saint", "ste": "sainte", "riv": "riviere", "mt": "mont"}


def simplify(text) -> str:
    """Folded, punctuation as spaces, abbreviations expanded, single spaces."""
    words = re.sub(r"[^a-z0-9]+", " ", fold(text)).split()
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def clean_label(text) -> str:
    """A printed label with surrounding and repeated spaces removed."""
    return re.sub(r"\s+", " ", str(text)).strip()


def district_code(name) -> str | None:
    """District code of a district name or abbreviation, or None."""
    key = simplify(name)
    key = re.sub(r"\b(district|of|the|in)\b", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return DISTRICT_ALIASES.get(key)


def canonical_town(text: str) -> str | None:
    key = simplify(text)
    for name, keywords in TOWNS:
        if any(k in key for k in keywords):
            return name
    return None


@dataclass(frozen=True)
class AreaName:
    """A census area name, parsed.

    kind: ``ward`` (Municipal Ward), ``town`` (a town as a whole), ``region``
    (Rodrigues region) or ``vca`` (Village Council Area, or any other name).
    base: the place name (town name for wards), simplified.
    ward: ward number (region number for Rodrigues regions), or None.
    part: the split-part word (north / south / east / west / part), or None.
    others: the other parts listed in brackets, as (direction, district code).
    """

    kind: str
    base: str
    ward: int | None = None
    part: str | None = None
    others: tuple = field(default=())

    @property
    def key(self) -> tuple:
        number = self.ward if self.kind in ("ward", "town") else None
        return (self.base, number, self.part)


def _parse_others(text: str) -> list[tuple[str, str | None]]:
    """'South in Moka & West in B/R' -> [('south', '18'), ('west', '19')]."""
    out = []
    for piece in re.split(r"&|\band\b", text):
        words = simplify(piece).split()
        if words and words[0] in DIRECTIONS:
            out.append((words[0], district_code(" ".join(words[1:]))))
    return out


def parse_area_name(name) -> AreaName:
    """Parse a census area name as printed in CA1 / D2, Appendix II or OSM."""
    text = fold(name)
    others = []
    for group in re.findall(r"\(([^)]*)\)", text):
        others.extend(_parse_others(group))
    text = re.sub(r"\([^)]*\)?", " ", text)
    # "Richelieu VCA East in District of Plaines Wilhems": the district is context only.
    text = re.sub(r"\b(north|south|east|west)\s+in\s+(the\s+)?district\s+of\s+[a-z ]+", r"\1", text)

    region = re.match(r"^region\s*(\d+)\s*[-,:]?\s*(.*)$", text)
    if region:
        return AreaName("region", simplify(region.group(2)), int(region.group(1)), None, tuple(others))

    ward = re.match(r"^(?:town of\s+)?(.*?)[\s,\-]*\bward\s*(\d+)(.*)$", text)
    if ward:
        town = canonical_town(ward.group(1)) or simplify(ward.group(1))
        rest = simplify(ward.group(3)).split()
        part = next((w for w in reversed(rest) if w in DIRECTIONS), None)
        return AreaName("ward", town, int(ward.group(2)), part, tuple(others))

    if text.startswith("town of "):
        return AreaName("town", canonical_town(text[8:]) or simplify(text[8:]), None, None, tuple(others))

    words = [w for w in simplify(text).split() if w != "vca"]
    part = None
    if words and words[-1] in DIRECTIONS and " ".join(words) not in NAMES_ENDING_IN_DIRECTION:
        part = words.pop()
    return AreaName("vca", " ".join(words), None, part, tuple(others))


# --- Matching ----------------------------------------------------------------------


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_names(
    left: pd.DataFrame,
    right: pd.DataFrame,
    overrides: dict | None = None,
    min_score: float = 0.85,
    margin: float = 0.05,
) -> pd.DataFrame:
    """Match area names in ``left`` to the ids in ``right``, within districts.

    Both frames have ``name`` and ``district_code`` columns; ``right`` also has
    ``id``. A ``district_code`` of None in ``left`` searches every district.
    ``overrides`` maps a left index label to an id.

    Steps, in order (the first that gives a single candidate wins):
    override; exact key (place name, ward number, split part); relaxed part
    (same place and ward, the part missing on one side, a single candidate);
    fuzzy place name (same ward, compatible part, similarity >= ``min_score``
    and ``margin`` ahead of the next best). Several candidates at a step is
    ``ambiguous``; none at all is ``unmatched``. Ids taken by more than one
    left row are marked ``duplicate``.

    Returns ``left``'s index with columns: match_id, method, score, candidates.
    """
    overrides = overrides or {}
    parsed_right = [parse_area_name(n) for n in right["name"]]
    rows = list(zip(right["id"].astype(str), right["district_code"].astype(str), parsed_right))

    out = {}
    for index, row in left.iterrows():
        if index in overrides:
            out[index] = (str(overrides[index]), "override", 1.0, "")
            continue
        name = parse_area_name(row["name"])
        district = row.get("district_code")
        district = None if district is None or pd.isna(district) else str(district)
        pool = [(i, p) for i, d, p in rows if district is None or d == district]

        exact = [i for i, p in pool if p.key == name.key]
        if len(exact) == 1:
            out[index] = (exact[0], "exact", 1.0, "")
            continue
        if len(exact) > 1:
            out[index] = (None, "ambiguous", 1.0, "|".join(exact))
            continue

        same_place = [(i, p) for i, p in pool if p.key[:2] == name.key[:2]]
        if len(same_place) == 1 and (name.part is None or same_place[0][1].part is None):
            out[index] = (same_place[0][0], "relaxed_part", 1.0, "")
            continue

        scored = sorted(
            (
                (_similarity(name.base, p.base), i)
                for i, p in pool
                if p.key[1] == name.key[1] and (p.part == name.part or p.part is None or name.part is None)
            ),
            reverse=True,
        )
        if scored and scored[0][0] >= min_score:
            if len(scored) == 1 or scored[0][0] - scored[1][0] >= margin:
                out[index] = (scored[0][1], "fuzzy", round(scored[0][0], 3), "")
            else:
                close = [i for s, i in scored if scored[0][0] - s < margin]
                out[index] = (None, "ambiguous", round(scored[0][0], 3), "|".join(close))
            continue
        best = "|".join(f"{i}:{s:.2f}" for s, i in scored[:3])
        out[index] = (None, "unmatched", round(scored[0][0], 3) if scored else 0.0, best)

    result = pd.DataFrame.from_dict(out, orient="index", columns=["match_id", "method", "score", "candidates"])
    result = result.reindex(left.index)
    taken = result["match_id"].dropna()
    dup = taken[taken.duplicated(keep=False)].index
    result.loc[dup, "candidates"] = result.loc[dup, "match_id"]
    result.loc[dup, "method"] = "duplicate"
    result.loc[dup, "match_id"] = None
    return result


# --- Workbook parsing --------------------------------------------------------------


def _is_number(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not pd.isna(value)
    text = str(value).strip().replace(",", "")
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", text))


def _number(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    return float(text) if text not in ("", "-", "--") else 0.0


def find_cell(sheet: pd.DataFrame, text: str, max_rows: int = 12, min_col: int = 0) -> tuple[int, int]:
    """(row, column) of the first header cell whose simplified text equals ``text``."""
    target = simplify(text)
    for r in range(min(max_rows, len(sheet))):
        for c in range(min_col, sheet.shape[1]):
            value = sheet.iat[r, c]
            if pd.notna(value) and simplify(value) == target:
                return r, c
    raise ValueError(f"Header {text!r} not found in the first {max_rows} rows")


def parse_blocks(sheet: pd.DataFrame, columns: dict[str, int], start_row: int, age_rows: bool = False) -> pd.DataFrame:
    """Rows of a printed census table laid out in blocks.

    A block is a heading row (a label and no numbers: an area, district or
    island) followed by sex rows (``Both sexes`` / ``Male`` / ``Female``), each
    optionally followed by age rows (``age_rows=True``). ``columns`` maps
    output names to sheet column positions; the label is in column 0.

    Returns one row per sex (and age) row: heading, heading_row (0-based sheet
    row), sex, age_group (``All ages`` on sex rows), sheet_row, and the values.
    """
    records = []
    heading = heading_row = sex = None
    for r in range(start_row, len(sheet)):
        label = sheet.iat[r, 0]
        if pd.isna(label) or not str(label).strip():
            continue
        label = clean_label(label)
        values = [sheet.iat[r, c] for c in columns.values()]
        has_numbers = any(_is_number(v) for v in values)
        if label.lower().startswith(("source", "note", "table of content")):
            continue
        if not has_numbers:
            heading, heading_row, sex = label, r, None
            continue
        if heading is None:
            raise ValueError(f"Row {r + 1}: values before any heading ({label!r})")
        if label in SEXES:
            sex, age = label, ALL_AGES
        elif age_rows and sex is not None:
            age = label
        else:
            raise ValueError(f"Row {r + 1}: unexpected label {label!r} under {heading!r}")
        records.append(
            {
                "heading": heading,
                "heading_row": heading_row,
                "sex": sex,
                "age_group": age,
                "sheet_row": r,
                **{name: _number(v) for name, v in zip(columns, values)},
            }
        )
    return pd.DataFrame.from_records(records)


@dataclass
class Heading:
    level: str  # republic, island, district, district_segment, town, area
    district_code: str | None
    segment: str  # all, urban, rural


def classify_headings(headings: list[str]) -> list[Heading]:
    """Classify the block headings of a by-area census table, in document order.

    Headings name the Republic, the Island of Mauritius, the districts (with
    urban / rural parts), the towns and the areas. Areas are given the district
    of the last district heading. The Island of Rodrigues is a district (21).
    """
    out = []
    district = None
    segment = "all"
    for text in headings:
        t = fold(text)
        seg = "urban" if re.search(r"\burban\b", t) else "rural" if re.search(r"\brural\b", t) else "all"
        wholly = "wholly" in t
        if t.startswith("republic of mauritius"):
            out.append(Heading("republic", None, seg))
            district = None
        elif t.startswith("island of mauritius"):
            out.append(Heading("island", None, seg))
            district = None
        elif t.startswith("island of rodrigues") or t == "rodrigues":
            # Rodrigues is wholly rural (printed so in CA1, not in D2).
            district, segment = RODRIGUES_DISTRICT_CODE, "rural"
            out.append(Heading("district", district, "all"))
        elif re.search(r"\bdistrict\b", t):
            name = re.split(r"\bdistrict\b", t)[0]
            code = district_code(name)
            if code is None:
                raise ValueError(f"Unknown district heading: {text!r}")
            if code != district:
                district = code
                segment = seg if wholly else "all"
                out.append(Heading("district", district, "all"))
            else:
                segment = seg
                out.append(Heading("district_segment", district, seg))
        elif t.startswith("town of") and "ward" not in t:
            out.append(Heading("town", district, segment))
        else:
            if district is None:
                raise ValueError(f"Area heading {text!r} before any district heading")
            out.append(Heading("area", district, segment))
    return out


def blocks_with_levels(blocks: pd.DataFrame) -> pd.DataFrame:
    """Add level, district_code and segment columns to parse_blocks output."""
    headings = blocks.drop_duplicates("heading_row").sort_values("heading_row")
    classes = classify_headings(headings["heading"].tolist())
    info = pd.DataFrame(
        {
            "heading_row": headings["heading_row"].to_numpy(),
            "level": [h.level for h in classes],
            "district_code": [h.district_code for h in classes],
            "segment": [h.segment for h in classes],
        }
    )
    return blocks.merge(info, on="heading_row", how="left")


@dataclass
class Check:
    name: str
    expected: float
    actual: float

    @property
    def ok(self) -> bool:
        return abs(self.expected - self.actual) < 0.5


def check_area_totals(blocks: pd.DataFrame, value_columns: list[str]) -> list[Check]:
    """Published totals against the sums of their parts, for a by-area table.

    Every sex row; for every value column: Both sexes = Male + Female; each
    district (and its urban / rural part) = its areas; each town = its wards in
    the district; Island of Mauritius (and urban / rural) = its areas;
    Republic = Island of Mauritius + Rodrigues.
    """
    checks = []
    b = blocks[blocks["age_group"] == ALL_AGES]
    wide = b.pivot_table(index="heading_row", columns="sex", values=value_columns, aggfunc="sum")
    for col in value_columns:
        both = wide[(col, "Both sexes")]
        parts = wide[(col, "Male")] + wide[(col, "Female")]
        bad = (both - parts).abs() >= 0.5
        checks.append(Check(f"{col}: Both sexes = Male + Female (rows failing: {int(bad.sum())})", 0, int(bad.sum())))

    heads = b.drop_duplicates("heading_row").set_index("heading_row")
    order = list(heads.index)
    areas = heads[heads["level"] == "area"]
    mauritius_codes = set(DISTRICTS)
    for sex in SEXES:
        values = b[b["sex"] == sex].set_index("heading_row")[value_columns]

        def total(rows):
            return values.loc[list(rows)].sum() if len(rows) else pd.Series(0.0, index=value_columns)

        for row, head in heads.iterrows():
            if head["level"] == "district":
                parts = areas.index[areas["district_code"] == head["district_code"]]
                label = f"district {head['district_code']}"
            elif head["level"] == "district_segment":
                parts = areas.index[
                    (areas["district_code"] == head["district_code"]) & (areas["segment"] == head["segment"])
                ]
                label = f"district {head['district_code']} {head['segment']}"
            elif head["level"] == "town":
                following = order[order.index(row) + 1 :]
                parts = []
                for r in following:
                    if heads.loc[r, "level"] != "area" or parse_area_name(heads.loc[r, "heading"]).kind != "ward":
                        break
                    parts.append(r)
                label = f"town {head['heading']}"
            elif head["level"] == "island":
                in_island = areas[areas["district_code"].isin(mauritius_codes)]
                if head["segment"] != "all":
                    in_island = in_island[in_island["segment"] == head["segment"]]
                parts = in_island.index
                label = f"Island of Mauritius {head['segment']}"
            elif head["level"] == "republic":
                parts = areas.index if head["segment"] == "all" else areas.index[areas["segment"] == head["segment"]]
                label = f"Republic {head['segment']}"
            else:
                continue
            sums = total(parts)
            for col in value_columns:
                checks.append(
                    Check(f"{col} {sex}: {label} = sum of {len(parts)} areas", values.loc[row, col], sums[col])
                )
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok]


def summarise_checks(checks: list[Check]) -> dict:
    bad = failed(checks)
    return {
        "checks": len(checks),
        "failed": len(bad),
        "failures": [{"check": c.name, "published": c.expected, "sum": c.actual} for c in bad],
    }


# --- Table extraction --------------------------------------------------------------


def parse_ca1(sheet: pd.DataFrame) -> pd.DataFrame:
    """Table CA1 (Vol VII): population 16+ and employed, by area and sex."""
    _, total_col = find_cell(sheet, "Total", min_col=1)
    _, employed_col = find_cell(sheet, "Employed", min_col=1)
    start, _ = find_cell(sheet, "REPUBLIC OF MAURITIUS", max_rows=20)
    blocks = parse_blocks(sheet, {"population_16_plus": total_col, "employed": employed_col}, start)
    return blocks_with_levels(blocks)


def parse_d2(sheet: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Table D2 (Vol II): resident population by area, sex and 5-year age band.

    Returns the blocks (values: ``all_ages`` and one column per printed band,
    e.g. ``5-9``) and the list of band columns.
    """
    header_row, all_col = find_cell(sheet, "All ages", min_col=1)
    columns = {"all_ages": all_col}
    for c in range(all_col + 1, sheet.shape[1]):
        value = sheet.iat[header_row, c]
        if pd.notna(value) and str(value).strip():
            columns[age_band_label(value)] = c
    start, _ = find_cell(sheet, "REPUBLIC OF MAURITIUS", max_rows=20)
    blocks = blocks_with_levels(parse_blocks(sheet, columns, start))
    return blocks, [c for c in columns if c != "all_ages"]


def age_band_label(value) -> str:
    """Printed age band label without spaces: '5 - 9' -> '5-9', '< 1' -> '<1'."""
    return re.sub(r"\s+", "", str(value).strip())


def parse_d1_single_years(sheet: pd.DataFrame) -> pd.DataFrame:
    """Table D1 (Vol II): resident population by district and single year of age.

    Returns heading, district_code, level, age (int), population (both sexes).
    """
    _, both_col = find_cell(sheet, "Both sexes", min_col=1)
    records = []
    heading = None
    start, _ = find_cell(sheet, "REPUBLIC OF MAURITIUS", max_rows=20)
    for r in range(start, len(sheet)):
        label = sheet.iat[r, 0]
        if pd.isna(label):
            continue
        label = clean_label(label)
        value = sheet.iat[r, both_col]
        if not _is_number(value):
            if not label.lower().startswith(("source", "note")):
                heading = label
            continue
        if re.fullmatch(r"\d+", label):
            records.append({"heading": heading, "age": int(label), "population": _number(value)})
        elif fold(label) == "under 1":
            records.append({"heading": heading, "age": 0, "population": _number(value)})
    out = pd.DataFrame.from_records(records)
    classes = {h: c for h, c in zip(out["heading"].unique(), classify_headings(list(out["heading"].unique())))}
    out["level"] = out["heading"].map(lambda h: classes[h].level)
    out["district_code"] = out["heading"].map(lambda h: classes[h].district_code)
    return out


def parse_appendix_ii(sheet: pd.DataFrame) -> pd.DataFrame:
    """Appendix II (Vol VII): 4-digit codes and names of Municipal Wards and VCAs.

    Returns zone_id, zone_name, district_code (from the district headings),
    sheet_row. Fails if a code does not start with its heading's district code.
    """
    records = []
    district = None
    for r in range(len(sheet)):
        values = [v for v in sheet.iloc[r].tolist() if pd.notna(v) and str(v).strip()]
        if not values:
            continue
        first = str(values[0]).strip()
        if re.fullmatch(r"\d{4}(\.0)?", first):
            if district is None:
                raise ValueError(f"Appendix II row {r + 1}: code before any district heading")
            code = first[:4]
            name = clean_label(values[-1])
            if not code.startswith(district):
                raise ValueError(f"Appendix II: code {code} ({name}) is listed under district {district}")
            records.append({"zone_id": code, "zone_name": name, "district_code": district, "sheet_row": r})
        elif fold(first).startswith("district of"):
            code = district_code(re.sub(r"\(.*\)", "", first)[len("district of") :])
            if code is None:
                raise ValueError(f"Appendix II: unknown district heading {first!r}")
            district = code
    out = pd.DataFrame.from_records(records)
    if out["zone_id"].duplicated().any():
        raise ValueError(f"Appendix II: duplicated codes {out.loc[out.zone_id.duplicated(), 'zone_id'].tolist()}")
    return out


def parse_appendix_iv_regions(sheet: pd.DataFrame) -> pd.DataFrame:
    """Appendix IV (Vol VII): the Rodrigues regions (column headings) and localities.

    Returns zone_id (synthetic 2101-2106), zone_name (as printed, e.g.
    ``Region 1 - La Ferme``), district_code (21), localities (``;``-separated).
    """
    header = None
    for r in range(min(15, len(sheet))):
        cells = [clean_label(v) for v in sheet.iloc[r].tolist() if pd.notna(v)]
        if sum(bool(re.match(r"(?i)region\s*\d", c)) for c in cells) >= 2:
            header = r
            break
    if header is None:
        raise ValueError("Appendix IV: no row of region headings found")
    records = []
    for c in range(sheet.shape[1]):
        value = sheet.iat[header, c]
        if pd.isna(value) or not re.match(r"(?i)region\s*\d", str(value).strip()):
            continue
        name = clean_label(value)
        number = int(re.match(r"(?i)region\s*(\d+)", name).group(1))
        localities = [clean_label(v) for v in sheet.iloc[header + 1 :, c].tolist() if pd.notna(v) and str(v).strip()]
        records.append(
            {
                "zone_id": f"2{RODRIGUES_DISTRICT_CODE[1]}{number:02d}",
                "zone_name": name,
                "district_code": RODRIGUES_DISTRICT_CODE,
                "localities": "; ".join(localities),
            }
        )
    out = pd.DataFrame.from_records(records)
    if sorted(out["zone_id"]) != [f"21{n:02d}" for n in range(1, 7)]:
        raise ValueError(f"Appendix IV: expected regions 1-6, found {out['zone_name'].tolist()}")
    return out


def parse_ca27(sheet: pd.DataFrame) -> pd.DataFrame:
    """Table CA27 (Vol VII): employed 16+ by residence district, sex, age and workplace.

    Returns one row per residence block x sex x age group, with columns
    heading, level, district_code, sex, age_group, Total, the nine districts
    (config names), Not stated, Rodrigues and Outer Islands, Outside Mauritius.
    """
    _, total_col = find_cell(sheet, "Total", min_col=1)
    columns = {"Total": total_col}
    district_row, _ = find_cell(sheet, "Not stated", min_col=1)
    for c in range(total_col + 1, sheet.shape[1]):
        value = sheet.iat[district_row, c]
        if pd.isna(value):
            continue
        if simplify(value) == "not stated":
            columns["Not stated"] = c
        else:
            code = district_code(value)
            if code is None or code not in DISTRICTS:
                raise ValueError(f"CA27: unknown workplace column {value!r}")
            columns[DISTRICTS[code]] = c
    for label in ("Rodrigues and Outer Islands", "Outside Mauritius"):
        columns[label] = find_cell(sheet, label, min_col=1)[1]
    missing = [d for d in DISTRICTS.values() if d not in columns]
    if missing:
        raise ValueError(f"CA27: workplace columns not found: {missing}")
    start, _ = find_cell(sheet, "REPUBLIC OF MAURITIUS", max_rows=20)
    blocks = parse_blocks(sheet, columns, start, age_rows=True)
    return blocks_with_levels(blocks)


CA27_WORKPLACES = [*DISTRICTS.values(), "Not stated", "Rodrigues and Outer Islands", "Outside Mauritius"]


def check_ca27(blocks: pd.DataFrame) -> list[Check]:
    """CA27: row totals, sexes, ages, and districts against the island and Republic."""
    checks = []
    row_sum = blocks[CA27_WORKPLACES].sum(axis=1)
    bad = int(((blocks["Total"] - row_sum).abs() >= 0.5).sum())
    checks.append(Check(f"Total = sum of the 12 workplace columns (rows failing: {bad})", 0, bad))
    values = ["Total", *CA27_WORKPLACES]
    for heading_row, block in blocks.groupby("heading_row"):
        name = block["heading"].iloc[0]
        for sex, rows in block.groupby("sex"):
            all_ages = rows[rows["age_group"] == ALL_AGES][values].sum()
            ages = rows[rows["age_group"] != ALL_AGES][values].sum()
            for col in values:
                checks.append(Check(f"{name} {sex} {col}: all ages = sum of age groups", all_ages[col], ages[col]))
        by_sex = block[block["age_group"] == ALL_AGES].set_index("sex")[values]
        for col in values:
            checks.append(
                Check(
                    f"{name} {col}: Both sexes = Male + Female",
                    by_sex.loc["Both sexes", col],
                    by_sex.loc["Male", col] + by_sex.loc["Female", col],
                )
            )
    districts = blocks[blocks["district_code"].isin(DISTRICTS)]
    island = blocks[(blocks["level"] == "island")].set_index(["sex", "age_group"])[values]
    rod = blocks[blocks["district_code"] == RODRIGUES_DISTRICT_CODE].set_index(["sex", "age_group"])[values]
    republic = blocks[(blocks["level"] == "republic")].set_index(["sex", "age_group"])[values]
    sums = districts.groupby(["sex", "age_group"])[values].sum()
    for idx in island.index:
        for col in values:
            checks.append(
                Check(f"Island of Mauritius {idx} {col} = sum of 9 districts", island.loc[idx, col], sums.loc[idx, col])
            )
            checks.append(
                Check(
                    f"Republic {idx} {col} = Mauritius + Rodrigues",
                    republic.loc[idx, col],
                    island.loc[idx, col] + rod.loc[idx, col],
                )
            )
    return checks


def split_band(total: int, share: float) -> tuple[int, int]:
    """Split an integer count into two integers in proportion ``share``, conserving the total."""
    first = round(total * share)
    first = min(max(first, 0), total)
    return first, total - first


# --- Geometry ----------------------------------------------------------------------


def split_by_line(polygon, direction_point, share: float, tolerance: float = 1e-4):
    """Split ``polygon`` with a straight line into (near, far) parts.

    The line is perpendicular to the direction from the polygon's centroid to
    ``direction_point``, and placed so that the ``near`` part (the side towards
    the point) has ``share`` of the area. An APPROXIMATION, for flagged use only.
    """
    from shapely import affinity
    from shapely.geometry import box

    c = polygon.centroid
    dx, dy = direction_point.x - c.x, direction_point.y - c.y
    import math

    angle = math.degrees(math.atan2(dy, dx))
    rotated = affinity.rotate(polygon, -angle, origin=c)  # the point is now in +x
    minx, miny, maxx, maxy = rotated.bounds
    target = share * polygon.area
    lo, hi = minx, maxx
    for _ in range(100):
        mid = (lo + hi) / 2
        near = rotated.intersection(box(mid, miny - 1, maxx + 1, maxy + 1))
        if near.area > target:
            lo = mid
        else:
            hi = mid
        if abs(near.area - target) <= tolerance * polygon.area:
            break
    near = rotated.intersection(box(mid, miny - 1, maxx + 1, maxy + 1))
    far = rotated.difference(box(mid, miny - 1, maxx + 1, maxy + 1))
    return affinity.rotate(near, angle, origin=c), affinity.rotate(far, angle, origin=c)
