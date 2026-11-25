"""Energy criticality: the unmet demand when one component of the network fails.

One rule, compute_energy_criticality, analyses the inferred-provided network, with demand shares from
workflow/0-preprocess/energy.smk and, if set, two tables in the incoming folder (line circuits, substation names; on
Mauritius read from the CEB network map). It fails each substation, transmission line and plant and solves a linear
power flow on the lines above max_radial_line_voltage_kv (src/energy/criticality.py), and fails each distribution
line, at or below it, from the connectivity of the network (src/energy/connectivity.py). The target rule
build_energy_criticality asks for its tables in the data folder set by data_root, so
`snakemake -c1 build_energy_criticality` runs it.

Settings are under criticality in config/energy/energy.yaml; the method is in docs/src/infrastructure-energy.md.
"""

from energy.paths import results_name

# Optional tables, as paths in the incoming folder.
CRITICALITY_TABLES = {
    name: f"{INCOMING_DIR}/{CRITICALITY[name]}" for name in ("line_circuits", "substation_names") if CRITICALITY.get(name)
}
# Results of the model-data pack: 20261012-model-data gives 20261012-results.
CRITICALITY_DIR = f"{{data}}/results/energy/{results_name(ENERGY)}/criticality/{INFERRED_PROVIDED_NAME}"


def criticality_table_options(wildcards, input):
    """Return the command-line options that pass the optional tables."""
    return " ".join(f"--{name.replace('_', '-')} {shlex.quote(input[name])}" for name in CRITICALITY_TABLES)


rule compute_energy_criticality:
    """
    Fail each component of inferred-provided in turn and record the unmet demand at peak (MW) and per day of outage
    (MWh, at average demand): each substation, transmission line and plant by a linear power flow on the lines above
    max_radial_line_voltage_kv, and each distribution line, at or below it, from the connectivity of the network,
    run radially without switching.

    Test with:
    snakemake -c1 data/results/energy/<date>-results/criticality/inferred-provided-mauritius-rodrigues/criticality.csv
    """
    input:
        network=f"{NETWORKS_DIR}/{INFERRED_PROVIDED_NAME}/{INFERRED_PROVIDED_NAME}.nc",
        substation_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_substations.csv",
        node_weights=f"{DEMAND_DIR}/{INFERRED_PROVIDED_NAME}/service_weights_nodes.csv",
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
        tables=criticality_table_options,
        line_types=" ".join(
            f"--line-type {int(kv)} {shlex.quote(str(line_type))}"
            for kv, line_type in CRITICALITY.get("line_types", {}).items()
        ),
        max_radial_line_voltage_kv=MAX_RADIAL_LINE_VOLTAGE_KV,
        s_max_pu=CRITICALITY.get("s_max_pu", 1.0),
        island_carriers=" ".join(
            f"--island-carrier {shlex.quote(str(carrier))}"
            for carrier in CRITICALITY.get("island_carriers", [])
        ),
    shell:
        """
        python {input.script:q} \
            --network {input.network:q} \
            --substation-weights {input.substation_weights:q} \
            --node-weights {input.node_weights:q} \
            --demand-levels {input.demand_levels:q} \
            --output-dir {params.output_dir:q} \
            {params.tables} \
            {params.line_types} \
            --max-radial-line-voltage-kv {params.max_radial_line_voltage_kv} \
            --s-max-pu {params.s_max_pu} \
            {params.island_carriers}
        """


rule build_energy_criticality:
    """
    Build the criticality tables of inferred-provided in the data folder set by data_root (data/ by default).

    Run with:
    snakemake -c1 build_energy_criticality
    """
    input:
        expand(rules.compute_energy_criticality.output, data=ENERGY_DATA_ROOT),
