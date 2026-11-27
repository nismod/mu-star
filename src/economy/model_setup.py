"""Prepare model constraints and run parameters for the BHM workflow."""

from pathlib import Path


def _make_constraints(constraint_settings):
    from globdata.base_constraints import ModelConstraint

    settings = dict(constraint_settings)
    settings["proxy_to_sector"] = {
        section: set(layers) for section, layers in settings["proxy_to_sector"].items()
    }
    return ModelConstraint(**settings)


def model_name_for(model_settings, constraint_settings):
    """Derive the stable model name from the settings used to prepare a run."""
    from globdata.parameters import model_alias

    constraints = _make_constraints(constraint_settings)
    return model_alias(
        model_nickname=model_settings["model_nickname"],
        study_region=model_settings["region"],
        final_res=model_settings["final_res"],
        freeze=model_settings["model_freeze"],
        **constraints.__dict__,
    )


def prepare_model_setup(
    *,
    constraint_path,
    model_name,
    model_settings,
    constraint_settings,
    model_params_path,
    job_index_path,
    log_path,
):
    """Create the model parameter and job-index files consumed by globdata-run."""
    import logging

    import ibis as ib
    import scalenav.oop as snoo
    import yaml
    from globdata.base_params import ModelParams
    from globdata.parameters import region_lookup, set_model_index

    constraints = _make_constraints(constraint_settings)
    calculated_name = model_name_for(model_settings, constraint_settings)
    if calculated_name != model_name:
        raise ValueError(f"Model name mismatch: expected {model_name!r}, calculated {calculated_name!r}")

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    ib.options.interactive = True
    ib.options.graphviz_repr = True
    conn = snoo.connect(
        memory_limit=model_settings["memory_limit"],
        max_temp_directory_size=model_settings["max_temp_directory_size"],
    )

    dose = snoo.table(
        conn,
        name="dose",
        path=constraint_path,
        overwrite=True,
    )
    looked_up = (
        dose.pipe(
            region_lookup,
            value=model_settings["region"],
            columns=constraints.search_columns,
        )
        .order_by(constraints.constraint_id_col)
    )
    if looked_up.count().execute() == 0:
        raise ValueError(f"No constraint rows found for region {model_settings['region']!r}")

    study_region = looked_up[constraints.constraint_id_col].execute().values.tolist()
    job_index = set_model_index(study_region, constraints)

    model_params = ModelParams(
        model_nickname=model_settings["model_nickname"],
        model_name=model_name,
        study_region=study_region,
        final_res=model_settings["final_res"],
        entropy=model_settings["entropy"],
        verbose=model_settings["verbose"],
        n_cores=model_settings["n_cores"],
        samples=model_settings["samples"],
        chains=model_settings["n_cores"],
        target_accept=model_settings["target_accept"],
        max_treedepth=model_settings["max_treedepth"],
        tunes=int(model_settings["samples"] * 0.3),
        trace_frac=model_settings["trace_frac"],
        information_threshold=model_settings["information_threshold"],
    )

    model_params_path = Path(model_params_path)
    job_index_path = Path(job_index_path)
    model_params_path.parent.mkdir(parents=True, exist_ok=True)
    job_index_path.parent.mkdir(parents=True, exist_ok=True)
    with model_params_path.open("w") as stream:
        yaml.safe_dump({**model_params.__dict__, **constraints.__dict__}, stream, sort_keys=False)
    job_index.to_csv(job_index_path, index=False)
    logging.info("Prepared model setup for %s with %d regions", model_name, len(study_region))
