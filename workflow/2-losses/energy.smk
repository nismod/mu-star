"""Energy criticality: the unmet demand when one substation, CEB line or power plant fails.

Uses the inferred-provided network and demand shares from workflow/0-preprocess/energy.smk
and two tables read from the CEB network map (line circuits, substation names). Settings
are under criticality in config/energy/energy.yaml; the method is in
src/energy/criticality.py and docs/src/infrastructure-energy.md.
"""

from energy.paths import results_name
from energy.provided import LINE_CIRCUITS_FILE, SUBSTATION_NAMES_FILE

CRITICALITY = ENERGY.get("criticality", {})
CRITICALITY_LINE_TYPES = CRITICALITY.get("line_types", {})
# Results of the model-data pack: 20261012-model-data gives 20261012-results.
CRITICALITY_DIR = f"{{data}}/results/energy/{results_name(ENERGY)}/criticality/{INFERRED_PROVIDED_NAME}"


rule build_energy_criticality:
    """
    Fail each substation, CEB line and power plant of inferred-provided in turn
    and record the unmet demand at peak (MW) and per day of outage (MWh, at average demand).

    Test with:
    snakemake -c1 data/results/energy/<date>-results/criticality/inferred-provided-mauritius-rodrigues/criticality.csv
    """
    input:
        network=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/{INFERRED_PROVIDED_NAME}.nc",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        demand_levels=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/demand_levels.csv",
        line_circuits=f"{INCOMING_DIR}/{LINE_CIRCUITS_FILE}",
        substation_names=f"{INCOMING_DIR}/{SUBSTATION_NAMES_FILE}",
        script=ancient("workflow/2-losses/energy_criticality.py"),
    output:
        criticality=f"{CRITICALITY_DIR}/criticality.csv",
        by_area=f"{CRITICALITY_DIR}/criticality_by_area.csv",
        metadata=f"{CRITICALITY_DIR}/metadata.json",
    params:
        output_dir=CRITICALITY_DIR,
        line_type_66=CRITICALITY_LINE_TYPES.get(66, "149-AL1/24-ST1A 110.0"),
        line_type_132=CRITICALITY_LINE_TYPES.get(132, "243-AL1/39-ST1A 110.0"),
        s_max_pu=CRITICALITY.get("s_max_pu", 1.0),
        island_carriers=" ".join(
            f"--island-carrier {shlex.quote(str(carrier))}"
            for carrier in CRITICALITY.get("island_carriers", ["thermal", "hydro"])
        ),
    shell:
        """
        python {input.script:q} \
            --network {input.network:q} \
            --line-circuits {input.line_circuits:q} \
            --substation-names {input.substation_names:q} \
            --substation-weights {input.substation_weights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            --line-type-66 {params.line_type_66:q} \
            --line-type-132 {params.line_type_132:q} \
            --s-max-pu {params.s_max_pu} \
            {params.island_carriers}
        """
