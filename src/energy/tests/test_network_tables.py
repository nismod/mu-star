import json

import pandas as pd

from energy.network_tables import (
    CEB_TOTAL_NETWORK_LENGTH_KM,
    CEB_TOTAL_NETWORK_LENGTH_SOURCE,
    validate_model_tables,
    write_input_templates,
    write_model_tables,
)


def _model_tables():
    buses = pd.DataFrame({"bus_id": ["A", "B"]})
    lines = pd.DataFrame(
        {
            "line_id": ["AB"],
            "bus0": ["A"],
            "bus1": ["B"],
            "v_nom_kv": [66.0],
            "length_km": [478.9],
            "s_nom_mva": [100.0],
        }
    )
    generators = pd.DataFrame(
        {
            "generator_id": ["plant"],
            "bus_id": ["A"],
            "carrier": ["thermal"],
            "output_capacity_mw": [50.0],
            "marginal_cost": [10.0],
        }
    )
    return buses, lines, generators


def test_input_templates_use_human_capacity_name(tmp_path):
    outputs = write_input_templates(tmp_path / "templates")

    generator_columns = pd.read_csv(outputs.generators).columns.tolist()
    line_columns = pd.read_csv(outputs.lines).columns.tolist()
    assert "output_capacity_mw" in generator_columns
    assert "capacity_mw" not in generator_columns
    assert "p_nom" not in generator_columns
    assert line_columns[:6] == [
        "line_id",
        "bus0",
        "bus1",
        "v_nom_kv",
        "length_km",
        "s_nom_mva",
    ]


def test_model_table_export_adds_capacity_basis_and_validates_length(tmp_path):
    buses, lines, generators = _model_tables()

    outputs, report = write_model_tables(
        buses,
        lines,
        generators,
        tmp_path / "base",
        source="base",
        reference_line_length_km=478.9,
        reference_generation_capacity_mw=50.0,
    )

    exported_generators = pd.read_csv(outputs.generators)
    saved_report = json.loads(outputs.validation.read_text())
    assert exported_generators.loc[0, "capacity_basis"] == "electrical_output"
    assert exported_generators.loc[0, "capacity_unit"] == "MW_e"
    assert report["status"] == "valid"
    assert saved_report["totals"]["generator_output_capacity_mw"] == 50.0
    assert saved_report["checks"]["line_length_against_published_ceb_total"]["status"] == "pass"
    generation_check = saved_report["checks"]["generation_capacity_against_ceb_reported_total"]
    assert generation_check["status"] == "pass"
    assert generation_check["coverage_fraction"] == 1.0


def test_validation_reports_unknown_bus_without_hiding_other_totals():
    buses, lines, generators = _model_tables()
    generators.loc[0, "bus_id"] = "missing"

    report = validate_model_tables(
        buses,
        lines,
        generators,
        source="base",
        reference_line_length_km=478.9,
    )

    assert report["status"] == "invalid"
    assert any("unknown buses" in error for error in report["errors"])
    assert report["totals"]["line_length_km"] == 478.9


def test_base_validation_retains_incomplete_generator_records_as_warnings():
    buses, lines, generators = _model_tables()
    generators.loc[0, "output_capacity_mw"] = None

    report = validate_model_tables(
        buses,
        lines,
        generators,
        source="base",
        reference_line_length_km=478.9,
        allow_incomplete_generators=True,
    )

    assert report["status"] == "valid_with_warnings"
    assert report["errors"] == []
    assert any("omitted from the PyPSA network" in item for item in report["warnings"])


def test_validation_compares_modelled_and_reported_generation_capacity():
    buses, lines, generators = _model_tables()

    report = validate_model_tables(
        buses,
        lines,
        generators,
        source="base",
        reference_generation_capacity_mw=100.0,
        generation_capacity_tolerance_fraction=0.10,
    )

    check = report["checks"]["generation_capacity_against_ceb_reported_total"]
    assert check["status"] == "warning"
    assert check["model_total_mw"] == 50.0
    assert check["reference_total_mw"] == 100.0
    assert check["coverage_fraction"] == 0.5
    assert any("covers 50.0%" in item for item in report["warnings"])


def test_inferred_validation_uses_explicit_whole_network_reference():
    buses, lines, generators = _model_tables()
    lines["length_km"] = CEB_TOTAL_NETWORK_LENGTH_KM
    lines["source"] = "osm"
    anchor = lines.copy()
    anchor["line_id"] = "anchor"
    anchor["length_km"] = 2.0
    anchor["source"] = "substation_anchor"
    lines = pd.concat([lines, anchor], ignore_index=True)

    report = validate_model_tables(
        buses,
        lines,
        generators,
        source="inferred",
        reference_line_length_km=CEB_TOTAL_NETWORK_LENGTH_KM,
        reference_line_length_scope="CEB total transmission and distribution length",
        reference_line_length_source=CEB_TOTAL_NETWORK_LENGTH_SOURCE,
        reference_line_length_note="Whole-network circuit-km coverage check.",
        reference_line_length_sources=("osm",),
    )

    check = report["checks"]["line_length_against_published_ceb_total"]
    assert check["status"] == "pass"
    assert check["reference_total_km"] == 10_492.2
    assert check["model_total_km"] == 10_492.2
    assert check["model_all_lines_total_km"] == 10_494.2
    assert check["included_model_sources"] == ["osm"]
    assert check["reference_scope"] == "CEB total transmission and distribution length"
    assert check["comparison_note"] == "Whole-network circuit-km coverage check."
