# 00 Data review

Look at the inputs before building: the cleaned CEB data (substations, 66 kV
routes, generation sites, `generators.csv` and the demand tables), the
OpenStreetMap roads, power features and island outlines, and the night-light
image and targets.

Run `00_data_review.ipynb` top to bottom. A missing file does not stop it: the
cell prints the command that makes the file, for example
`snakemake -c1 data/processed/energy/provided/generators.csv`.
