import pytest

from damages.units import UnitError, parse_unit, units_match


@pytest.mark.parametrize(
    "a, b",
    [
        ("m", "metres"),
        ("m/s", "ms-1"),
        ("m/s", "metres per second"),
    ],
)
def test_units_match(a, b):
    assert units_match(a, b)


def test_units_differ():
    assert not units_match("m", "m/s")


@pytest.mark.parametrize(
    "unit",
    [
        "",
        "cm",
        "km/h",
        "years",
        "M",  # case matters: not metres
        "Metres",
        "m s-1",
        "metres  per second",  # inner spacing is not collapsed
        "m per s",
    ],
)
def test_unknown_unit_raises(unit):
    with pytest.raises(UnitError, match="unknown unit"):
        parse_unit(unit)


def test_units_match_raises_on_either_side():
    with pytest.raises(UnitError):
        units_match("m", "cm")
    with pytest.raises(UnitError):
        units_match("feet", "m")


def test_parse_unit():
    assert parse_unit(" metres ") == "m"
    assert parse_unit("ms-1") == "m/s"
