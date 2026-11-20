import pytest

from damages.metadata import load_metadata_table, read_metadata_table


def _write(tmp_path, text):
    path = tmp_path / "table.csv"
    path.write_text(text)
    return path


def test_read_valid_layer_table(tmp_path):
    path = _write(tmp_path, "# comment\nlayer,path,sector\nroad, a.gpq ,transport\npower_1,b.gpq,\n")
    table = read_metadata_table(path, "layer")
    assert table.layer.tolist() == ["road", "power_1"]
    assert table.path.tolist() == ["a.gpq", "b.gpq"]


def test_header_only_is_valid(tmp_path):
    path = _write(tmp_path, "# comment\nlayer,path,sector,description\n")
    table, problems = load_metadata_table(path, "layer")
    assert table.empty and problems == []


def test_layer_problems_listed_together(tmp_path):
    path = _write(tmp_path, "layer,path\nroad,a.gpq\nroad,b.gpq\nbad-name,c.gpq\nx,\n")
    with pytest.raises(ValueError) as err:
        read_metadata_table(path, "layer")
    message = str(err.value)
    assert "duplicate layer: road" in message
    assert "'bad-name'" in message
    assert "blank 'path' in data row(s) 4" in message


def test_hazard_problems(tmp_path):
    path = _write(
        tmp_path,
        "peril,subperil,path,variable,hazard\n"
        "flood,coastal,a.zarr,depth,h1\n"
        "flood,coastal,b.zarr,depth,h2\n"
        "flood river,pluvial,c.zarr,,h3\n",
    )
    table, problems = load_metadata_table(path, "hazard")
    assert len(table) == 3
    text = "\n".join(problems)
    assert "duplicate peril/subperil: flood/coastal" in text
    assert "'flood river'" in text
    assert "blank 'variable' in data row(s) 3" in text


def test_missing_columns_and_unreadable(tmp_path):
    table, problems = load_metadata_table(_write(tmp_path, "peril,subperil\nflood,coastal\n"), "hazard")
    assert "missing columns ['path', 'variable', 'hazard']" in problems[0]
    table, problems = load_metadata_table(tmp_path / "nope.csv", "layer")
    assert table.empty and "could not read" in problems[0]
    table, problems = load_metadata_table(_write(tmp_path, ""), "layer")
    assert table.empty and "could not read" in problems[0]
