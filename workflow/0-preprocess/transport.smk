
rule download_osm:
    output:
        gpkg = "{data}/incoming/Infrastructure/OpenStreetMap/mauritius-260622.gpkg",
        pbf = "{data}/incoming/Infrastructure/OpenStreetMap/mauritius-260622.osm.pbf",
    shell:
        """
        target_dir=$(dirname {output.gpkg})
        pushd $target_dir
            wget https://download.geofabrik.de/africa/mauritius-latest.osm.pbf \
                --no-clobber \
                --content-disposition

            wget https://download.geofabrik.de/africa/mauritius-latest-free.gpkg.zip \
                --no-clobber \
                --content-disposition

            unzip -o mauritius-260622-free.gpkg.zip
            mv mauritius.gpkg mauritius-260622.gpkg
        popd
        """

rule prepare_airport_areas:
    input:
        pbf = "{data}/incoming/Infrastructure/OpenStreetMap/mauritius-260622.osm.pbf",
        script = "workflow/0-preprocess/transport_prepare_airport_areas.py"
    output:
        areas = "{data}/processed/networks/transport/airport-areas.geoparquet",
    shell:
        """
        python {input.script} \
            --input {input.pbf} \
            --output {output.areas}
        """
