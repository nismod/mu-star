"""Compare unit strings, treating common spellings of the same unit as equal."""

import re

_SYNONYMS = {
    "m": {"m", "metre", "metres", "meter", "meters"},
    "m/s": {
        "m/s",
        "m s-1",
        "m s^-1",
        "m.s-1",
        "ms-1",
        "ms^-1",
        "m per s",
        "metre per second",
        "metres per second",
        "meter per second",
        "meters per second",
    },
}
_CANONICAL = {spelling: unit for unit, spellings in _SYNONYMS.items() for spelling in spellings}


def normalise_unit(unit: str) -> str:
    """Lower-case a unit, collapse whitespace and map known synonyms to one spelling (e.g. "Metres" to "m")."""
    text = re.sub(r"\s+", " ", str(unit).strip().lower())
    return _CANONICAL.get(text, text)


def units_match(a: str, b: str) -> bool:
    """Whether two unit strings name the same unit, ignoring case, spacing and common synonyms."""
    return normalise_unit(a) == normalise_unit(b)
