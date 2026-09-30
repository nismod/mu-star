rule collate_jba_flooding:
    """
    Normalise file naming for, and cloud optimise JBA flooding GeoTIFFs.

    Test with:
    snakemake -c1 data/visualise/tileserver/raster/data/jba-fluvial
    """
    input:
        fluvial = "{data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps",
        pluvial = "{data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps",
        coastal = "{data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps",
    output:
        fluvial = "{data}/visualise/tileserver/raster/data/jba-fluvial",
        pluvial = "{data}/visualise/tileserver/raster/data/jba-pluvial",
        coastal = "{data}/visualise/tileserver/raster/data/jba-coastal",
    shell:
        """
        OUT_ROOT="{wildcards.data}/visualise/tileserver/raster/data"
        NPROC={threads}
        INPUT_DIRS=(
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/FLRF"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/FLRF_GWL2"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/FLRF_GWL3"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/FLRF_GWL4"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLRF - River Flood Maps/FLRF_GWL5"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/FLSW"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/FLSW_GWL2"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/FLSW_GWL3"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/FLSW_GWL4"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/FLSW - Surface Water Flood Maps/FLSW_GWL5"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps/STSU"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps/STSU_GMSLR10cm"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps/STSU_GMSLR20cm"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps/STSU_GMSLR40cm"
            "{wildcards.data}/incoming/Hazards/JBA Flood Hazard/STSU - Coastal Flood Maps/STSU_GMSLR80cm"
        )

        # Which shared output folder each hazard type collapses into
        declare -A CLASS_OUT_DIR=(
        [FLRF]="jba-fluvial"
        [FLSW]="jba-pluvial"
        [STSU]="jba-coastal"
        )

        for IN_DIR in "${{INPUT_DIRS[@]}}"; do
        BASENAME="$(basename "$IN_DIR")"

        # Identify the hazard class (FLRF / FLSW / STSU) from the folder name
        CLASS=""
        for c in "${{!CLASS_OUT_DIR[@]}}"; do
            if [[ "$BASENAME" == "$c"* ]]; then
            CLASS="$c"
            break
            fi
        done
        if [[ -z "$CLASS" ]]; then
            echo "Could not determine hazard class for '$BASENAME', quitting" >&2
            exit 1
        fi

        # Work out whether this is a "baseline" folder (no _GWL / _GMSLR suffix)
        # and, if so, which token needs to be inserted into filenames.
        TOKEN=""
        if [[ "$BASENAME" != *_GWL* && "$BASENAME" != *_GMSLR* ]]; then
            case "$CLASS" in
            FLRF|FLSW) TOKEN="GWL0.75" ;;
            STSU)      TOKEN="GMSLR0cm" ;;
            esac
        fi

        OUT_DIR="${{OUT_ROOT}}/${{CLASS_OUT_DIR[$CLASS]}}"
        mkdir -p "$OUT_DIR"

        echo ">>> Optimizing: $IN_DIR"
        echo "    -> $OUT_DIR"
        terracotta optimize-rasters "$IN_DIR/*.tif" -o "$OUT_DIR" --nproc "$NPROC"

        # For baseline folders, rename the files we just produced to include the
        # token, e.g. MU_202604_FLRF_U_RP100_RD_30m_4326.tif
        #          -> MU_202604_FLRF_U_GWL0.75_RP100_RD_30m_4326.tif
        # We key off the *source* filenames (not a glob over OUT_DIR) since OUT_DIR
        # is now shared across several input folders and may already contain other
        # already-renamed files from previous runs.
        if [[ -n "$TOKEN" ]]; then
            while IFS= read -r -d '' src; do
            fname="$(basename "$src")"
            new_name="$(sed "s/_RP/_${{TOKEN}}_RP/" <<< "$fname")"
            if [[ "$new_name" != "$fname" && -e "${{OUT_DIR}}/${{fname}}" ]]; then
                mv -- "${{OUT_DIR}}/${{fname}}" "${{OUT_DIR}}/${{new_name}}"
            fi
            done < <(find "$IN_DIR" -maxdepth 1 -type f -print0)
        fi
        done
        """
