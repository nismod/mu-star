import pytest

from damages.units import normalise_unit, units_match


@pytest.mark.parametrize(
    "a, b",
    [
        ("m", "Metres"),
        (" metre ", "METERS"),
        ("m/s", "m s-1"),
        ("m/s", "ms-1"),
        ("m/s", "Metres  per Second"),
        ("kg", "KG"),
    ],
)
def test_units_match(a, b):
    assert units_match(a, b)


@pytest.mark.parametrize("a, b", [("m", "cm"), ("m", "m/s"), ("m/s", "km/h"), ("m", "years")])
def test_units_differ(a, b):
    assert not units_match(a, b)


def test_normalise_unit():
    assert normalise_unit(" Meters ") == "m"
    assert normalise_unit("Years") == "years"
