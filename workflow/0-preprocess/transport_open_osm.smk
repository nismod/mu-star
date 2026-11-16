"""Open-data fallback for the land transport and inter-island inputs.

The road and metro networks, schools and airports / ports were to be provided by hand
(docs/src/transport-data.md, items 7, 10, 11, 15-17 and 20). If they are not, these rules build
them from open data, in the schemas and at the locations the transport workflow expects, so the
rest of the workflow runs unchanged:

    OpenStreetMap extract (ODbL) ──┬─> roads.gpkg per island        (item 11)
                                   ├─> metro stations, lines, order (items 15-17, Mauritius)
                                   ├─> schools.gpkg per island      (item 10; + official primary schools)
                                   └─> interisland nodes.gpkg       (item 20)
    WorldPop (CC BY 4.0) ──────────────> population rasters         (item 7)

Everything is a fallback and has limits (see each script's docstring): no enrolment (placeholder
capacity per level), no metro run times, Rodrigues schools poorly mapped, ports from a short
documented list. Replace the output by the provided data when available: these rules only write
files, they do not change config.

Wildcards:
    {data}    data root (normally "data")
    {island}  "mauritius" or "rodrigues"

Licences:
    OpenStreetMap data: (c) OpenStreetMap contributors, Open Database Licence 1.0
        https://www.openstreetmap.org/copyright. Outputs derived from it are a Derived Database
        and must be attributed the same way and offered under the ODbL.
    WorldPop: CC BY 4.0, https://www.worldpop.org (cite the R2025A constrained 100 m product).
    Geoportal primary school list: Government of Mauritius open data, check the layer's terms.

Run everything with:
    snakemake -c2 transport_open_osm_all
"""

OSM_PBF_URL = "https://download.openstreetmap.fr/extracts/africa/mauritius-latest.osm.pbf"
GEOPORTAL_WFS = "https://geoportal.govmu.org/geoserver/ows?service=WFS&version=1.0.0&request=GetFeature"
GEOPORTAL_PRIMARY_SCHOOLS_URL = (
    GEOPORTAL_WFS + "&typename=geonode:primary_schools&outputFormat=application/json&srsName=EPSG:4326"
)
WORLDPOP_TOTAL_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2022/MUS/v1/100m/constrained/"
    "mus_pop_2022_CN_100m_R2025A_v1.tif"
)
# Age band rasters (both sexes, persons): AA is the lower age of a five-year band (05 = ages 5-9).
WORLDPOP_AGE_URL = (
    "https://data.worldpop.org/GIS/AgeSex_structures/Global_2015_2030/R2025A/2022/MUS/v1/100m/constrained/"
    "mus_t_{age}_2022_CN_100m_R2025A_v1.tif"
)
WORLDPOP_AGE_BANDS = ["05", "10", "15"]

TOSM_SCRIPTS = "workflow/0-preprocess"
TOSM_PBF = "{data}/incoming/Infrastructure/OpenStreetMap/mauritius-latest.osm.pbf"
TOSM_TRANSPORT = "{data}/incoming/Infrastructure/Transport"
TOSM_ROADS = TOSM_TRANSPORT + "/{island}/road/roads.gpkg"
TOSM_METRO = TOSM_TRANSPORT + "/mauritius/metro"
TOSM_SCHOOLS = "{data}/incoming/Education/{island}/schools.gpkg"
TOSM_OFFICIAL_PRIMARY = "{data}/incoming/Education/mauritius/geoportal/primary_schools.geojson"
TOSM_INTERISLAND_NODES = TOSM_TRANSPORT + "/interisland/nodes.gpkg"
TOSM_WORLDPOP = "{data}/incoming/Population/WorldPop"


def tosm_official_schools(wc):
    """Official primary school list (Mauritius only; Rodrigues has none)."""
    return [TOSM_OFFICIAL_PRIMARY.format(data=wc.data)] if wc.island == "mauritius" else []


def tosm_official_flag(wc, input):
    return f"--official {input.official[0]}" if input.official else ""


# The OSM extract itself is downloaded by transport_open_census_download_osm
# (workflow/0-preprocess/transport_open_census.smk), to the path in TOSM_PBF.


rule transport_open_osm_download_primary_schools:
    """
    Download the official list of primary schools (geoportal layer geonode:primary_schools, WFS
    GeoJSON, EPSG:4326): 259 schools with name and address, no enrolment.

    Test with:
    snakemake -c1 data/incoming/Education/mauritius/geoportal/primary_schools.geojson
    """
    output:
        geojson=TOSM_OFFICIAL_PRIMARY,
    shell:
        """
        mkdir -p $(dirname {output.geojson})
        curl -sS -f -L --retry 3 -o {output.geojson}.part "{GEOPORTAL_PRIMARY_SCHOOLS_URL}"
        mv {output.geojson}.part {output.geojson}
        """


rule transport_open_osm_roads:
    """
    Build an island's road network from OpenStreetMap (item 11): drivable highways classified
    motorway ... service, noded at junctions, one-way handled, largest connected component only.
    Writes layer "edges" and a summary JSON (counts and km by class, dropped components).

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/road/roads.gpkg
    """
    wildcard_constraints:
        island="mauritius|rodrigues",
    input:
        pbf=TOSM_PBF,
        script=f"{TOSM_SCRIPTS}/transport_open_osm_roads.py",
    output:
        roads=TOSM_ROADS,
        summary=TOSM_TRANSPORT + "/{island}/road/roads-summary.json",
    shell:
        """
        python {input.script} --pbf {input.pbf} --island {wildcards.island} \
            --output {output.roads} --summary {output.summary}
        """


rule transport_open_osm_metro:
    """
    Build the Metro Express stations, lines and station order from OpenStreetMap route relations
    (items 15-17, Mauritius only): main line (Place d'Armes - Curepipe Central) and Rose Hill -
    Mahatma Gandhi branch. No run times: the metro network uses metro.default_speed_kph.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/mauritius/metro/metro_line_stations.csv
    """
    input:
        pbf=TOSM_PBF,
        script=f"{TOSM_SCRIPTS}/transport_open_osm_metro.py",
    output:
        stations=TOSM_METRO + "/metro_stations.gpkg",
        lines=TOSM_METRO + "/metro_lines.gpkg",
        line_stations=TOSM_METRO + "/metro_line_stations.csv",
    shell:
        """
        python {input.script} --pbf {input.pbf} --output-dir $(dirname {output.stations})
        """


rule transport_open_osm_schools:
    """
    Build an island's schools (item 10): official primary schools (Mauritius) plus OpenStreetMap
    schools classified primary / secondary, duplicates removed. Enrolment is empty and capacity a
    PLACEHOLDER per level (replace by Ministry of Education enrolment).

    Test with:
    snakemake -c1 data/incoming/Education/mauritius/schools.gpkg
    """
    wildcard_constraints:
        island="mauritius|rodrigues",
    input:
        pbf=TOSM_PBF,
        official=tosm_official_schools,
        script=f"{TOSM_SCRIPTS}/transport_open_osm_schools.py",
    output:
        schools=TOSM_SCHOOLS,
        summary="{data}/incoming/Education/{island}/schools-summary.json",
    params:
        official=tosm_official_flag,
    shell:
        """
        python {input.script} --pbf {input.pbf} --island {wildcards.island} {params.official} \
            --output {output.schools} --summary {output.summary}
        """


rule transport_open_osm_interisland_nodes:
    """
    Build the inter-island airports and ports (item 20): OpenStreetMap aerodromes, and ports from a
    short documented fallback list located from OSM features (ports are poorly mapped). Links
    (item 19) are still to be built by hand.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/interisland/nodes.gpkg
    """
    input:
        pbf=TOSM_PBF,
        script=f"{TOSM_SCRIPTS}/transport_open_osm_nodes.py",
    output:
        nodes=TOSM_INTERISLAND_NODES,
        summary=TOSM_TRANSPORT + "/interisland/nodes-summary.json",
    shell:
        """
        python {input.script} --pbf {input.pbf} --output {output.nodes} --summary {output.summary}
        """


rule transport_open_osm_worldpop_total:
    """
    Download the WorldPop R2025A 2022 constrained 100 m population grid for Mauritius (item 7;
    persons per pixel, EPSG:4326, covers Mauritius, Rodrigues, Agalega and St Brandon; about
    1.28 million people).

    Test with:
    snakemake -c1 data/incoming/Population/WorldPop/mus_ppp_2022_constrained.tif
    """
    output:
        raster=TOSM_WORLDPOP + "/mus_ppp_2022_constrained.tif",
    shell:
        """
        mkdir -p $(dirname {output.raster})
        curl -sS -f -L --retry 3 -o {output.raster}.part {WORLDPOP_TOTAL_URL}
        mv {output.raster}.part {output.raster}
        """


rule transport_open_osm_worldpop_age:
    """
    Download a WorldPop 2022 constrained age-band grid (both sexes, five-year band starting at
    {age}: 05 = ages 5-9). Optional, for the school-age children.

    Test with:
    snakemake -c1 data/incoming/Population/WorldPop/age/mus_t_05_2022_constrained.tif
    """
    wildcard_constraints:
        age="[0-9]{2}",
    output:
        raster=TOSM_WORLDPOP + "/age/mus_t_{age}_2022_constrained.tif",
    params:
        url=lambda wc: WORLDPOP_AGE_URL.format(age=wc.age),
    shell:
        """
        mkdir -p $(dirname {output.raster})
        curl -sS -f -L --retry 3 -o {output.raster}.part {params.url}
        mv {output.raster}.part {output.raster}
        """


rule transport_open_osm_all:
    """
    Everything the open-data fallback builds, for the default data root.

    Test with:
    snakemake -c2 transport_open_osm_all
    """
    input:
        expand(TOSM_ROADS, data="data", island=["mauritius", "rodrigues"]),
        expand(TOSM_SCHOOLS, data="data", island=["mauritius", "rodrigues"]),
        expand(TOSM_METRO + "/metro_line_stations.csv", data="data"),
        expand(TOSM_INTERISLAND_NODES, data="data"),
        expand(TOSM_WORLDPOP + "/mus_ppp_2022_constrained.tif", data="data"),
        expand(TOSM_WORLDPOP + "/age/mus_t_{age}_2022_constrained.tif", data="data", age=WORLDPOP_AGE_BANDS),
