"""Energy criticality: the unmet demand when one substation, provided line or power plant fails.

Uses the inferred-provided network and demand shares from workflow/0-preprocess/energy.smk
and, if set, two tables in the incoming folder (line circuits, substation names; on Mauritius
read from the CEB network map). Settings are under criticality in config/energy/energy.yaml;
the method is in src/energy/criticality.py and docs/src/infrastructure-energy.md.
"""

from energy.paths import results_name

CRITICALITY = ENERGY.get("criticality", {})
# Optional tables, as paths in the incoming folder.
CRITICALITY_TABLES = {
    name: f"{INCOMING_DIR}/{CRITICALITY[name]}"
    for name in ("line_circuits", "substation_names")
    if CRITICALITY.get(name)
}
# Results of the model-data pack: 20261012-model-data gives 20261012-results.
CRITICALITY_DIR = f"{{data}}/results/energy/{results_name(ENERGY)}/criticality/{INFERRED_PROVIDED_NAME}"


rule build_energy_criticality:
    """
    Fail each substation, provided line and power plant of inferred-provided in turn
    and record the unmet demand at peak (MW) and per day of outage (MWh, at average demand).

    Test with:
    snakemake -c1 data/results/energy/<date>-results/criticality/inferred-provided-mauritius-rodrigues/criticality.csv
    """
    input:
        network=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/{INFERRED_PROVIDED_NAME}.nc",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        demand_levels=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/demand_levels.csv",
        script=ancient("workflow/2-losses/energy_criticality.py"),
        **CRITICALITY_TABLES,
    output:
        criticality=f"{CRITICALITY_DIR}/criticality.csv",
        by_area=f"{CRITICALITY_DIR}/criticality_by_area.csv",
        line_ratings=f"{CRITICALITY_DIR}/line_ratings.csv",
        metadata=f"{CRITICALITY_DIR}/metadata.json",
    params:
        output_dir=CRITICALITY_DIR,
        tables=lambda wildcards, input: " ".join(
            f"--{name.replace('_', '-')} {shlex.quote(input[name])}" for name in CRITICALITY_TABLES
        ),
        line_types=" ".join(
            f"--line-type {int(kv)} {shlex.quote(str(line_type))}"
            for kv, line_type in CRITICALITY.get("line_types", {}).items()
        ),
        s_max_pu=CRITICALITY.get("s_max_pu", 1.0),
        island_carriers=" ".join(
            f"--island-carrier {shlex.quote(str(carrier))}"
            for carrier in CRITICALITY.get("island_carriers", ["thermal", "hydro"])
        ),
    shell:
        """
        python {input.script:q} \
            --network {input.network:q} \
            --substation-weights {input.substation_weights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            {params.tables} \
            {params.line_types} \
            --s-max-pu {params.s_max_pu} \
            {params.island_carriers}
        """
