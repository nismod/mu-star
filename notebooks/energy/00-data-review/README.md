# 00 · Data review (inputs)

Look at the inputs of the energy network build before building anything:

- the cleaned CEB data (`snakemake -c1 data/processed/energy/provided/generators.csv`):
  substations, transmission routes, generation sites, the generators table and
  the demand tables;
- the OpenStreetMap files (downloaded by the build when they are missing):
  roads, power features and the area-of-interest outline;
- the night lights (monthly tiles, downloaded by the build when they are
  missing): the radiance composite, the target points and their metadata.

Open `00_data_review.ipynb` and run it top to bottom. Missing files do not stop
it: each cell prints the command that creates what it needs.
