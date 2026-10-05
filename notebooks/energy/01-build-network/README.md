# 01 Build network

1. Edit `src/energy/`.
2. Rebuild: `snakemake -c1 build_energy_networks` in a terminal, or set
   `RUN_BUILD = True` in the notebook.
3. Rerun `01_build_network.ipynb` and pick a network to see its summary, map,
   node and edge tables, validation report and PyPSA network.

The notebook also lists the rules behind `build_energy_networks` and shows how
to build one network by asking for its output file.
