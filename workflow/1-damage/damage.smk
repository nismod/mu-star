"""
Calculate an estimate of direct damage (rehabilitation) costs to assets due to
various perils.

Asset layers are listed in the layer metadata table (config["metadata"]["layer"])
and hazards in the hazard metadata table (config["metadata"]["hazard"]). Asset
types, vulnerability, damage curves and costs come from the damage
configuration workbook (config["damage"]["config"]).

For each layer and hazard:
    split_layer      -> results/damage/{layer}/rp/{peril}/{subperil}/split.gpq
    hazard_intensity -> results/damage/{layer}/rp/{peril}/{subperil}/intensity.zarr
    damage           -> results/damage/{layer}/rp/{peril}/{subperil}/damage.zarr
Then, across all layers and hazards:
    damage_summary   -> results/damage/summary.csv

Check the damage configuration workbook with:
    snakemake -c1 {data}/results/damage/damage-config-report.txt

Report what will be calculated, and what is missing, with:
    snakemake -c1 damage_report

Run the example with:
    snakemake -c1 example/results/damage/summary.csv --configfile example/config.yaml
"""

import pandas as pd


def read_metadata_table(path, key_columns):
    table = pd.read_csv(path, comment="#", dtype=str, skipinitialspace=True).dropna(how="all")
    missing = set(key_columns) - set(table.columns)
    if missing:
        raise ValueError(f"{path} is missing columns {sorted(missing)}")
    return table.set_index(key_columns, drop=False)


DAMAGE_LAYERS = read_metadata_table(config["metadata"]["layer"], ["layer"])
DAMAGE_HAZARDS = read_metadata_table(config["metadata"]["hazard"], ["peril", "subperil"])


def layer_metadata(wildcards):
    return DAMAGE_LAYERS.loc[wildcards.layer.removeprefix("layer-")]


def hazard_metadata(wildcards):
    return DAMAGE_HAZARDS.loc[(wildcards.peril.removeprefix("peril-"), wildcards.subperil.removeprefix("subperil-"))]


def layer_path(wildcards):
    return f"{wildcards.data}/{layer_metadata(wildcards).path}"


def hazard_path(wildcards):
    return f"{wildcards.data}/{hazard_metadata(wildcards).path}"


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
    Split vector assets on the hazard grid, defined by GeoZarr attributes of
    the hazard data cube.

    Test with:
    snakemake -c1 example/results/damage/layer-road/rp/peril-flood/subperil-coastal/split.gpq --configfile example/config.yaml
    """
    input:
        layer = layer_path,
        hazard = hazard_path,
        script = "workflow/1-damage/damage_split.py",
    output:
        # columns: asset attributes, split_id, index_i, index_j, length_m, area_m2, share, geometry
        split = "{data}/results/damage/{layer}/rp/{peril}/{subperil}/split.gpq",
    params:
        measure_crs = config["damage"]["measure_crs"],
    shell:
        """
        python {input.script:q} \
            --layer {input.layer:q} \
            --hazard {input.hazard:q} \
            --measure-crs {params.measure_crs:q} \
            --output {output.split:q}
        """


rule hazard_intensity:
    """
    Look up hazard intensity for each split, for all non-spatial hazard
    dimensions (e.g. return period, epoch, climate scenario).

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
