library(data.table)
library(Btoolkit)
library(cppRosm)
library(readr)

options(max.print = 50)

# OLDER FILE
# extract_graph("datasets/mauritius/raw/osm/mauritius-260621.osm.pbf", out = "datasets/mauritius/processed/cppRosm_network/")

extract_graph(snakemake@input[[1]], out = snakemake@params[[1]])
