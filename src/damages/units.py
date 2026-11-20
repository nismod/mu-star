"""Parse hazard intensity unit strings into a canonical symbol.

Parsing is strict: only units listed here are known, spellings are
case-sensitive, and anything else raises ``UnitError`` rather than being
compared as free text. Add a unit (and its accepted spellings) here when a new
hazard metric needs one. If units ever need converting, not just comparing,
consider pint instead.
"""

_SPELLINGS = {
    "m": {"m", "metres"},
    "m/s": {
        "m/s",
        "ms-1",
        "metres per second",
    },
}
_CANONICAL = {spelling: unit for unit, spellings in _SPELLINGS.items() for spelling in spellings}


class UnitError(ValueError):
    """A unit string is not one of the known units."""


def parse_unit(unit: str) -> str:
    """Canonical symbol for a known unit, e.g. "metres" to "m".

    Leading and trailing whitespace is ignored; case and inner spacing are not.

    Raises
    ------
    UnitError
        If the unit is not a known spelling of a known unit.
    """
    text = str(unit).strip()
    try:
        return _CANONICAL[text]
    except KeyError:
        known = ", ".join(repr(u) for u in _SPELLINGS)
        raise UnitError(f"unknown unit {text!r}; known units are {known}") from None


def units_match(a: str, b: str) -> bool:
    """Whether two unit strings name the same known unit.

    Raises
    ------
    UnitError
        If either unit is not known.
    """
    return parse_unit(a) == parse_unit(b)
