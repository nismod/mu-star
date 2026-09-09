"""Human-readable network tables and lightweight model validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

GENERATOR_REQUIRED_COLUMNS = (
    "generator_id",
    "bus_id",
    "carrier",
    "output_capacity_mw",
    "marginal_cost",
)
GENERATOR_TEMPLATE_COLUMNS = (
    "generator_id",
    "name",
    "bus_id",
    "carrier",
    "output_capacity_mw",
    "marginal_cost",
    "efficiency",
    "source",
)
GENERATOR_EXPORT_COLUMNS = (
    "generator_id",
    "name",
    "bus_id",
    "carrier",
    "output_capacity_mw",
    "capacity_unit",
    "capacity_basis",
    "marginal_cost",
    "efficiency",
    "source",
)
LINE_REQUIRED_COLUMNS = (
    "line_id",
    "bus0",
    "bus1",
    "v_nom_kv",
    "length_km",
    "s_nom_mva",
)
LINE_TEMPLATE_COLUMNS = (
    *LINE_REQUIRED_COLUMNS,
    "source_route_id",
    "source",
)

# CEB reports 442 km of overhead and 36.9 km of underground 66 kV lines.
CEB_TRANSMISSION_LENGTH_KM = 478.9
CEB_TRANSMISSION_LENGTH_SOURCE = "https://ceb.mu/fact-sheets/grid-infrastructure"
# The same CEB fact sheet reports 10,492.2 circuit-km across transmission,
# medium-voltage distribution and low-voltage distribution. This is the
# appropriate published comparator for the island-wide inferred network.
CEB_TOTAL_NETWORK_LENGTH_KM = 10_492.2
CEB_TOTAL_NETWORK_LENGTH_SOURCE = CEB_TRANSMISSION_LENGTH_SOURCE
# CEB Annual Report 2023-2024, pp. 50-51: grand total installed capacity,
# including CEB, IPP, SSDG and MSDG generation.
CEB_REPORTED_INSTALLED_GENERATION_MW = 881.56
CEB_REPORTED_GENERATION_CAPACITY_SOURCE = (
    "https://ceb.mu/files/files/publications/Annual%20Report/CEB%20AR%202023-2024.pdf"
)


@dataclass(frozen=True)
class ModelTableOutputs:
    generators: Path
    lines: Path
    validation: Path


@dataclass(frozen=True)
class InputTemplateOutputs:
    generators: Path
    lines: Path


def _missing_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> list[str]:
    return sorted(set(columns) - set(frame.columns))


def _ordered_csv_frame(
    frame: pd.DataFrame,
    preferred_columns: tuple[str, ...],
) -> pd.DataFrame:
    """Return a CSV-safe frame with the public columns first."""
    result = pd.DataFrame(frame.drop(columns="geometry", errors="ignore")).copy()
    for column in preferred_columns:
        if column not in result:
            result[column] = pd.NA
    extras = [column for column in result.columns if column not in preferred_columns]
    return result[[*preferred_columns, *extras]]


def normalise_generator_table(generators: pd.DataFrame) -> pd.DataFrame:
    """Keep the human schema stable while retaining useful source columns."""
    result = _ordered_csv_frame(generators, GENERATOR_EXPORT_COLUMNS)
    if "capacity_unit" not in generators:
        result["capacity_unit"] = "MW_e"
    if "capacity_basis" not in generators:
        result["capacity_basis"] = "electrical_output"
    return result


def normalise_line_table(lines: pd.DataFrame) -> pd.DataFrame:
    """Keep the human line schema stable while retaining provenance columns."""
    return _ordered_csv_frame(lines, LINE_TEMPLATE_COLUMNS)


def write_input_templates(output_dir: Path) -> InputTemplateOutputs:
    """Write header-only CSVs that document accepted user-input schemas."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generators_path = output_dir / "generators.csv"
    lines_path = output_dir / "lines.csv"
    pd.DataFrame(columns=GENERATOR_TEMPLATE_COLUMNS).to_csv(generators_path, index=False)
    pd.DataFrame(columns=LINE_TEMPLATE_COLUMNS).to_csv(lines_path, index=False)
    return InputTemplateOutputs(generators=generators_path, lines=lines_path)


def _check_ids(
    frame: pd.DataFrame,
    column: str,
    label: str,
    errors: list[str],
) -> None:
    if column not in frame:
        return
    values = frame[column]
    if values.isna().any() or values.astype(str).str.strip().eq("").any():
        errors.append(f"{label}.{column} contains blank values")
    if values.astype(str).duplicated().any():
        errors.append(f"{label}.{column} contains duplicate values")


def _numeric_values(
    frame: pd.DataFrame,
    column: str,
    label: str,
    errors: list[str],
    *,
    allow_zero: bool,
    allow_missing: bool = False,
) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype="float64")
    raw = frame[column]
    values = pd.to_numeric(raw, errors="coerce")
    blank = raw.isna() | raw.astype(str).str.strip().eq("")
    if (values.isna() & ~blank).any():
        errors.append(f"{label}.{column} contains non-numeric values")
    if values.isna().any() and not allow_missing:
        errors.append(f"{label}.{column} contains blank or non-numeric values")
        return values
    invalid = values.dropna().lt(0) if allow_zero else values.dropna().le(0)
    if invalid.any():
        comparison = "non-negative" if allow_zero else "greater than zero"
        errors.append(f"{label}.{column} must be {comparison}")
    return values


def validate_model_tables(
    buses: pd.DataFrame,
    lines: pd.DataFrame,
    generators: pd.DataFrame,
    *,
    source: str,
    reference_line_length_km: float | None = None,
    reference_line_length_scope: str | None = None,
    reference_line_length_source: str | None = None,
    reference_line_length_note: str | None = None,
    reference_line_length_sources: tuple[str, ...] | None = None,
    line_length_tolerance_fraction: float = 0.35,
    reference_generation_capacity_mw: float | None = None,
    generation_capacity_tolerance_fraction: float = 0.10,
    allow_incomplete_generators: bool = False,
) -> dict[str, object]:
    """Validate the public tables and return a human-reviewable report.

    The published CEB length comparison is deliberately advisory: mapped route
    length and CEB circuit length are not guaranteed to use the same basis.
    """
    errors: list[str] = []
    warnings: list[str] = []

    bus_missing = _missing_columns(buses, ("bus_id",))
    line_missing = _missing_columns(lines, LINE_REQUIRED_COLUMNS)
    generator_missing = _missing_columns(generators, GENERATOR_REQUIRED_COLUMNS)
    if bus_missing:
        errors.append(f"buses missing columns: {bus_missing}")
    if line_missing:
        errors.append(f"lines missing columns: {line_missing}")
    if generator_missing:
        errors.append(f"generators missing columns: {generator_missing}")
        if "output_capacity_mw" in generator_missing and "capacity_mw" in generators:
            errors.append("generators.capacity_mw has been renamed to output_capacity_mw")
    complete_generators = pd.Series(False, index=generators.index)
    if not generator_missing:
        complete_generators = generators[list(GENERATOR_REQUIRED_COLUMNS)].notna().all(axis=1)
    if generators.empty and not generator_missing:
        warnings.append("No generators are present; this topology-only network cannot supply demand.")
    elif allow_incomplete_generators and not generator_missing:
        incomplete_count = int((~complete_generators).sum())
        if incomplete_count:
            warnings.append(
                f"{incomplete_count} generator records are retained for review but "
                "omitted from the PyPSA network until required values are populated."
            )
    if "marginal_cost_basis" in generators:
        proxy_costs = generators["marginal_cost_basis"].astype(str).str.contains("proxy", case=False, na=False)
        if proxy_costs.any():
            warnings.append(
                f"{int(proxy_costs.sum())} generator records use a neutral dispatch-cost "
                "proxy suitable for VoLL topology tests, not operating-cost analysis."
            )

    _check_ids(buses, "bus_id", "buses", errors)
    _check_ids(lines, "line_id", "lines", errors)
    _check_ids(generators, "generator_id", "generators", errors)

    line_lengths = _numeric_values(lines, "length_km", "lines", errors, allow_zero=False)
    _numeric_values(lines, "v_nom_kv", "lines", errors, allow_zero=False)
    _numeric_values(lines, "s_nom_mva", "lines", errors, allow_zero=False)
    generator_capacities = _numeric_values(
        generators,
        "output_capacity_mw",
        "generators",
        errors,
        allow_zero=True,
        allow_missing=allow_incomplete_generators,
    )
    _numeric_values(
        generators,
        "marginal_cost",
        "generators",
        errors,
        allow_zero=True,
        allow_missing=allow_incomplete_generators,
    )

    if "bus_id" in buses:
        bus_ids = set(buses["bus_id"].dropna().astype(str))
        for label, frame, columns in (
            ("lines", lines, ("bus0", "bus1")),
            ("generators", generators, ("bus_id",)),
        ):
            for column in columns:
                if column not in frame:
                    continue
                referenced = set(frame[column].dropna().astype(str))
                missing_bus_ids = sorted(referenced - bus_ids)
                if missing_bus_ids:
                    errors.append(f"{label}.{column} references unknown buses: {missing_bus_ids}")

    total_line_length_km = float(line_lengths.sum()) if not line_lengths.empty else 0.0
    comparison_line_lengths = line_lengths
    if reference_line_length_sources is not None:
        if "source" not in lines:
            errors.append("lines.source is required when reference_line_length_sources is configured")
            comparison_line_lengths = pd.Series(dtype="float64")
        else:
            comparison_line_lengths = line_lengths.loc[lines["source"].astype(str).isin(reference_line_length_sources)]
    comparison_line_length_km = float(comparison_line_lengths.sum()) if not comparison_line_lengths.empty else 0.0
    total_recorded_generator_output_capacity_mw = (
        round(float(generator_capacities.sum()), 9) if not generator_capacities.empty else 0.0
    )
    total_generator_output_capacity_mw = (
        round(float(generator_capacities.loc[complete_generators].sum()), 9) if not generator_capacities.empty else 0.0
    )
    line_length_check: dict[str, object]
    if reference_line_length_km is None:
        line_length_check = {
            "status": "not_applicable",
            "reason": "No like-for-like published length is configured for this source.",
        }
    else:
        relative_difference = abs(comparison_line_length_km - reference_line_length_km) / float(
            reference_line_length_km
        )
        within_tolerance = relative_difference <= line_length_tolerance_fraction
        line_length_check = {
            "status": "pass" if within_tolerance else "warning",
            "model_total_km": comparison_line_length_km,
            "model_all_lines_total_km": total_line_length_km,
            "included_model_sources": list(reference_line_length_sources)
            if reference_line_length_sources is not None
            else "all",
            "reference_total_km": float(reference_line_length_km),
            "relative_difference": relative_difference,
            "tolerance_fraction": line_length_tolerance_fraction,
            "reference_scope": reference_line_length_scope or "published line-length reference",
            "reference_source": reference_line_length_source or CEB_TRANSMISSION_LENGTH_SOURCE,
            "comparison_note": reference_line_length_note
            or (
                "CEB reports 442 km overhead plus 36.9 km underground at 66 kV; "
                "the model total may use a different route/circuit-length basis."
            ),
        }
        if not within_tolerance:
            comparison_label = reference_line_length_scope or "the published line-length reference"
            warnings.append(
                f"Model line length differs from {comparison_label} by "
                f"{relative_difference:.1%}; review coverage and length basis."
            )

    generation_capacity_check: dict[str, object]
    if reference_generation_capacity_mw is None:
        generation_capacity_check = {
            "status": "not_applicable",
            "reason": "No reported installed-generation total is configured for this source.",
        }
    else:
        relative_difference = abs(total_generator_output_capacity_mw - reference_generation_capacity_mw) / float(
            reference_generation_capacity_mw
        )
        coverage_fraction = total_generator_output_capacity_mw / float(reference_generation_capacity_mw)
        within_tolerance = relative_difference <= generation_capacity_tolerance_fraction
        generation_capacity_check = {
            "status": "pass" if within_tolerance else "warning",
            "model_total_mw": total_generator_output_capacity_mw,
            "reference_total_mw": float(reference_generation_capacity_mw),
            "coverage_fraction": coverage_fraction,
            "relative_difference": relative_difference,
            "tolerance_fraction": generation_capacity_tolerance_fraction,
            "capacity_basis": "installed electrical output capacity",
            "report_period": "2023-2024",
            "reference_source": CEB_REPORTED_GENERATION_CAPACITY_SOURCE,
            "comparison_note": (
                "The CEB grand total includes CEB, IPP, SSDG and MSDG generation; "
                "the model includes only generators with capacity and network-bus data."
            ),
        }
        if not within_tolerance:
            warnings.append(
                "Modelled generator output capacity covers "
                f"{coverage_fraction:.1%} of the CEB-reported installed total "
                f"({total_generator_output_capacity_mw:.2f} MW versus "
                f"{reference_generation_capacity_mw:.2f} MW); review missing plant "
                "coverage and scope."
            )

    return {
        "source": source,
        "status": "invalid" if errors else "valid_with_warnings" if warnings else "valid",
        "errors": errors,
        "warnings": warnings,
        "totals": {
            "buses": len(buses),
            "lines": len(lines),
            "generators": int(complete_generators.sum()),
            "generator_records": len(generators),
            "line_length_km": total_line_length_km,
            "generator_output_capacity_mw": total_generator_output_capacity_mw,
            "recorded_generator_output_capacity_mw": (total_recorded_generator_output_capacity_mw),
        },
        "checks": {
            "line_length_against_published_ceb_total": line_length_check,
            "generation_capacity_against_ceb_reported_total": (generation_capacity_check),
        },
    }


def write_model_tables(
    buses: pd.DataFrame,
    lines: pd.DataFrame,
    generators: pd.DataFrame,
    output_dir: Path,
    *,
    source: str,
    reference_line_length_km: float | None = None,
    reference_line_length_scope: str | None = None,
    reference_line_length_source: str | None = None,
    reference_line_length_note: str | None = None,
    reference_line_length_sources: tuple[str, ...] | None = None,
    line_length_tolerance_fraction: float = 0.35,
    reference_generation_capacity_mw: float | None = None,
    generation_capacity_tolerance_fraction: float = 0.10,
    allow_incomplete_generators: bool = False,
) -> tuple[ModelTableOutputs, dict[str, object]]:
    """Write source-specific human tables and their validation report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generators_path = output_dir / "generators.csv"
    lines_path = output_dir / "lines.csv"
    validation_path = output_dir / "validation.json"

    normalise_generator_table(generators).to_csv(generators_path, index=False)
    normalise_line_table(lines).to_csv(lines_path, index=False)
    report = validate_model_tables(
        buses,
        lines,
        generators,
        source=source,
        reference_line_length_km=reference_line_length_km,
        reference_line_length_scope=reference_line_length_scope,
        reference_line_length_source=reference_line_length_source,
        reference_line_length_note=reference_line_length_note,
        reference_line_length_sources=reference_line_length_sources,
        line_length_tolerance_fraction=line_length_tolerance_fraction,
        reference_generation_capacity_mw=reference_generation_capacity_mw,
        generation_capacity_tolerance_fraction=(generation_capacity_tolerance_fraction),
        allow_incomplete_generators=allow_incomplete_generators,
    )
    validation_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return (
        ModelTableOutputs(
            generators=generators_path,
            lines=lines_path,
            validation=validation_path,
        ),
        report,
    )
