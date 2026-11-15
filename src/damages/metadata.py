"""Read and validate the layer and hazard metadata tables.

The tables (``config["metadata"]["layer"]`` and ``config["metadata"]["hazard"]``,
see ``config/layer.csv`` and ``config/hazard.csv``) list the asset layers and
hazard data cubes that the damage workflow runs on. Names are used in file
paths and snakemake wildcards, so they must be words (letters, digits and
underscores).
"""

import re
from pathlib import Path

import pandas as pd

NAME_PATTERN = re.compile(r"\w+")

# columns that must be present and non-blank, columns that name things and are unique together
TABLES = {
    "layer": {"required": ["layer", "path"], "names": ["layer"], "key": ["layer"]},
    "hazard": {
        "required": ["peril", "subperil", "path", "variable", "hazard"],
        "names": ["peril", "subperil"],
        "key": ["peril", "subperil"],
    },
}


def load_metadata_table(path: str | Path, kind: str) -> tuple[pd.DataFrame, list[str]]:
    """Read a metadata table, returning it and a list of problems found.

    Parameters
    ----------
    path : str or Path
        CSV file, with ``#`` comment lines.
    kind : str
        "layer" or "hazard".

    Returns
    -------
    table : pandas.DataFrame
        All-string table without blank rows, with values stripped. Empty if
        the file could not be read. Rows with problems are kept.
    problems : list of str
        Human-readable problems: unreadable file, missing columns, blank
        required cells, names that are not words, duplicate keys.
    """
    spec = TABLES[kind]
    try:
        table = pd.read_csv(path, comment="#", dtype=str, skipinitialspace=True).dropna(how="all")
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as err:
        return pd.DataFrame(columns=spec["required"]), [f"{path}: could not read {kind} table: {err}"]
    table = table.map(lambda v: v.strip() if isinstance(v, str) else v).reset_index(drop=True)

    problems = []
    missing = [c for c in spec["required"] if c not in table.columns]
    if missing:
        problems.append(f"{path}: {kind} table is missing columns {missing}")
    present = [c for c in spec["required"] if c in table.columns]
    for column in present:
        blank = table[column].isna() | (table[column] == "")
        if blank.any():
            # data row numbers, counting from 1 after the header
            rows = ", ".join(str(i + 1) for i in table.index[blank])
            problems.append(f"{path}: {kind} table has blank {column!r} in data row(s) {rows}")
    for column in spec["names"]:
        if column not in table.columns:
            continue
        values = table[column].dropna()
        bad = sorted({v for v in values if v and not NAME_PATTERN.fullmatch(v)})
        if bad:
            problems.append(
                f"{path}: {kind} table {column} values must contain only letters, digits and underscores: "
                + ", ".join(repr(v) for v in bad)
            )
    key = spec["key"]
    if all(c in table.columns for c in key):
        complete = table.dropna(subset=key)
        complete = complete[(complete[key] != "").all(axis=1)]
        dupes = complete[complete.duplicated(key, keep=False)]
        for values in dict.fromkeys(map(tuple, dupes[key].to_numpy())):
            problems.append(f"{path}: {kind} table has duplicate {'/'.join(key)}: {'/'.join(values)}")
    return table, problems


def read_metadata_table(path: str | Path, kind: str) -> pd.DataFrame:
    """Read a metadata table, raising a ValueError listing all problems found."""
    table, problems = load_metadata_table(path, kind)
    if problems:
        raise ValueError(f"Invalid {kind} metadata table:\n  " + "\n  ".join(problems))
    return table
