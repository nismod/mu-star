"""
Calculate an estimate of direct damage (rehabilitation) costs to assets due to
various perils.

Asset layers are listed in the layer metadata table (config["metadata"]["layer"])
and hazards in the hazard metadata table (config["metadata"]["hazard"]). Asset
types, vulnerability, damage curves and costs come from the damage
configuration workbook (config["damage"]["config"]).

For each layer, once for all hazards:
    split_layer      -> results/damage/{layer}/split.gpq
Then for each layer and hazard:
    hazard_intensity -> results/damage/{layer}/rp/{peril}/{subperil}/intensity.zarr
    damage           -> results/damage/{layer}/rp/{peril}/{subperil}/damage.zarr
Adding or changing a hazard (or its grid) changes the inputs of split_layer, so
snakemake re-splits every layer, and re-runs all their downstream intensities and
damages.

Then, across all layers and hazards:
    damage_summary   -> results/damage/summary.csv

Check the damage configuration workbook with:
    snakemake -c1 {data}/results/damage/damage-config-report.txt

Report what will be calculated, and what is missing, with:
    snakemake -c1 damage_report

Run the example with:
    snakemake -c1 example/results/damage/summary.csv --configfile example/config.yaml
"""

from damages.metadata import read_metadata_table


# Fail at parse time, listing every problem, if the metadata tables are invalid
DAMAGE_LAYERS = read_metadata_table(config["metadata"]["layer"], "layer").set_index("layer", drop=False)
DAMAGE_HAZARDS = read_metadata_table(config["metadata"]["hazard"], "hazard").set_index(
    ["peril", "subperil"], drop=False
)


def layer_metadata(wildcards):
    return DAMAGE_LAYERS.loc[wildcards.layer.removeprefix("layer-")]


def hazard_metadata(wildcards):
    return DAMAGE_HAZARDS.loc[(wildcards.peril.removeprefix("peril-"), wildcards.subperil.removeprefix("subperil-"))]


def layer_path(wildcards):
    return f"{wildcards.data}/{layer_metadata(wildcards).path}"


def hazard_path(wildcards):
    return f"{wildcards.data}/{hazard_metadata(wildcards).path}"


def all_hazard_paths(wildcards):
    return [f"{wildcards.data}/{path}" for path in DAMAGE_HAZARDS.path]


rule validate_damage_config:
    """
    Report missing or inconsistent entries in the damage configuration workbook.

    Test with:
    snakemake -c1 example/results/damage/damage-config-report.txt --configfile example/config.yaml
    """
    input:
        damage_config = config["damage"]["config"],
        script = "workflow/1-damage/damage_validate_config.py",
    output:
        report = "{data}/results/damage/damage-config-report.txt",
    shell:
        """
        python {input.script:q} \
            --damage-config {input.damage_config:q} \
            --output {output.report:q}
        """


rule damage_report:
    """
    Report available asset layers and hazards, and which damage calculations
    will run or are missing configuration, for the data root
    (config["data_root"], default "data"). Writes
    {data_root}/results/damage/report.md and prints it. Runs every time it is
    requested by name.

    Test with:
    snakemake -c1 damage_report --configfile example/config.yaml
    """
    params:
        data_root = config.get("data_root", "data"),
        layers = config["metadata"]["layer"],
        hazards = config["metadata"]["hazard"],
        damage_config = config["damage"]["config"],
        report = f"{config.get('data_root', 'data')}/results/damage/report.md",
        script = "workflow/1-damage/damage_report.py",
    shell:
        """
        python {params.script:q} \
            --data-root {params.data_root:q} \
            --layers {params.layers:q} \
            --hazards {params.hazards:q} \
            --damage-config {params.damage_config:q} \
            --output {params.report:q}
        """


rule split_layer:
    """
    Split vector assets once on the grids of all hazards, defined by GeoZarr
    attributes of the hazard data cubes, so that each split lies within one
    cell of every grid. Adding or changing a hazard re-splits the layer.

    Test with:
    snakemake -c1 example/results/damage/layer-road/split.gpq --configfile example/config.yaml
    """
    input:
        layer = layer_path,
        hazards = all_hazard_paths,
        script = "workflow/1-damage/damage_split.py",
    output:
        # columns: asset attributes, split_id, index_i_g<k>, index_j_g<k> for each distinct hazard grid,
        # length_m, area_m2, share, geometry. Grids are in the file metadata, key mu_star:grids
        split = "{data}/results/damage/{layer}/split.gpq",
    params:
        hazards = lambda wildcards, input: " ".join(f"--hazard '{path}'" for path in input.hazards),
    shell:
        """
        python {input.script:q} \
            --layer {input.layer:q} \
            {params.hazards} \
            --output {output.split:q}
        """


rule hazard_intensity:
    """
    Look up hazard intensity for each split of the layer, on this hazard's
    grid, for all non-spatial hazard dimensions (e.g. return period, epoch,
    climate scenario).

    Test with:
    snakemake -c1 example/results/damage/layer-road/rp/peril-flood/subperil-coastal/intensity.zarr --configfile example/config.yaml
    """
    input:
        split = rules.split_layer.output.split,
        hazard = hazard_path,
        script = "workflow/1-damage/damage_intensity.py",
    output:
        # dimensions: (split, ...hazard_dims), aligned with rows of split.gpq
        intensity = directory("{data}/results/damage/{layer}/rp/{peril}/{subperil}/intensity.zarr"),
    params:
        variable = lambda wildcards: hazard_metadata(wildcards).variable,
    shell:
        """
        python {input.script:q} \
            --splits {input.split:q} \
            --hazard {input.hazard:q} \
            --variable {params.variable:q} \
            --output {output.intensity:q}
        """


rule damage:
    """
    Determine damage fractions wrought upon a given split layer, then calculate
    the resulting rehabilitation costs and expected annual damages, for splits,
    assets and totals by asset type.

    Test with:
    snakemake -c1 example/results/damage/layer-road/rp/peril-flood/subperil-coastal/damage.zarr --configfile example/config.yaml
    """
    input:
        damage_config = config["damage"]["config"],
        split = rules.split_layer.output.split,
        intensity = rules.hazard_intensity.output.intensity,
        script = "workflow/1-damage/damage_calculate.py",
    output:
        # groups: split, asset, asset_type
        # dimensions: (split|asset|asset_type, ...hazard_dims, return_period, quantile)
        damage = directory("{data}/results/damage/{layer}/rp/{peril}/{subperil}/damage.zarr"),
    params:
        hazard = lambda wildcards: hazard_metadata(wildcards).hazard,
        n_samples = config["damage"]["n_samples"],
        quantiles = " ".join(f"--quantile {q}" for q in config["damage"]["quantiles"]),
        seed = config["damage"]["seed"],
        hazard_cv = config["damage"]["hazard_cv"],
        eta_sd = config["damage"]["eta_sd"],
        strict = "--strict" if config["damage"].get("strict", False) else "--no-strict",
    shell:
        """
        python {input.script:q} \
            --splits {input.split:q} \
            --intensity {input.intensity:q} \
            --damage-config {input.damage_config:q} \
            --hazard {params.hazard:q} \
            --n-samples {params.n_samples} \
            {params.quantiles} \
            --seed {params.seed} \
            --hazard-cv {params.hazard_cv} \
            --eta-sd {params.eta_sd} \
            {params.strict} \
            --output {output.damage:q}
        """


def all_damage_outputs(wildcards):
    return [
        f"{wildcards.data}/results/damage/layer-{layer}/rp/peril-{peril}/subperil-{subperil}/damage.zarr"
        for layer in DAMAGE_LAYERS.layer
        for peril, subperil in zip(DAMAGE_HAZARDS.peril, DAMAGE_HAZARDS.subperil)
    ]


rule damage_summary:
    """
    Summarise total damages by asset type, across all layers and hazards.

    Test with:
    snakemake -c1 example/results/damage/summary.csv --configfile example/config.yaml
    """
    input:
        damage = all_damage_outputs,
        script = "workflow/1-damage/damage_summary.py",
    output:
        summary = "{data}/results/damage/summary.csv",
    params:
        damage = lambda wildcards, input: " ".join(f"--damage '{path}'" for path in input.damage),
    shell:
        """
        python {input.script:q} \
            {params.damage} \
            --output {output.summary:q}
        """
