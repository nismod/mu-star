"""Census helpers: matching printed area names to official 2022 census codes."""

from __future__ import annotations

import re
import unicodedata

import pandas as pd


def normalise(name) -> str:
    """Lower case, no accents, punctuation as spaces, single spaces."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def clean_code(values: pd.Series) -> pd.Series:
    """Census codes as strings: strip spaces and any '.0' left by spreadsheets."""
    return values.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)


def match_codes(areas: pd.DataFrame, lookup: pd.DataFrame, district_codes: dict, overrides: dict) -> pd.Series:
    """Census code for each CA1 area row (index-aligned with ``areas``)."""
    code_of_district = {normalise(name): code for code, name in district_codes.items()}
    lookup = lookup.assign(key_name=lookup["zone_name"].map(normalise))
    by_district = {(r.district_code, r.key_name): r.zone_id for r in lookup.itertuples()}
    counts = lookup["key_name"].value_counts()
    unique = {r.key_name: r.zone_id for r in lookup.itertuples() if counts[r.key_name] == 1}

    codes = {}
    for index, row in areas.iterrows():
        printed, district = str(row["area_name"]).strip(), str(row.get("district_name", "") or "").strip()
        override = overrides.get(f"{printed}|{district}") or overrides.get(printed)
        if override:
            codes[index] = str(override)
            continue
        key = normalise(printed)
        district_code = code_of_district.get(normalise(district))
        codes[index] = by_district.get((district_code, key)) or unique.get(key)
    return pd.Series(codes, dtype=object)
