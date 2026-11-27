"""Pre-process economic data for model constraints."""

from economy import (
    CATALOGUE_ROOT,
    cppRosm_edges_path,
    cppRosm_nodes_path,
    cppRosm_roads,
    econ_utilities_folder,
    main_roads,
    utility_full_plots,
    utility_plot_limits,
    roads_path,
    voronoids_fig_path,
    wtp_path,
    wwtp_path,
    substations_path,
)

rule econ_utilities:
    """Relate economic activity to utility assets and produce supporting figures."""
    input:
        output_path="./data/processed/Socio-Economic/BHM/models/hylas_mauritius_h3_9_s_CDEFGHIJKLMNOPQRS/MUS_None_h3_9_ind_CDEFGHIJKLMNOPQRS_2026_11_25_14/MUS_output.parquet",
        wtp=wtp_path,
        wwtp=wwtp_path,
        roads=roads_path,
        cpprosm=cppRosm_roads,
        nodes=cppRosm_nodes_path,
        edges=cppRosm_edges_path,
        substations = substations_path,
        custom_bounds=rules.econ_catalogue.output.custom_bounds,
        code="src/economy/utilities.py",
    output:
        figures=directory(econ_utilities_folder),
    params:
        country_code=config["local_econ"]["country_code"],
        main_roads=main_roads,
        full_plots=utility_full_plots,
        plot_limits=utility_plot_limits,
        dpi=config["figures"]["dpi"],
        voronoids_fig=voronoids_fig_path,
        catalogue_root=CATALOGUE_ROOT,
    log:
        "logs/econ_utilities.log",
    run:
        import globdata

        from economy.utilities import econ_utilities
        from globdata.catalogue import load_catalogue

        current_catalogue = globdata.get_catalogue()
        try:
            globdata.set_catalogue(load_catalogue(root=params.catalogue_root))
            econ_utilities(
                output_path=input.output_path,
                wtp_path=input.wtp,
                wwtp_path=input.wwtp,
                roads_path=input.roads,
                nodes_path=input.nodes,
                edges_path=input.edges,
                substations_path=input.substations,
                catalogue=globdata.CATALOGUE,
                country_code=params.country_code,
                main_roads=params.main_roads,
                full_plots=params.full_plots,
                plot_limits=params.plot_limits,
                dpi=params.dpi,
                voronoids_fig=params.voronoids_fig,
                econ_utilities_figs=output.figures,
                log_path=log[0], 
            )
        finally:
            globdata.set_catalogue(current_catalogue)
