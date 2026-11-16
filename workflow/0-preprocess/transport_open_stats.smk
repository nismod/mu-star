"""Open Statistics Mauritius data for the land and inter-island transport workflows.

Downloads published Statistics Mauritius workbooks (original file names kept)
and extracts three small tables, in the schemas of docs/src/transport-data.md:

    National Accounts series (GVA by industry group, 2022) ─┐
    Census 2022 Vol VII, Table CA3 (employed by section) ───┴─> output per worker (item 9)
    Digest of Labour Statistics (comparison only) ──────────────┘
    Digest of Education Statistics 2025 ──────────────────────> enrolment by district and level (item 10 scaling)
    Digest of Tourism + Digest of Rodrigues Statistics ───────> published inter-island traffic (item 21 source table)

Each extraction script fails if a table does not look as expected or a total
check fails (census total employed, GVA total, district sums). The reference
years and sources are fixed by the constants and rule parameters below.

The digests below are the latest issues found on
2026-11-15; update the file names when newer issues are published.

Run all with:
    snakemake -c1 data/incoming/Socio-economic/GDP/output-per-worker-by-sector.csv \
        data/incoming/Education/enrolment-by-district-level.csv \
        data/incoming/Infrastructure/Transport/interisland/traffic-published.csv
"""

OS_SCRIPTS = "workflow/0-preprocess"
OS_STATS = "{data}/incoming/Socio-economic/StatisticsMauritius"
OS_BASE = "https://statsmauritius.govmu.org"

# name of the downloaded file -> URL (the file name is the published one)
OS_DOWNLOADS = {
    # 2022 census, Volume VII (Economic): CA3 = employed by geographical district and industry section
    "HPC_TR_Vol7_Economic_Yr22_301224.xls": f"{OS_BASE}/Documents/Census_and_Surveys/Census2022/HPC_TR_Vol7_Economic_Yr22_301224.xls",
    # National Accounts historical series 2013-2026 (Oct 2026, 2023 CEA base): Table 3 = GVA by industry group
    "HS_NA_221026.xlsx": f"{OS_BASE}/Documents/Statistics/By_Subject/National_Accounts/HS_NA_221026.xlsx",
    # Digest of National Accounts 2025 (Jun 2026, 2018 CEA base): Table 4, an older basis (comparison)
    "Digest_NA_Yr2025_260626.xlsx": f"{OS_BASE}/Documents/Statistics/Digests/National_Accounts/Digest_NA_Yr2025_260626.xlsx",
    # Digest of Labour Statistics 2025: Table 1.4 employment by industry (CMPHS + SEE), 2023-2025
    "Digest_Labour_Yr25_280926.xlsx": f"{OS_BASE}/Documents/Statistics/Digests/Labour/Digest_Labour_Yr25_280926.xlsx",
    # Digest of Education Statistics 2025: Tables 120, 122, 132, 140
    "Digest_Edu_Yr2025_150426.xlsx": f"{OS_BASE}/Documents/Statistics/Digests/Education/Digest_Edu_Yr2025_150426.xlsx",
    # Digest of International Travel and Tourism Statistics 2025: Tables 2a and 2b
    "Digest_Tourism_Yr25_290926.xlsx": f"{OS_BASE}/Documents/Statistics/Digests/Tourism/Digest_Tourism_Yr25_290926.xlsx",
    # Digest of Rodrigues Statistics 2025: Tables 20, 22, 30
    "Digest_Rod_Yr25_040926.xlsx": f"{OS_BASE}/Documents/Statistics/Digests/Rodrigues/Digest_Rod_Yr25_040926.xlsx",
}

OS_GVA_YEAR = 2022  # census year; GVA is for the calendar year
OS_CENSUS_EMPLOYED_TOTAL = 538516  # Republic of Mauritius, both sexes (Table CA1)
OS_EDUCATION_YEAR = 2025
OS_LABOUR_YEAR = 2023  # first year of Digest of Labour Statistics Table 1.4 is 2023

OS_OUT_GDP = "{data}/incoming/Socio-economic/GDP"
OS_OUT_EDU = "{data}/incoming/Education"
OS_OUT_IIT = "{data}/incoming/Infrastructure/Transport/interisland"


wildcard_constraints:
    stats_file="|".join(k.replace(".", r"\.") for k in OS_DOWNLOADS),


rule transport_open_stats_download:
    """
    Download one Statistics Mauritius workbook, keeping its published name.
    Polite: waits a second before the request and fails on any HTTP error.

    Test with:
    snakemake -c1 data/incoming/Socio-economic/StatisticsMauritius/HS_NA_221026.xlsx
    """
    output:
        workbook=f"{OS_STATS}/{{stats_file}}",
    params:
        url=lambda wc: OS_DOWNLOADS[wc.stats_file],
    shell:
        """
        sleep 1
        curl --fail --silent --show-error --location --retry 2 --retry-delay 5 --max-time 300 \
            --user-agent "mu-star open-data download" --output {output.workbook} "{params.url}"
        """


rule transport_open_stats_output_per_worker:
    """
    Output per worker by ISIC section (data item 9): GVA at current basic
    prices by industry group, 2022, divided by the 2022 census employed
    population by industry section (CA3). Also writes
    output-per-worker-by-sector-reconciliation.csv (unmatched employment,
    labour survey comparison, totals).

    Test with:
    snakemake -c1 data/incoming/Socio-economic/GDP/output-per-worker-by-sector.csv
    """
    input:
        gva=f"{OS_STATS}/HS_NA_221026.xlsx",
        census=f"{OS_STATS}/HPC_TR_Vol7_Economic_Yr22_301224.xls",
        labour=f"{OS_STATS}/Digest_Labour_Yr25_280926.xlsx",
        script=f"{OS_SCRIPTS}/transport_open_stats_output_per_worker.py",
    output:
        table=f"{OS_OUT_GDP}/output-per-worker-by-sector.csv",
        reconciliation=f"{OS_OUT_GDP}/output-per-worker-by-sector-reconciliation.csv",
    params:
        year=OS_GVA_YEAR,
        total=OS_CENSUS_EMPLOYED_TOTAL,
        labour_year=OS_LABOUR_YEAR,
        source_gva=(
            "Statistics Mauritius, National Accounts 2013-2026 historical series (HS_NA_221026.xlsx), "
            "Table 3, GVA by industry group at current basic prices, 2022, MUR million"
        ),
        source_employment=(
            "Statistics Mauritius, 2022 Housing and Population Census, Vol VII Economic characteristics "
            "(HPC_TR_Vol7_Economic_Yr22_301224.xls), Table CA3, Republic of Mauritius, both sexes"
        ),
    shell:
        """
        python {input.script} --gva-xlsx {input.gva} --gva-sheet "Table 3" \
            --census-xls {input.census} --census-sheet CA3 \
            --labour-xlsx {input.labour} --labour-sheet "Table 1.4" --labour-year {params.labour_year} \
            --year {params.year} --expected-total {params.total} \
            --source-gva "{params.source_gva}" --source-employment "{params.source_employment}" \
            --output {output.table}
        """


rule transport_open_stats_enrolment:
    """
    Primary and secondary schools and enrolment by district (nine districts of
    the Island of Mauritius; Rodrigues as a whole), 2025, to scale placeholder
    school capacities. The digest publishes no school-by-school list.

    Test with:
    snakemake -c1 data/incoming/Education/enrolment-by-district-level.csv
    """
    input:
        digest=f"{OS_STATS}/Digest_Edu_Yr2025_150426.xlsx",
        script=f"{OS_SCRIPTS}/transport_open_stats_enrolment.py",
    output:
        table=f"{OS_OUT_EDU}/enrolment-by-district-level.csv",
    params:
        year=OS_EDUCATION_YEAR,
    shell:
        """
        python {input.script} --digest {input.digest} --year {params.year} \
            --source-name "Statistics Mauritius, Digest of Education Statistics 2025 (Digest_Edu_Yr2025_150426.xlsx)" \
            --output {output.table}
        """


rule transport_open_stats_traffic:
    """
    Published passenger and freight movements between Mauritius and Rodrigues
    (annual; arrivals in and departures from Rodrigues; all passengers; freight
    exported from Rodrigues in tonnes by commodity). Nothing is published for
    Agalega or St Brandon, nor for freight from Mauritius to Rodrigues. A
    source table for a later step: not keyed by link_id.

    Test with:
    snakemake -c1 data/incoming/Infrastructure/Transport/interisland/traffic-published.csv
    """
    input:
        tourism=f"{OS_STATS}/Digest_Tourism_Yr25_290926.xlsx",
        rodrigues=f"{OS_STATS}/Digest_Rod_Yr25_040926.xlsx",
        script=f"{OS_SCRIPTS}/transport_open_stats_traffic.py",
    output:
        table=f"{OS_OUT_IIT}/traffic-published.csv",
    shell:
        """
        python {input.script} --tourism {input.tourism} --rodrigues {input.rodrigues} \
            --tourism-source "Statistics Mauritius, Digest of International Travel and Tourism Statistics 2025 (Digest_Tourism_Yr25_290926.xlsx)" \
            --rodrigues-source "Statistics Mauritius, Digest of Rodrigues Statistics 2025 (Digest_Rod_Yr25_040926.xlsx)" \
            --output {output.table}
        """
