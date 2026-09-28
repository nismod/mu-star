# 01 · Build network (build, then inspect)

The development loop for the energy model:

1. edit `src/energy/`;
2. rebuild: `snakemake -c1 build_energy_networks` from the terminal, or set
   `RUN_BUILD = True` in the notebook;
3. re-run `01_build_network.ipynb` to inspect one product: summary, interactive
   map, node and edge tables, validation report and the PyPSA network.

The notebook also explains the rule chain behind `build_energy_networks` and how
to build a single product by naming the file it writes.
