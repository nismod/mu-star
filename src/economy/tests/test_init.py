from economy import CATALOGUE_ROOT, OUTPUT_ROOT, main_roads


def test_model_roots_are_exported():
    assert CATALOGUE_ROOT == "../catalogue"
    assert OUTPUT_ROOT == "../model/py/BHM"


def test_default_main_roads_are_explicit():
    assert main_roads == ["trunk"]
