import pytest

from energy.paths import model_data, processed_energy_dir, results_energy_dir, results_name


def test_model_data_is_a_folder_name():
    assert model_data({"model_data": " 20261006-model-data "}) == "20261006-model-data"


@pytest.mark.parametrize("name", [None, "", "..", "a/b", "{data}"])
def test_model_data_rejects_other_values(name):
    with pytest.raises(ValueError):
        model_data({"model_data": name})


def test_processed_files_sit_in_the_model_data_folder(tmp_path):
    assert processed_energy_dir(tmp_path) == tmp_path / "processed" / "energy" / model_data()


def test_results_folder_takes_the_pack_date():
    assert results_name({"model_data": "20261012-model-data"}) == "20261012-results"
    assert results_name({"model_data": "test-pack"}) == "test-pack-results"


def test_results_sit_in_the_results_folder(tmp_path):
    assert results_energy_dir(tmp_path) == tmp_path / "results" / "energy" / results_name()
