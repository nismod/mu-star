import click
import geopandas
import osmium
import pandas


def remove_overlapping_features(aeroways, tag):
    """
    Remove geometries for a specific tag that overlap with other features or each other.

    Parameters:
        aeroways: GeoDataFrame with 'aeroway' column and geometries
        tag: String tag to filter and process (e.g., 'taxiway')

    Returns:
        GeoDataFrame with non-overlapping geometries for the given tag
    """
    # Get all features except the target tag and aerodrome
    non_target = aeroways[~aeroways["aeroway"].isin([tag, "aerodrome"])].copy()
    other_area = non_target.loc[:, "geometry"].union_all()

    # Get target features and subtract other areas
    target_features = aeroways[aeroways["aeroway"] == tag].copy()
    target_features.geometry = target_features.difference(other_area)

    # Remove internal overlaps
    target_geometries = target_features.geometry.reset_index(drop=True)
    target_sindex = target_geometries.sindex
    non_overlapping_positions = []
    non_overlapping_geometries = []

    for position, geometry in enumerate(target_geometries.array):
        if geometry.is_empty:
            continue

        overlapping_positions = target_sindex.query(geometry, predicate="intersects")
        overlapping_positions = overlapping_positions[overlapping_positions < position]
        if len(overlapping_positions) > 0:
            geometry = geometry.difference(
                target_geometries.iloc[overlapping_positions].union_all()
            )
            if geometry.is_empty:
                continue

        non_overlapping_positions.append(position)
        non_overlapping_geometries.append(geometry)

    target_features = target_features.iloc[non_overlapping_positions].copy()
    target_features.geometry = geopandas.GeoSeries(
        non_overlapping_geometries, index=target_features.index, crs=target_features.crs
    )

    return target_features


def process_overlapping_features(aeroways, tag):
    """
    Process a specific aeroway tag by removing overlaps and reintegrating with other features.

    Parameters:
        aeroways: GeoDataFrame with 'aeroway' column and geometries
        tag: String tag to process (e.g., 'taxiway', 'apron')

    Returns:
        GeoDataFrame with processed features
    """
    sites = aeroways[aeroways["aeroway"] == "aerodrome"].copy()
    non_target = aeroways[~aeroways["aeroway"].isin([tag, "aerodrome"])].copy()
    target_features = remove_overlapping_features(aeroways, tag)

    if not target_features.empty:
        aeroways = pandas.concat(
            [sites, non_target, target_features], ignore_index=True
        )

    return aeroways


@click.command()
@click.option(
    "--input",
    "input_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=str),
)
@click.option(
    "--output",
    "output_file",
    required=True,
    type=click.Path(dir_okay=False, writable=True, path_type=str),
)
def main(input_file, output_file):
    line_handler = AerowayWayHandler({"runway", "stopway", "taxiway"})
    line_handler.apply_file(input_file, locations=True)
    line_gdf = geopandas.GeoDataFrame(
        {
            "osm_id": line_handler.ids,
            "aeroway": line_handler.aeroways,
        },
        geometry=geopandas.GeoSeries.from_wkb(line_handler.wkbs, crs="EPSG:4326"),
    )

    line_gdf = line_gdf.to_crs("EPSG:32740")
    line_gdf.geometry = line_gdf.geometry.buffer(20)

    polygon_handler = AerowayAreaHandler({"aerodrome", "apron", "hangar", "terminal"})
    polygon_handler.apply_file(input_file, locations=True)
    polygon_gdf = geopandas.GeoDataFrame(
        {
            "osm_id": polygon_handler.ids,
            "aeroway": polygon_handler.aeroways,
        },
        geometry=geopandas.GeoSeries.from_wkb(polygon_handler.wkbs, crs="EPSG:4326"),
    )
    polygon_gdf = polygon_gdf.to_crs("EPSG:32740")

    aeroways = pandas.concat([polygon_gdf, line_gdf], ignore_index=True)

    aeroways = process_overlapping_features(aeroways, "apron")
    aeroways = process_overlapping_features(aeroways, "taxiway")

    aeroways.to_parquet(output_file)


class AerowayWayHandler(osmium.SimpleHandler):
    def __init__(self, tag_aeroway):
        super().__init__()
        self.wkb_factory = osmium.geom.WKBFactory()
        self.ids = []
        self.names = []
        self.ref = []
        self.icao = []
        self.iata = []
        self.building = []
        self.aeroways = []
        self.wkbs = []
        self.tag_aeroway = tag_aeroway

    def way(self, obj):
        aeroway = obj.tags.get("aeroway")
        if aeroway not in self.tag_aeroway:
            return

        try:
            wkb = self.wkb_factory.create_linestring(obj)
        except osmium.InvalidLocationError:
            return

        self.ids.append(obj.id)
        self.names.append(obj.tags.get("name"))
        self.ref.append(obj.tags.get("ref"))
        self.iata.append(obj.tags.get("iata"))
        self.icao.append(obj.tags.get("icao"))
        self.building.append(obj.tags.get("building"))
        self.aeroways.append(aeroway)
        self.wkbs.append(wkb)


class AerowayAreaHandler(osmium.SimpleHandler):
    def __init__(self, tag_aeroway):
        super().__init__()
        self.wkb_factory = osmium.geom.WKBFactory()
        self.ids = []
        self.names = []
        self.ref = []
        self.icao = []
        self.iata = []
        self.building = []
        self.aeroways = []
        self.wkbs = []
        self.tag_aeroway = tag_aeroway

    def area(self, obj):
        aeroway = obj.tags.get("aeroway")
        if aeroway not in self.tag_aeroway:
            return

        try:
            wkb = self.wkb_factory.create_multipolygon(obj)
        except (osmium.InvalidLocationError, RuntimeError):
            return

        self.ids.append(obj.orig_id())
        self.names.append(obj.tags.get("name"))
        self.ref.append(obj.tags.get("ref"))
        self.iata.append(obj.tags.get("iata"))
        self.icao.append(obj.tags.get("icao"))
        self.building.append(obj.tags.get("building"))
        self.aeroways.append(aeroway)
        self.wkbs.append(wkb)


if __name__ == "__main__":
    main()
