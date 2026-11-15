"""Inter-island transport: air and sea links between Mauritius, Rodrigues and
the outer islands (Agalega).

DRAFT: rules, schemas and a simple first method. This is a separate estimate
from the land journey-to-work analysis (no commuting between islands). It
covers passengers and freight.

1. Flows: observed annual passengers and freight per link and direction,
   with service capacity and utilisation.
2. Criticality: fail each link, airport and port in turn. For each island
   pair, report the passengers and freight that lose their link, how much
   could move to other scheduled services (passengers only: freight is
   limited to specific modes) within spare capacity, and what is stranded.

Baseline passengers (two-way, annual): Mauritius - Rodrigues air ~280k-315k;
sea, Agalega and St Brandon unknown (see config).

Run with:
    snakemake -c1 data/out/transport/interisland/criticality.csv
"""

TI = config["transport_interisland"]
TI_SCRIPT = "workflow/0-preprocess/transport_interisland.py"


def ti_input(key):
    return "{data}/" + TI["inputs"][key]


rule transport_interisland_flows:
    """
    Daily and annual passengers and freight per inter-island link and
    direction, with capacity and utilisation.

    Test with:
    snakemake -c1 data/out/transport/interisland/link-flows.csv
    """
    input:
        links=ti_input("links"),
        nodes=ti_input("nodes"),
        traffic=lambda wc: [p] if Path(p := f"{wc.data}/{TI['inputs']['traffic']}").exists() else [],
        script=TI_SCRIPT,
    output:
        flows="{data}/out/transport/interisland/link-flows.csv",
    params:
        traffic=lambda wc, input: f"--traffic {input.traffic}" if input.traffic else "",
    shell:
        """
        python {input.script} flows --config {TL_CONFIG} \
            --links {input.links} --nodes {input.nodes} {params.traffic} \
            --output {output.flows}
        """


rule transport_interisland_criticality:
    """
    Single-asset failures of inter-island links, airports and ports: affected,
    shifted (to the other mode, within spare capacity) and stranded passengers
    and freight per island pair.

    Test with:
    snakemake -c1 data/out/transport/interisland/criticality.csv
    """
    input:
        flows=rules.transport_interisland_flows.output.flows,
        script=TI_SCRIPT,
    output:
        criticality="{data}/out/transport/interisland/criticality.csv",
    shell:
        """
        python {input.script} criticality --config {TL_CONFIG} --flows {input.flows} --output {output.criticality}
        """
