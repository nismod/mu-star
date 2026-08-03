
rule download_wind_atlas:
    paths = ["/api/gdal/country/geojson?areaId=MUS"] + \
        expand(
            "/api/gis/country/MUS/{var}/{height}",
            height=[10, 50, 100, 150, 200],
            var=[
                "wind-speed",
                "power-density",
                "air-density",
                "combined-Weibull-A",
                "combined-Weibull-K",
            ]
        ) +
        expand(
            "/api/gis/country/MUS/{var}/",
            var=[
                "capacity-factor_IEC1",
                "capacity-factor_IEC2",
                "capacity-factor_IEC3",
                "IEC-class-fatigue-loads",
                "IEC-class-fatigue-loads-incl-wake",
                "IEC-class-extreme-loads",
            ]
        )

    base_url = "https://globalwindatlas.info"
