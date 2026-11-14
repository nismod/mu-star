"""Land transport, part 2 of 2: baseline assignment and single-asset criticality.

DRAFT. Needs the outputs of workflow/0-preprocess/transport_land.smk.

1. Baseline: assign peak-period commuting demand (all modes at once) to the
   multimodal network with transport_flow_model (sequential, capacity constrained,
   so bus and metro capacity binds; roads unconstrained unless configured).
2. Failure sets: one scenario per physical asset that carries baseline flow
   (a road edge, together with the bus segments over it, or a metro segment),
   split into a fixed number of batches.
3. Criticality: per batch, transport_flow_model.disrupt reroutes the affected
   flows; we report the changes in journey time, distance and generalised cost,
   and the isolated trips.
4. Summary: one row per failed asset, joined to its geometry for mapping.

Wildcards:
    {data}    data root
    {zoning}  zone system, e.g. "h3-r8"
    {batch}   "batch-0000" ... one per criticality job (count: criticality.n_batches)

Run everything with:
    snakemake -c8 data/out/transport/land/h3-r8/criticality/asset-criticality.geoparquet
"""

TL_LOSS_SCRIPTS = "workflow/2-losses"
TL_OUT = "{data}/out/transport/land/{zoning}"
TL_N_BATCHES = int(config["transport_land"]["criticality"]["n_batches"])
TL_BATCHES = [f"batch-{index:04d}" for index in range(TL_N_BATCHES)]


rule transport_baseline_assignment:
    """
    Assign the baseline demand. Writes link flows (by mode), per-path detail
    (needed by disrupt), per-OD journey time / distance / cost, and the demand
    that could not be assigned (PT over capacity, or no path).

    If mode_split.pt_overflow is "car", unassigned PT trips are moved to their
    car centroids and assigned in a second pass.

    Test with:
    snakemake -c1 data/out/transport/land/h3-r8/baseline/link-flows.parquet
    """
    input:
        links="{data}/processed/transport/networks/multimodal/{zoning}/links.parquet",
        demand="{data}/processed/transport/demand/{zoning}/demand-by-mode.parquet",
        script=f"{TL_LOSS_SCRIPTS}/transport_baseline_assignment.py",
    output:
        link_flows=f"{TL_OUT}/baseline/link-flows.parquet",
        paths=f"{TL_OUT}/baseline/paths.parquet",
        od_journeys=f"{TL_OUT}/baseline/od-journeys.parquet",
        unassigned=f"{TL_OUT}/baseline/unassigned.parquet",
        summary=f"{TL_OUT}/baseline/summary.json",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --links {input.links} --demand {input.demand} \
            --output-link-flows {output.link_flows} --output-paths {output.paths} \
            --output-od-journeys {output.od_journeys} --output-unassigned {output.unassigned} \
            --output-summary {output.summary}
        """


rule transport_failure_sets:
    """
    List the single-asset failure scenarios to evaluate: failure units (from the
    network build) whose links carry baseline flow, filtered by asset type and
    road class from config, each given a batch number.

    Test with:
    snakemake -c1 data/out/transport/land/h3-r8/criticality/failure-sets.parquet
    """
    input:
        failure_units="{data}/processed/transport/networks/multimodal/{zoning}/failure-units.parquet",
        link_flows=rules.transport_baseline_assignment.output.link_flows,
        script=f"{TL_LOSS_SCRIPTS}/transport_failure_sets.py",
    output:
        failure_sets=f"{TL_OUT}/criticality/failure-sets.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --failure-units {input.failure_units} --link-flows {input.link_flows} \
            --output {output.failure_sets}
        """


rule transport_criticality_batch:
    """
    Evaluate one batch of single-asset failure scenarios against the baseline.

    scenario-impacts: one row per scenario, with affected / rerouted / isolated
    trips and the totals of extra person-minutes, person-km and cost.
    od-impacts: one row per scenario and affected OD pair (for drill-down; can
    be large).

    Test with:
    snakemake -c1 data/out/transport/land/h3-r8/criticality/batches/batch-0000/scenario-impacts.parquet
    """
    input:
        links="{data}/processed/transport/networks/multimodal/{zoning}/links.parquet",
        paths=rules.transport_baseline_assignment.output.paths,
        link_flows=rules.transport_baseline_assignment.output.link_flows,
        failure_sets=rules.transport_failure_sets.output.failure_sets,
        script=f"{TL_LOSS_SCRIPTS}/transport_criticality_batch.py",
    output:
        scenario_impacts=f"{TL_OUT}/criticality/batches/{{batch}}/scenario-impacts.parquet",
        od_impacts=f"{TL_OUT}/criticality/batches/{{batch}}/od-impacts.parquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} --batch {wildcards.batch} \
            --links {input.links} --paths {input.paths} --link-flows {input.link_flows} \
            --failure-sets {input.failure_sets} \
            --output-scenario-impacts {output.scenario_impacts} --output-od-impacts {output.od_impacts}
        """


rule transport_criticality_summary:
    """
    Gather all batches into one table per failed asset, rank them, and join the
    asset geometry (road edge or metro segment) for mapping and the viewer.

    Test with:
    snakemake -c1 data/out/transport/land/h3-r8/criticality/asset-criticality.geoparquet
    """
    input:
        scenario_impacts=expand(
            f"{TL_OUT}/criticality/batches/{{batch}}/scenario-impacts.parquet",
            batch=TL_BATCHES,
            allow_missing=True,
        ),
        failure_sets=rules.transport_failure_sets.output.failure_sets,
        road_edges="{data}/processed/transport/networks/road/edges.geoparquet",
        metro_segments="{data}/processed/transport/networks/metro/segments.geoparquet",
        baseline_summary=rules.transport_baseline_assignment.output.summary,
        script=f"{TL_LOSS_SCRIPTS}/transport_criticality_summary.py",
    output:
        table=f"{TL_OUT}/criticality/asset-criticality.csv",
        spatial=f"{TL_OUT}/criticality/asset-criticality.geoparquet",
    shell:
        """
        python {input.script} --config {TL_CONFIG} \
            --failure-sets {input.failure_sets} \
            --road-edges {input.road_edges} --metro-segments {input.metro_segments} \
            --baseline-summary {input.baseline_summary} \
            --output-table {output.table} --output-spatial {output.spatial} \
            {input.scenario_impacts}
        """


rule transport_land_all:
    """
    Run the whole land transport analysis for the default zoning.

    snakemake -c8 transport_land_all
    """
    input:
        expand(
            "{data}/out/transport/land/{zoning}/criticality/asset-criticality.geoparquet",
            data=config.get("data_root", "data"),
            zoning="h3-r8",
        ),
