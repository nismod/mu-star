import pytest

from energy.paths import model_data, processed_energy_dir


def test_model_data_is_a_folder_name():
    assert model_data({"model_data": " 20261006-model-data "}) == "20261006-model-data"


@pytest.mark.parametrize("name", [None, "", "..", "a/b", "{data}"])
def test_model_data_rejects_other_values(name):
    with pytest.raises(ValueError):
        model_data({"model_data": name})


def test_processed_files_sit_in_the_model_data_folder(tmp_path):
    assert processed_energy_dir(tmp_path) == tmp_path / "processed" / "energy" / model_data()
