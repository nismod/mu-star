"""Energy criticality: the unmet demand when one component of the network fails.

Two rules analyse the inferred-provided network, with demand shares from
workflow/0-preprocess/energy.smk and, if set, two tables in the incoming folder (line
circuits, substation names; on Mauritius read from the CEB network map):

- build_energy_criticality_flow fails each substation, transmission line and plant and
  solves a linear power flow on the transmission network (src/energy/criticality.py);
- build_energy_criticality_connectivity fails the same components and each distribution
  line, from the connectivity of the network alone (src/energy/connectivity.py).

Settings are under criticality in config/energy/energy.yaml; the method is in
docs/src/infrastructure-energy.md.
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
CRITICALITY_RESULTS_DIR = f"{{data}}/results/energy/{results_name(ENERGY)}/criticality"
CRITICALITY_FLOW_DIR = f"{CRITICALITY_RESULTS_DIR}/flow/{INFERRED_PROVIDED_NAME}"
CRITICALITY_CONNECTIVITY_DIR = f"{CRITICALITY_RESULTS_DIR}/connectivity/{INFERRED_PROVIDED_NAME}"
MAX_RADIAL_LINE_VOLTAGE_KV = CRITICALITY.get("max_radial_line_voltage_kv", 35)
ISLAND_CARRIER_OPTIONS = " ".join(
    f"--island-carrier {shlex.quote(str(carrier))}"
    for carrier in CRITICALITY.get("island_carriers", ["thermal", "hydro"])
)


def criticality_table_options(wildcards, input):
    """Return the command-line options that pass the optional tables."""
    return " ".join(f"--{name.replace('_', '-')} {shlex.quote(input[name])}" for name in CRITICALITY_TABLES)


rule build_energy_criticality_flow:
    """
    Fail each substation, transmission line and power plant of inferred-provided in turn,
    solve a linear power flow on the lines above max_radial_line_voltage_kv, and record the
    unmet demand at peak (MW) and per day of outage (MWh, at average demand).

    Test with:
    snakemake -c1 data/results/energy/<date>-results/criticality/flow/inferred-provided-mauritius-rodrigues/criticality.csv
    """
    input:
        network=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/{INFERRED_PROVIDED_NAME}.nc",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        demand_levels=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/demand_levels.csv",
        script=ancient("workflow/2-losses/energy_criticality_flow.py"),
        **CRITICALITY_TABLES,
    output:
        criticality=f"{CRITICALITY_FLOW_DIR}/criticality.csv",
        by_area=f"{CRITICALITY_FLOW_DIR}/criticality_by_area.csv",
        line_ratings=f"{CRITICALITY_FLOW_DIR}/line_ratings.csv",
        metadata=f"{CRITICALITY_FLOW_DIR}/metadata.json",
    params:
        output_dir=CRITICALITY_FLOW_DIR,
        tables=criticality_table_options,
        line_types=" ".join(
            f"--line-type {int(kv)} {shlex.quote(str(line_type))}"
            for kv, line_type in CRITICALITY.get("line_types", {}).items()
        ),
        max_radial_line_voltage_kv=MAX_RADIAL_LINE_VOLTAGE_KV,
        s_max_pu=CRITICALITY.get("s_max_pu", 1.0),
        island_carriers=ISLAND_CARRIER_OPTIONS,
    shell:
        """
        python {input.script:q} \
            --network {input.network:q} \
            --substation-weights {input.substation_weights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            {params.tables} \
            {params.line_types} \
            --max-radial-line-voltage-kv {params.max_radial_line_voltage_kv} \
            --s-max-pu {params.s_max_pu} \
            {params.island_carriers}
        """


rule build_energy_criticality_connectivity:
    """
    Fail each substation, transmission line, power plant and distribution line of
    inferred-provided in turn and record the unmet demand at peak (MW) and per day of
    outage (MWh, at average demand), from the connectivity of the network alone, with the
    distribution lines run radially and no switching.

    Test with:
    snakemake -c1 data/results/energy/<date>-results/criticality/connectivity/inferred-provided-mauritius-rodrigues/criticality.csv
    """
    input:
        network=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/{INFERRED_PROVIDED_NAME}.nc",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        node_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_nodes.csv",
        demand_levels=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/demand_levels.csv",
        script=ancient("workflow/2-losses/energy_criticality_connectivity.py"),
        **CRITICALITY_TABLES,
    output:
        criticality=f"{CRITICALITY_CONNECTIVITY_DIR}/criticality.csv",
        by_area=f"{CRITICALITY_CONNECTIVITY_DIR}/criticality_by_area.csv",
        metadata=f"{CRITICALITY_CONNECTIVITY_DIR}/metadata.json",
    params:
        output_dir=CRITICALITY_CONNECTIVITY_DIR,
        tables=criticality_table_options,
        max_radial_line_voltage_kv=MAX_RADIAL_LINE_VOLTAGE_KV,
        island_carriers=ISLAND_CARRIER_OPTIONS,
    shell:
        """
        python {input.script:q} \
            --network {input.network:q} \
            --substation-weights {input.substation_weights:q} \
            --node-weights {input.node_weights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            {params.tables} \
            --max-radial-line-voltage-kv {params.max_radial_line_voltage_kv} \
            {params.island_carriers}
        """
