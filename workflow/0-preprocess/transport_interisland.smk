"""Inter-island transport: air and sea links between Mauritius, Rodrigues and
the outer islands (Agalega).

DRAFT: rules, schemas and a simple first method. This is a separate estimate
from the land journey-to-work analysis (no commuting between islands). It
covers passengers and freight.

1. Flows: observed annual passengers and freight per link and direction,
   with service capacity and utilisation.
2. Criticality: fail each link, airport and port in turn. For each island
   pair, report the passengers and freight that lose their link, how much
   could move to the other mode (air <-> sea) within its spare capacity, and
   what is left stranded.

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
        traffic=ti_input("traffic"),
        script=TI_SCRIPT,
    output:
        flows="{data}/out/transport/interisland/link-flows.csv",
    shell:
        """
        python {input.script} flows --config {TL_CONFIG} \
            --links {input.links} --nodes {input.nodes} --traffic {input.traffic} \
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
        python {input.script} criticality --flows {input.flows} --output {output.criticality}
        """
