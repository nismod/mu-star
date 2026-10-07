# Energy

The energy workflow models the electricity network of Mauritius and Rodrigues.
The Central Electricity Board (CEB) provided its 66 kV transmission network
but no data on its distribution network, so that is estimated from roads and
night-time lights.

## Data

A `*-star` repository can be run on public data alone, using the inferred
method: the substations and power plants mapped in OpenStreetMap, connected by
a distribution network estimated from roads and night-time lights. For
working with official provided files (the utility's substations, transmission
lines and power plants), or with a combination of the two (the provided
network connected to the estimated distribution network), those files are
needed as well.

The workflow downloads the public data when it is missing: OpenStreetMap
roads, power features and island outlines; twelve monthly VIIRS night-light
images for 2024 (the Earth Observation Group's cloud-free average radiance,
from the ArcGIS image service NighttimeLightsMDNB); and the WorldPop 2020
population grid (100 m cells, adjusted to UN totals).

For Mauritius, the provided data are below. The CEB files are shared under
licence and are not public.

| Data | Source | Folder or file |
| --- | --- | --- |
| Substations and 66 kV lines (Mauritius) | CEB | `Substation/`, `Power Transmission/` |
| Generation sites | CEB | `Generation Source/` |
| Monthly peak demand, annual demand by sector | CEB | `Power Demand/` |
| Distribution network | pending | |
| Power plants and installed capacity | extracted from the CEB Annual Report 2023-24, pp. 50-51 and 97 | `CEB Annual Report/ceb_plant_capacities_2023_24.csv` |
| Plant locations | CEB generation sites, OpenStreetMap, geocoded villages | `CEB Annual Report/ceb_plant_sites.csv` |
| System peak and average demand | extracted from the annual report, pp. 45 and 51 | `CEB Annual Report/ceb_demand_levels_2023_24.csv` |

## Methods

The rules turn the inputs into processed files in
`data/processed/energy/<model_data>/`:

1. **CEB tables** (`provided/`). Substations are moved onto the nearest 66 kV
   route. Each plant in the annual report gets its location from the sites
   table and, on Mauritius, the nearest CEB substation. Rooftop solar and
   plants with no location yet are left out of the networks for now.
2. **Lit areas** (`nightlight/`). The median of the twelve monthly images,
   pixel by pixel; a zero can mean that a month had no cloud-free view. A
   filter from [GridFinder](https://github.com/carderne/gridfinder) (Chris
   Arderne, MIT licence) keeps pixels brighter than their surroundings, and
   those above a threshold become *targets*: places the network must reach.
3. **Networks** (`networks/<network>/`), one for each way of running the model,
   named after how it is made and the area it covers. `base-mauritius` uses the
   provided data alone, which covers Mauritius only: it joins the substations
   along the CEB routes, closing gaps shorter than 75 m, and attaches the
   plants. `inferred-osm-<region>` (public data) and
   `inferred-provided-<region>` (combination) take their power assets from
   OpenStreetMap or from the provided data, for the area set as `region` in
   `config/energy/energy.yaml` (here `mauritius-rodrigues`). They keep the
   drivable roads within 1 km of a target or a power asset, and put back the
   shortest road path to any lit area this cuts off. A two-way street is one
   line; two roads between the same junctions stay two lines. Each substation
   and plant connects to the nearest point on a kept road within 1 km. In
   `inferred-provided` it also connects to the nearest 66 kV line, and
   transformers join the 66 kV and 11 kV lines.
4. **Demand shares** (`demand/`), with PyPSA-Earth's method. Each substation
   supplies the area closer to it than to any other; on Rodrigues the plants
   share the island. An area's share of system demand is 0.6 times its share
   of night-light radiance plus 0.4 times its share of population, and the
   same weights split it between the area's road nodes. PyPSA-Earth uses GDP
   instead of night lights, but GDP grids are too coarse here. System demand,
   from the annual report: 525.7 MW at peak (16 February 2024, p. 45) and
   346.8 MW on average (p. 51).

The estimated lines show where distribution lines probably run, not where they
are. Voltages and capacities are placeholders, in the `model_v_nom_kv` and
`model_s_nom_mva` columns: roads 11 kV and 5 MVA (a typical feeder); 66 kV
lines 50 MVA (one circuit); the connection from a substation or plant to a
road 137 MVA (2,595 MVA of transformers over 19 substations, report p. 71);
and every line of `base-mauritius` 10,000 MVA, so that no line limits flow.
`v_nom_kv` and `s_nom_mva` stay empty until CEB values exist. The distances
and placeholder values are set in `config/energy/energy.yaml`.

## Running the model

Set up the `mu-star` conda environment as in the README. The rules read
inputs from `data/incoming/Infrastructure/Energy/` and write to
`data/processed/energy/<model_data>/` and `data/out/energy/<model_data>/`,
where `model_data` in `config/energy/energy.yaml` names the folder. The
workflow downloads the public data. Project members link or copy the provided
data from the project OneDrive, which uses the same folder names. Instead of
copying individual files, they can use a `yyyymmdd-model-data` pack of
processed files, so nothing needs to run:

```shell
SHARED="<synced OneDrive project folder>"  # holds Incoming Data, Processed Data and Output Data
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
mkdir -p data/incoming/Infrastructure data/processed/energy data/out/energy
ln -s "$SHARED/Incoming Data/Infrastructure/Energy" data/incoming/Infrastructure/
ln -s "$SHARED/Processed Data/Infrastructure/Energy/$MODEL_DATA" data/processed/energy/  # the pack
ln -s "$SHARED/Output Data/Infrastructure/Energy/$MODEL_DATA" data/out/energy/  # its review tables
```

Snakemake writes through these links into the OneDrive. Before changing the
energy code or forcing a rerun (`-F` or `-R`), copy the pack instead with
`cp -Rp`, which keeps the file dates that Snakemake compares.

Then run, from the repository root:

```shell
MODEL_DATA=$(sed -n 's/^ *model_data: *//p' config/energy/energy.yaml)
# public data
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-osm-mauritius-rodrigues/inferred-osm-mauritius-rodrigues.nc"
# provided data
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/base-mauritius/base-mauritius.nc"
# combination, then its demand shares
snakemake -c1 "data/processed/energy/$MODEL_DATA/networks/inferred-provided-mauritius-rodrigues/inferred-provided-mauritius-rodrigues.nc"
snakemake -c1 "data/processed/energy/$MODEL_DATA/demand/inferred-provided-mauritius-rodrigues/service_weights_nodes.csv"
# all three networks
snakemake -c1 build_energy_networks
```

Add `-n` to list what would run. With a pack in place, the network commands
have nothing to do; the demand command still needs the provided data.

## Outputs

In `data/processed/energy/<model_data>/`:

- `networks/<network>/`: the [PyPSA](https://pypsa.org/) network
  (`<network>.nc`); `<network>_metadata.json` with the inputs, settings and
  file checksums; `geoparquet/`, the nodes and edges for GIS; and, for the
  inferred networks, `inferred_distribution/`, the road network as CSV tables.
- `demand/inferred-provided-<region>/`: the service areas, the shares by
  substation and by node, and the peak and average demand.

In `data/out/energy/<model_data>/<network>/`: `generators.csv`, `lines.csv`
and `validation.json`. The validation compares the model with CEB's published
totals, line length (479 km of 66 kV lines for `base-mauritius`, 10,492 km of
all lines for the inferred networks) and installed capacity, and warns beyond
a tolerance. The notebooks in `notebooks/energy/` show the inputs and the
networks.

## Limitations

- Rodrigues has no CEB network data, so there is no substation or 66 kV line
  between its plants. An island with no power asset at all gets a placeholder
  substation on its roads.
- Distribution lines that do not follow roads are missing.
- Voltages and capacities are placeholders, and no power flow is run.
- Supply lost when assets fail (disruption) is not modelled yet.
