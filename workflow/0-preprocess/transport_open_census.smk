"""Open data for the census inputs of the land transport workflow.

Downloads the 2022 census workbooks (Statistics Mauritius) and an OSM extract,
and writes the census input files that transport_land.smk reads, at the
paths in config (transport_land.inputs and school.children_table), in the
schemas of docs/src/transport-data.md (items 1-6):

    Vol VII (CA1, CA27, Appendices II, IV) ─┐
    Vol II (D1, D2) ────────────────────────┴─> transport_open_census_tables
        CA1-employment-by-area.csv, appendix-ii-iv-area-codes.csv,
        CA27-residence-by-workplace-district.csv, children-by-area-age.csv
    OSM extract + area codes + CA1 ──────────> transport_open_census_boundaries
        census-areas.gpkg

Each extraction rule writes a review CSV (a log, kept when the rule fails):
the rule fails if anything is unmatched, ambiguous or fails a total check.
Name and geometry exceptions are listed, with reasons, in
src/transport/open_census.py.

Run all with:
    snakemake -c1 data/incoming/Socio-economic/Census/2022/census-areas.gpkg
"""


configfile: "config/transport/transport.yaml"


OC_INPUTS = config["transport_land"]["inputs"]
OC_SCRIPTS = "workflow/0-preprocess"
OC_CENSUS = "{data}/incoming/Socio-economic/Census/2022"
OC_REVIEW = "{data}/processed/transport/national/open-census"
OC_CENSUS_URL = "https://statsmauritius.govmu.org/Documents/Census_and_Surveys/Census2022"
OC_VOL7 = "HPC_TR_Vol7_Economic_Yr22_301224.xls"
OC_VOL2 = "HPC_TR_Vol2_Demography_Yr22.xlsx"
OC_OSM_URL = "https://download.openstreetmap.fr/extracts/africa/mauritius-latest.osm.pbf"


def oc_path(key):
    """Path of a census input named in config, under the data root."""
    return "{data}/" + OC_INPUTS[key]


rule transport_open_census_download_vol7:
    """
    Download the 2022 census Volume VII (Economic Characteristics) workbook:
    tables CA1, CA27 and Appendices II and IV.

    Test with:
    snakemake -c1 data/incoming/Socio-economic/Census/2022/HPC_TR_Vol7_Economic_Yr22_301224.xls
    """
    input:
        script=f"{OC_SCRIPTS}/transport_open_census_download.py",
    output:
        workbook=f"{OC_CENSUS}/{OC_VOL7}",
    shell:
        """
        python {input.script} --url {OC_CENSUS_URL}/{OC_VOL7} --kind xls --output {output.workbook}
        """


rule transport_open_census_download_vol2:
    """
    Download the 2022 census Volume II (Demography) workbook: tables D1 and D2.

    Test with:
    snakemake -c1 data/incoming/Socio-economic/Census/2022/HPC_TR_Vol2_Demography_Yr22.xlsx
    """
    input:
        script=f"{OC_SCRIPTS}/transport_open_census_download.py",
    output:
        workbook=f"{OC_CENSUS}/{OC_VOL2}",
    shell:
        """
        python {input.script} --url {OC_CENSUS_URL}/{OC_VOL2} --kind xlsx --output {output.workbook}
        """


rule transport_open_census_download_osm:
    """
    Download the OpenStreetMap extract of Mauritius (openstreetmap.fr; all of
    the Republic: Mauritius, Rodrigues, Agalega, St Brandon). It is the latest
    extract on the day: the download date and checksum are in the
    .source.json sidecar.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/OpenStreetMap/mauritius-latest.osm.pbf
    """
    input:
        script=f"{OC_SCRIPTS}/transport_open_census_download.py",
    output:
        pbf="{data}/incoming/Infrastructure/OpenStreetMap/mauritius-latest.osm.pbf",
    shell:
        """
        python {input.script} --url {OC_OSM_URL} --kind pbf --output {output.pbf}
        """


rule transport_open_census_tables:
    """
    Census tables from the workbooks: CA1 employed and D2 population by area
    (with the area codes), the area code list (Appendix II, and the Rodrigues
    regions of Appendix IV with synthetic codes 2101-2106), CA27 residence by
    workplace district, and children by area and age band (D2, with 10-14
    split into 10-11 and 12-14 by D1 single-year shares). All published
    totals are checked against the sums of their parts.

    Test with:
    snakemake -c1 data/incoming/Socio-economic/Census/2022/CA1-employment-by-area.csv
    """
    input:
        vol7=rules.transport_open_census_download_vol7.output.workbook,
        vol2=rules.transport_open_census_download_vol2.output.workbook,
        module="src/transport/open_census.py",
        script=f"{OC_SCRIPTS}/transport_open_census_tables.py",
    output:
        ca1=oc_path("census_table"),
        codes=oc_path("census_zone_codes"),
        ca27=oc_path("census_district_od"),
        children="{data}/" + config["transport_land"]["school"]["children_table"],
        matches=f"{OC_REVIEW}/ca1-code-matches.csv",
        validation=f"{OC_REVIEW}/tables-validation.json",
    log:
        review=f"{OC_REVIEW}/tables-review.csv",
    shell:
        """
        python {input.script} --vol7 {input.vol7} --vol2 {input.vol2} \
            --out-ca1 {output.ca1} --out-codes {output.codes} --out-ca27 {output.ca27} \
            --out-children {output.children} --out-matches {output.matches} \
            --out-validation {output.validation} --review {log.review}
        """


rule transport_open_census_boundaries:
    """
    Census area polygons from the OSM boundary relations (admin_level 8 and
    9), matched by name to the area codes within their district, with the
    OSM code tags as a cross-check. Checks overlaps, parents against their
    parts, and coverage of the island outlines. Areas OSM does not map (now
    Vacoas-Phoenix Wards 5 and 6 West) have no polygon unless
    open_census_approximate_residuals is true in config: then the unmapped
    part of the town is split between them by a straight line (flagged
    "approximate" in geometry_source).

    Test with:
    snakemake -c1 data/incoming/Socio-economic/Census/2022/census-areas.gpkg
    """
    input:
        pbf=rules.transport_open_census_download_osm.output.pbf,
        codes=rules.transport_open_census_tables.output.codes,
        ca1=rules.transport_open_census_tables.output.ca1,
        module="src/transport/open_census.py",
        script=f"{OC_SCRIPTS}/transport_open_census_boundaries.py",
    output:
        boundaries=oc_path("census_boundaries"),
        matches=f"{OC_REVIEW}/osm-code-matches.csv",
        summary=f"{OC_REVIEW}/boundaries-summary.json",
    log:
        review=f"{OC_REVIEW}/boundaries-review.csv",
    params:
        approximate=(
            "--approximate-residuals"
            if OC_INPUTS.get("open_census_approximate_residuals")
            else "--no-approximate-residuals"
        ),
    shell:
        """
        python {input.script} --pbf {input.pbf} --codes {input.codes} --ca1 {input.ca1} \
            --output {output.boundaries} --out-matches {output.matches} \
            --out-summary {output.summary} --review {log.review} {params.approximate}
        """
