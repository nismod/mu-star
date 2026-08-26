# 01 · Build network

The tight development loop for the energy model:

1. edit `src/energy/`
2. rebuild: `snakemake -c1 energy_network`
3. re-run `00_build_network.ipynb` to inspect nodes/edges, tables, the PyPSA
   network, and to compare products.
