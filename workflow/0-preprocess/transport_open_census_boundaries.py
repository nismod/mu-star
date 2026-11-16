"""Census area boundaries (2022) from OpenStreetMap, with the 4-digit census codes.

OSM has the census areas as boundary=administrative relations: admin_level 8
(Village Council Areas, many split by district, e.g. "Plaines des Roches VCA,
North"; the towns as a whole; the six Rodrigues regions) and 9 (Municipal
Wards, e.g. "Town of Port-Louis, Ward 2, North"). The districts are
admin_level 4, and the islands are place=island multipolygons.

Method
    1. Assemble the polygons (pyosmium) and give each area polygon the district
       it overlaps most (Rodrigues regions: 21).
    2. Drop the parents and duplicates listed in transport.open_census.OSM_PARENTS
       (towns, a VCA mapped both whole and in parts). An unlisted polygon that
       contains two or more others is an error.
    3. Match names to the Appendix II-IV codes within the district
       (transport.open_census.match_names). The OSM `code` tags are a
       cross-check: a disagreement is an error unless listed in
       OSM_CODE_TAG_ERRATA.
    4. Where OSM maps an area that the census splits by district as a single
       polygon (no part in the name) and the other part has no polygon of its
       own, cut the polygon along the OSM district boundary.
    5. Nested polygons listed in KNOWN_NESTED (an area mapped whole, with
       one of its census parts inside it): the outer one minus the inner one.
       Any other overlap of more than 1% of the smaller polygon is an error.
    6. Residuals: each parent minus its mapped parts. A residual of more than
       0.05 km2 (and 0.5% of the parent) must be explained in KNOWN_MISSING_GEOMETRY (areas OSM does not
       map). With --approximate-residuals, the residual is split between its
       codes by a straight line (population shares, the part adjacent to its
       other district's part on that side), flagged geometry_source
       "approximate"; otherwise those codes have no polygon.
    7. Checks: overlaps between polygons, coverage of the island outlines.

Outputs
    GeoPackage (EPSG:32740), layers:
        census_areas (first, read by default): areas with a CA1 row: zone_id,
            zone_name, district_code, island_code, osm_id, osm_name,
            geometry_source, area_km2.
        untabulated_parts: polygons of coded areas with no CA1 row.
        residuals: unexplained or unmapped residual polygons, for review.
    matches CSV: every OSM area polygon and its code.
    summary JSON: counts, coverage, overlaps, missing geometry.
    review CSV (log): issues; the script fails if any.
"""

import json
from pathlib import Path

import click
import geopandas
import osmium
import pandas as pd
import shapely
import shapely.wkb

from transport import open_census as oc

CRS = "EPSG:32740"
MIN_PIECE_KM2 = 0.01
MAX_RESIDUAL_KM2 = 0.05
MAX_RESIDUAL_SHARE = 0.005  # of the parent: digitising slivers
MAX_OVERLAP_SHARE = 0.01  # of the smaller polygon


def read_osm(pbf: str) -> geopandas.GeoDataFrame:
    """Administrative boundaries (levels 3, 4, 8, 9) and island polygons."""
    factory = osmium.geom.WKBFactory()
    rows = []

    class Handler(osmium.SimpleHandler):
        def area(self, a):
            if a.from_way():
                return
            tags = dict(a.tags)
            admin = tags.get("boundary") == "administrative" and tags.get("admin_level") in ("3", "4", "8", "9")
            island = tags.get("place") == "island" and tags.get("name") in ("Ile Maurice", "Rodrigues")
            if not (admin or island):
                return
            geometry = shapely.wkb.loads(factory.create_multipolygon(a), hex=True)
            rows.append(
                {
                    "osm_id": a.orig_id(),
                    "kind": "island" if island and not admin else "admin",
                    "admin_level": tags.get("admin_level"),
                    "name": tags.get("name"),
                    "code_tag": tags.get("code") or tags.get("ref:doe"),
                    "geometry": geometry,
                }
            )

    Handler().apply_file(pbf, locations=True, idx="flex_mem")
    return geopandas.GeoDataFrame(rows, crs="EPSG:4326").to_crs(CRS)


@click.command()
@click.option("--pbf", required=True, help="OSM extract of Mauritius (.osm.pbf)")
@click.option("--codes", required=True, help="appendix-ii-iv-area-codes.csv")
@click.option("--ca1", required=True, help="CA1-employment-by-area.csv")
@click.option("--output", required=True, help="census-areas.gpkg")
@click.option("--out-matches", required=True)
@click.option("--out-summary", required=True)
@click.option("--review", required=True)
@click.option("--approximate-residuals/--no-approximate-residuals", default=False)
def main(pbf, codes, ca1, output, out_matches, out_summary, review, approximate_residuals):
    issues = []

    def issue(kind, item, detail):
        issues.append({"kind": kind, "item": str(item), "detail": detail})

    lookup = pd.read_csv(codes, dtype=str)
    table = pd.read_csv(ca1, dtype={"zone_id": str, "district_code": str})
    tabulated = set(table.loc[table["row_type"] == "area", "zone_id"].dropna())
    population = table.dropna(subset=["zone_id"]).set_index("zone_id")["population"]
    name_of = lookup.set_index("zone_id")["zone_name"]

    osm = read_osm(pbf)
    islands = {n: g for n, g in zip(osm.loc[osm.kind == "island", "name"], osm.loc[osm.kind == "island", "geometry"])}
    mauritius_outline, rodrigues_outline = islands["Ile Maurice"], islands["Rodrigues"]
    districts = osm[(osm.kind == "admin") & (osm.admin_level == "4")].copy()
    districts["district_code"] = districts["name"].map(oc.district_code)
    if districts["district_code"].isna().any() or sorted(districts["district_code"]) != sorted(oc.DISTRICTS):
        raise click.ClickException(f"OSM districts do not match the nine districts: {districts['name'].tolist()}")
    district_geom = dict(zip(districts["district_code"], districts["geometry"]))

    areas = osm[(osm.kind == "admin") & osm.admin_level.isin(["8", "9"])].set_index("osm_id")

    def district_of(geometry):
        if geometry.intersection(rodrigues_outline).area > 0.5 * geometry.area:
            return oc.RODRIGUES_DISTRICT_CODE
        overlaps = {code: geometry.intersection(g).area for code, g in district_geom.items()}
        best = max(overlaps, key=overlaps.get)
        return best if overlaps[best] > 0.5 * geometry.area else None

    areas["district_code"] = areas.geometry.map(district_of)
    for osm_id, r in areas[areas["district_code"].isna()].iterrows():
        issue("osm_no_district", osm_id, f"{r['name']}: less than half inside any district or Rodrigues")

    # --- Parents and duplicates -----------------------------------------------------
    parents = areas.loc[[i for i in oc.OSM_PARENTS if i in areas.index]]
    for i in set(oc.OSM_PARENTS) - set(areas.index):
        issue("stale_override", i, "OSM_PARENTS entry not in the extract")
    leaves = areas.drop(parents.index)
    joined = geopandas.sjoin(leaves[["geometry"]], leaves[["geometry"]], predicate="intersects")
    joined = joined[joined.index != joined["osm_id_right"]]
    contains = {}
    for a, b in zip(joined.index, joined["osm_id_right"]):
        inner = leaves.geometry[b]
        if leaves.geometry[a].intersection(inner).area > 0.9 * inner.area:
            contains.setdefault(a, []).append(b)
    for a, inner in contains.items():
        names = [leaves.loc[b, "name"] for b in inner]
        if len(inner) >= 2 or any(
            abs(leaves.geometry[a].area - leaves.geometry[b].area) < 0.01 * leaves.geometry[b].area for b in inner
        ):
            issue("osm_unlisted_parent", a, f"{leaves.loc[a, 'name']} contains {names}: add to OSM_PARENTS?")

    # --- Names to codes -----------------------------------------------------------------
    left = pd.DataFrame(
        {"name": leaves["name"].fillna(""), "district_code": leaves["district_code"]}, index=leaves.index
    )
    right = lookup.rename(columns={"zone_id": "id", "zone_name": "name"})
    overrides = {i: c for i, c in oc.OSM_CODE_OVERRIDES.items() if i in left.index}
    matches = left.join(oc.match_names(left, right, overrides=overrides))
    # Polygons that straddle a district line (OSM and census district lines
    # differ): retry an unmatched polygon in each other district it overlaps
    # by 10% or more; accept a single exact or relaxed match.
    for osm_id in matches.index[matches["match_id"].isna()]:
        geometry = leaves.geometry[osm_id]
        found = []
        for code, g in district_geom.items():
            if code == left.loc[osm_id, "district_code"] or geometry.intersection(g).area < 0.1 * geometry.area:
                continue
            retry = oc.match_names(left.loc[[osm_id]].assign(district_code=code), right)
            if retry["method"].iloc[0] in ("exact", "relaxed_part"):
                found.append((code, retry["match_id"].iloc[0], retry["method"].iloc[0]))
        if len(found) == 1:
            code, match_id, method = found[0]
            matches.loc[osm_id, ["district_code", "match_id", "method", "candidates"]] = [
                code,
                match_id,
                f"{method} (other district overlapped)",
                "",
            ]
    taken = matches["match_id"].dropna()
    for code in taken[taken.duplicated()].unique():
        ids = taken.index[taken == code].tolist()
        issue("osm_duplicate", code, f"code matched by several OSM polygons: {ids}")
    matches["code_tag"] = leaves["code_tag"]
    matches["zone_name"] = matches["match_id"].map(name_of)
    for osm_id, r in matches[matches["match_id"].isna()].iterrows():
        issue(
            f"osm_{r['method']}", osm_id, f"{r['name']} (district {r['district_code']}); candidates {r['candidates']}"
        )
    for osm_id, r in matches.dropna(subset=["match_id"]).iterrows():
        tag = r["code_tag"]
        if pd.isna(tag) or tag == r["match_id"]:
            if osm_id in oc.OSM_CODE_TAG_ERRATA:
                issue("stale_override", osm_id, "OSM_CODE_TAG_ERRATA entry: the tag is now absent or correct")
            continue
        if oc.OSM_CODE_TAG_ERRATA.get(osm_id) != r["match_id"]:
            issue("osm_code_tag", osm_id, f"{r['name']}: tagged {tag}, matched {r['match_id']} {r['zone_name']!r}")
    matches["code_tag_status"] = [
        "absent"
        if pd.isna(t)
        else "agrees"
        if t == m
        else "known erratum"
        if oc.OSM_CODE_TAG_ERRATA.get(i) == m
        else "disagrees"
        for i, t, m in zip(matches.index, matches["code_tag"], matches["match_id"])
    ]
    matches.to_csv(out_matches, index_label="osm_id")

    # --- Polygons per code, with district splits ----------------------------------------------
    pieces = {}  # code -> dict(geometry, osm_id, osm_name, source)
    for osm_id, r in matches.dropna(subset=["match_id"]).iterrows():
        pieces[r["match_id"]] = {
            "geometry": leaves.geometry[osm_id],
            "osm_id": str(osm_id),
            "osm_name": r["name"],
            "geometry_source": "osm",
        }
    parsed = {c: oc.parse_area_name(n) for c, n in name_of.items()}
    for code, piece in list(pieces.items()):
        if oc.parse_area_name(piece["osm_name"]).part is not None:
            continue
        siblings = [
            c
            for c, p in parsed.items()
            if c != code and p.key[:2] == parsed[code].key[:2] and c not in pieces and c[:2] in district_geom
        ]
        for sibling in siblings:
            cut = piece["geometry"].intersection(district_geom[sibling[:2]])
            if cut.area / 1e6 > MIN_PIECE_KM2 and cut.area > 0.01 * piece["geometry"].area:
                piece["geometry"] = piece["geometry"].difference(district_geom[sibling[:2]])
                piece["geometry_source"] = "osm, cut along the OSM district boundary"
                pieces[sibling] = {
                    "geometry": cut,
                    "osm_id": piece["osm_id"],
                    "osm_name": piece["osm_name"],
                    "geometry_source": "osm, cut along the OSM district boundary",
                }

    # --- Nested polygons: OSM maps some areas whole, with a part inside them ----------------
    nested_resolved = []
    for outer, inner in oc.KNOWN_NESTED:
        if outer not in pieces or inner not in pieces:
            issue("stale_override", f"{outer}/{inner}", "KNOWN_NESTED pair without both polygons")
            continue
        a, b = pieces[outer]["geometry"], pieces[inner]["geometry"]
        share = a.intersection(b).area / b.area
        if share < 0.95:
            issue("stale_override", f"{outer}/{inner}", f"KNOWN_NESTED: only {share:.1%} of {inner} lies in {outer}")
            continue
        pieces[outer]["geometry"] = a.difference(b)
        pieces[outer]["geometry_source"] += f", minus nested {inner}"
        nested_resolved.append({"outer": outer, "inner": inner, "removed_km2": round(b.area / 1e6, 4)})

    # --- Residuals: parents minus their mapped parts ------------------------------------------
    mapped = shapely.union_all([p["geometry"] for p in pieces.values()])
    residual_rows = []
    missing_by_parent = {}
    for code, (parent, reason) in oc.KNOWN_MISSING_GEOMETRY.items():
        missing_by_parent.setdefault(parent, []).append(code)
    residual_summary = {}
    for osm_id, r in parents.iterrows():
        residual = shapely.make_valid(r.geometry.difference(mapped))
        km2 = residual.area / 1e6
        residual_summary[r["name"]] = round(km2, 4)
        codes_here = [c for c in missing_by_parent.get(osm_id, []) if c not in pieces]
        if (km2 <= MAX_RESIDUAL_KM2 or km2 <= MAX_RESIDUAL_SHARE * r.geometry.area / 1e6) and not codes_here:
            continue
        if not codes_here:
            issue("osm_residual", osm_id, f"{r['name']} minus its parts leaves {km2:.3f} km2 unexplained")
        residual_rows.append(
            {
                "parent_osm_id": osm_id,
                "parent": r["name"],
                "codes": "+".join(codes_here),
                "area_km2": km2,
                "geometry": residual,
            }
        )
        if approximate_residuals and codes_here:
            if len(codes_here) != 2:
                raise click.ClickException(f"Approximate split is implemented for two codes, not {codes_here}")
            # The code whose other-district part is mapped lies next to it.
            near = [
                c
                for c in codes_here
                if any(o != c and parsed[o].key[:2] == parsed[c].key[:2] and o in pieces for o in parsed)
            ]
            if len(near) != 1:
                raise click.ClickException(f"Cannot orient the split of {codes_here}")
            near_code = near[0]
            far_code = next(c for c in codes_here if c != near_code)
            anchor_code = next(
                o for o in parsed if o != near_code and parsed[o].key[:2] == parsed[near_code].key[:2] and o in pieces
            )
            share = population[near_code] / (population[near_code] + population[far_code])
            polygon = (
                max(getattr(residual, "geoms", [residual]), key=lambda g: g.area)
                if residual.geom_type != "Polygon"
                else residual
            )
            near_geom, far_geom = oc.split_by_line(polygon, pieces[anchor_code]["geometry"].centroid, share)
            for code, geom in ((near_code, near_geom), (far_code, far_geom)):
                pieces[code] = {
                    "geometry": geom,
                    "osm_id": str(osm_id),
                    "osm_name": f"{r['name']} minus its mapped wards",
                    "geometry_source": (
                        "approximate: OSM residual split by a straight line, "
                        f"area share by population ({share:.3f} to {near_code})"
                    ),
                }

    # --- Assemble and check -----------------------------------------------------------------
    records = [
        {
            "zone_id": code,
            "zone_name": name_of.get(code),
            "district_code": code[:2],
            "island_code": code[0],
            **piece,
            "area_km2": piece["geometry"].area / 1e6,
        }
        for code, piece in sorted(pieces.items())
    ]
    allgeo = geopandas.GeoDataFrame(records, crs=CRS)
    allgeo["geometry"] = allgeo.geometry.map(lambda g: shapely.make_valid(shapely.normalize(g)))
    main_layer = allgeo[allgeo["zone_id"].isin(tabulated)]
    untabulated = allgeo[~allgeo["zone_id"].isin(tabulated)]
    for code in untabulated["zone_id"]:
        if code not in oc.CODES_WITHOUT_CA1_ROW:
            issue("polygon_without_ca1_row", code, f"{name_of.get(code)} has a polygon but no CA1 row")
    missing = sorted(tabulated - set(main_layer["zone_id"]))
    for code in missing:
        if code not in oc.KNOWN_MISSING_GEOMETRY:
            issue("ca1_area_without_polygon", code, name_of.get(code))
    no_polygon_codes = sorted(set(lookup["zone_id"]) - set(allgeo["zone_id"]))

    pairs = geopandas.sjoin(allgeo[["zone_id", "geometry"]], allgeo[["zone_id", "geometry"]], predicate="intersects")
    pairs = pairs[pairs["zone_id_left"] < pairs["zone_id_right"]]
    geom = allgeo.set_index("zone_id").geometry
    overlaps = []
    for a, b in zip(pairs["zone_id_left"], pairs["zone_id_right"]):
        area = geom[a].intersection(geom[b]).area
        if area > 1000:  # m2
            share = area / min(geom[a].area, geom[b].area)
            overlaps.append({"a": a, "b": b, "overlap_km2": round(area / 1e6, 4), "share_of_smaller": round(share, 4)})
            if share > MAX_OVERLAP_SHARE:
                issue("overlap", f"{a}/{b}", f"{area / 1e6:.3f} km2 ({share:.1%} of the smaller polygon)")
    union = shapely.union_all(allgeo.geometry.to_numpy())

    def coverage(outline):
        return {
            "outline_km2": round(outline.area / 1e6, 2),
            "covered_share": round(union.intersection(outline).area / outline.area, 5),
        }

    summary = {
        "osm_area_polygons": len(areas),
        "osm_parents_dropped": {str(k): v for k, v in oc.OSM_PARENTS.items()},
        "match_methods": matches["method"].value_counts().to_dict(),
        "fuzzy_matches": matches[matches["method"] == "fuzzy"][["name", "match_id", "zone_name", "score"]].to_dict(
            "records"
        ),
        "code_tags": matches["code_tag_status"].value_counts().to_dict(),
        "polygons": {
            "census_areas_layer": len(main_layer),
            "untabulated_parts_layer": len(untabulated),
            "by_source": allgeo["geometry_source"].value_counts().to_dict(),
            "ca1_areas": len(tabulated),
            "ca1_areas_without_polygon": {c: name_of.get(c) for c in missing},
            "codes_without_polygon": {c: name_of.get(c) for c in no_polygon_codes},
        },
        "nested_polygons_resolved": nested_resolved,
        "residual_km2_by_parent": residual_summary,
        "overlaps_over_1000_m2": overlaps,
        "overlap_total_km2": round(sum(o["overlap_km2"] for o in overlaps), 4),
        "coverage": {
            "island_of_mauritius_outline": coverage(mauritius_outline),
            "mauritius_osm_districts": coverage(shapely.union_all(districts.geometry.to_numpy())),
            "rodrigues_outline": coverage(rodrigues_outline),
            "area_outside_island_outlines_km2": round(
                union.difference(shapely.union_all([mauritius_outline, rodrigues_outline])).area / 1e6, 3
            ),
        },
        "approximate_residuals": approximate_residuals,
        "issues": len(issues),
    }
    with open(out_summary, "w") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False, default=str)

    out = Path(output)
    if out.exists():
        out.unlink()
    main_layer.to_file(out, layer="census_areas", driver="GPKG")
    if len(untabulated):
        untabulated.to_file(out, layer="untabulated_parts", driver="GPKG")
    if residual_rows:
        geopandas.GeoDataFrame(residual_rows, crs=CRS).to_file(out, layer="residuals", driver="GPKG")

    pd.DataFrame(issues, columns=["kind", "item", "detail"]).to_csv(review, index=False)
    if issues:
        raise click.ClickException(f"{len(issues)} issues to review in {review}")


if __name__ == "__main__":
    main()
