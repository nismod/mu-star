"""Check the damage configuration workbook for missing or inconsistent entries.

Writes a report listing each problem found (or "No problems found"). Exits
with an error if ``--strict`` and there are problems.

Example:

    python workflow/1-damage/damage_validate_config.py \\
        --damage-config example/config/asset-type-cost-curves.xlsx \\
        --output damage-config-report.txt
"""

import click

from damages.config import read_damage_config, validate_damage_config


@click.command()
@click.option("--damage-config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False))
@click.option("--strict/--no-strict", default=False)
def main(config_path, output_path, strict):
    problems = validate_damage_config(read_damage_config(config_path))
    lines = [f"Damage configuration: {config_path}", ""]
    lines += [f"- {problem}" for problem in problems] if problems else ["No problems found"]
    with open(output_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    click.echo("\n".join(lines))
    if strict and problems:
        raise click.ClickException(f"{len(problems)} problems found in {config_path}")


if __name__ == "__main__":
    main()
