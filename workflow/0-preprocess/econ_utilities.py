"""Access to utilities
Maps with main econ activity and relation to utilities:

- Water
- Transport
- Energy (?)
"""

FULL_PLOTS: bool = True


def econ_utilities(
    wtp_path,
    wwtp_path,
    roads_path,
    nodes_path,
    edges_path,
    main_roads,
    voronoids_fig,
    econ_utilities_figs,
):
    from pathlib import Path

    import numpy as np
    import pandas as pd
    import geopandas as gpd
    from shapely import box, LineString
    import shapely as shp
    import osmnx as ox
    import logging

    import matplotlib.pyplot as plt
    import matplotlib.colors as matc
    from matplotlib_scalebar.scalebar import ScaleBar
    import contextily as ctx

    import ibis as ib
    import ibis.selectors as s
    from ibis import _

    import scalenav.oop as snoo

    import igraph as ig

    ib.options.interactive = True
    from globdata.design import _clean_sector_token
    import reverse_geocoder as rg

    from globdata import plotting, ISIC_CODES
    from globdata.parameters import load_catalogue, make_long_labels

    voronoids_fig = Path(voronoids_fig)
    econ_utilities_figs = Path(econ_utilities_figs)
    voronoids_fig.parent.mkdir(parents=True, exist_ok=True)
    econ_utilities_figs.mkdir(parents=True, exist_ok=True)

    CATALOGUE = load_catalogue(local=True, root="../")
    CATALOGUE
    conn = snoo.connect()
    ## Focus Area
    region_gid_0 = [
        "MUS",
    ]
    region = conn.read_parquet(CATALOGUE["custom_bounds"]).filter(
        _.gid_0.isin(region_gid_0),
    )

    region_bound = region.geometry.execute().set_crs("epsg:4326")[0]
    region_bound
    region_bbox = region.geometry.execute().total_bounds
    # limits = (57.0808,-20.6867,58.0698,-19.6648)
    limits = (57.0808, -20.6867, 58.0698, -19.8)
    xmin, ymin, xmax, ymax = limits
    region_small_box = box(*limits)

    def geocoder_xy(query: str):
        return ox.geocode(query=query)[::-1]

    gadm = (
        (
            conn.read_parquet(CATALOGUE["gadm_gid_0"])
            .alias("bla")
            .sql("""
            SELECT
                * EXCLUDE geometry_simple,
                st_geomfromwkb(geometry_simple) as geometry
            from bla""")
            .filter(
                _.gid_0.isin(["MUS"]),
            )
        )
        .execute()
        .set_crs("epsg:4326")
    )

    roads = conn.read_parquet(roads_path).select("id", "geometry").execute().set_crs("epsg:4326")

    ### WWTP & WTP

    _wtp_gdf = gpd.read_file(wtp_path)
    _wtp_gdf.columns = [var.lower() for var in _wtp_gdf.columns]

    _wwtp_gdf = gpd.read_file(wwtp_path)
    _wwtp_gdf = _wwtp_gdf.rename(
        columns={
            "WWTP Name": "name",
            "ID": "id",
        }
    )

    ### cppRosm data
    nodes = pd.read_csv(nodes_path).astype({"id": str})
    edges = pd.read_csv(edges_path).astype({"from": str, "to": str})

    ### Build igraph
    node_index = dict(zip(nodes["id"], range(len(nodes))))

    edge_list = [
        (node_index[src], node_index[tgt])
        for src, tgt in edges[["from", "to"]].itertuples(index=False, name=None)
        if src in node_index and tgt in node_index
    ]

    road_graph_raw = ig.Graph(
        n=len(nodes),
        edges=edge_list,
        directed=False,
    )

    road_graph_raw.vs["id"] = nodes["id"].astype(str).tolist()
    road_graph_raw.vs["lon"] = nodes["lon"].tolist()
    road_graph_raw.vs["lat"] = nodes["lat"].tolist()

    road_graph_raw.es["length"] = edges["length"].tolist()
    road_graph_raw.es["highway"] = edges["highway"].tolist()

    # largest connected component (weak for directed graphs)
    road_graph = road_graph_raw.components(mode="weak").giant()

    # optional: get original node ids if available
    largest_node_ids = road_graph.vs["id"] if "id" in road_graph.vertex_attributes() else None

    # simplify the graph
    road_graph.simplify(
        combine_edges={
            "highway": "concat",
            "length": "sum",
        }
    )

    # get the nodes and edges from the simple graph
    nodes_df = pd.DataFrame(
        {
            "id": road_graph.vs["id"],
            "lon": road_graph.vs["lon"],
            "lat": road_graph.vs["lat"],
        }
    ).astype({"id": str})

    nodes_gdf = gpd.GeoDataFrame(
        nodes_df,
        geometry=gpd.points_from_xy(
            nodes_df["lon"],
            nodes_df["lat"],
        ),
        crs="epsg:4326",
    )

    edges_df = pd.DataFrame(
        {
            "from": [road_graph.vs[src]["id"] for src, tgt in road_graph.get_edgelist()],
            "to": [road_graph.vs[tgt]["id"] for src, tgt in road_graph.get_edgelist()],
            # "length": road_graph.es["length"],
            "highway": road_graph.es["highway"],
        }
    ).astype({"from": str, "to": str})

    # main_roads = [
    #     "trunk",
    #     # "primary",
    #     # 'secondary',
    # ]

    def build_road_geometry(nodes, edges, crs: str = "epsg:4326") -> gpd.GeoDataFrame:
        nodes = nodes[~nodes.duplicated()]

        if not nodes.index.name == "id":
            nodes.set_index("id", inplace=True)

        # for row in edges.iterrows():
        #     edge_list.append(LineString([nodes.loc[row['from'],['lon','lat']].values.flatten(),nodes.loc[row['to'],['lon','lat']].values.flatten()]))

        def build_linestring(nodes, id_from, id_to):
            try:
                return LineString(
                    [
                        nodes.loc[id_from, ["lon", "lat"]].values.flatten(),
                        nodes.loc[id_to, ["lon", "lat"]].values.flatten(),
                    ]
                )
            except (KeyError, IndexError, TypeError) as e:
                # missing or malformed node -> skip this edge
                return None

        # edges['geometry'] = edges.apply(lambda row: LineString([nodes.loc[row['from'],['lon','lat']].values.flatten(),nodes.loc[row['to'],['lon','lat']].values.flatten()]),axis=1)
        edges["geometry"] = edges.apply(lambda row: build_linestring(nodes, row["from"], row["to"]), axis=1)

        # edges_geom = gpd.GeoDataFrame(edges, geometry=gpd.GeoSeries(edge_list,crs=crs))

        return gpd.GeoDataFrame(edges, geometry="geometry", crs=crs)

    edges_main = build_road_geometry(nodes_df, edges=edges_df[edges_df.highway.isin(main_roads)])

    ### Identifying the main nodes for each utilities type

    #### Roads
    # extract vertex ids (original node ids) for edges whose 'highway' is in main_roads
    main_edge_vertices = set()

    for e in road_graph.es:
        if e["highway"] in main_roads:
            u, v = e.tuple
            main_edge_vertices.add(str(road_graph.vs[u]["id"]))
            main_edge_vertices.add(str(road_graph.vs[v]["id"]))

    main_vertices = sorted(main_edge_vertices)

    main_nodes = nodes.loc[nodes.id.isin(main_vertices)]

    ### WTP & WWTP road connection
    wwtp_gdf = gpd.sjoin_nearest(
        _wwtp_gdf[["name", "geometry"]].to_crs("esri:54009"),
        nodes_gdf[["id", "geometry"]].to_crs("esri:54009"),
        how="left",
        distance_col="nearest_dist",
    ).to_crs("epsg:4326")

    wwtp_gdf = wwtp_gdf[~wwtp_gdf["name"].duplicated()]

    wtp_gdf = gpd.sjoin_nearest(
        _wtp_gdf[["name", "geometry"]].to_crs("esri:54009"),
        nodes_gdf[["id", "geometry"]].to_crs("esri:54009"),
        how="left",
        distance_col="nearest_dist",
    ).to_crs("epsg:4326")

    wtp_gdf = wtp_gdf[~wtp_gdf["name"].duplicated()]

    # road_graph.vcount()
    def graph_voronoi(road_graph, seed_vertex_idxs, name="voronoi_seed"):
        try:
            import heapq
        except Exception as e:
            logging.log(msg="Could not import dependency `heapq`.")

        weights = road_graph.es["length"]

        n = road_graph.vcount()
        dist = np.full(n, np.inf)
        owner = np.full(n, -1, dtype=int)

        pq = []
        for vidx in seed_vertex_idxs:
            dist[vidx] = 0.0
            owner[vidx] = vidx
            heapq.heappush(pq, (0.0, vidx, vidx))

        adj = [[] for _ in range(n)]
        for eid, edge in enumerate(road_graph.es):
            u, v = edge.tuple
            w = weights[eid]
            adj[u].append((v, w))
            adj[v].append((u, w))

        while pq:
            d, u, seed = heapq.heappop(pq)
            if d != dist[u]:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    owner[v] = seed
                    heapq.heappush(pq, (nd, v, seed))

        seed_ids = [str(road_graph.vs[idx]["id"]) if idx >= 0 else None for idx in owner]

        road_graph.vs[name] = seed_ids
        # road_graph.vs['voronoi_distance'] = dist.tolist()
        return road_graph

    # Road network main nodes voronoi
    # road_graph_voronoi
    # modify here to use the LCC if possible
    node_index_lcc = dict(zip(road_graph.vs["id"], range(len(road_graph.vs["id"]))))
    seed_vertex_idxs = [node_index_lcc[nid] for nid in main_vertices if nid in node_index_lcc]

    road_graph_voronoi = graph_voronoi(road_graph, seed_vertex_idxs, name="voronoi_seed")
    road_graph_voronoi.vs[:5]["voronoi_seed"]
    # WWTP road voronoi
    wwtp_seed_vertex_idxs = [node_index_lcc[nid] for nid in wwtp_gdf["id"] if nid in node_index_lcc]

    road_graph_voronoi = graph_voronoi(road_graph, wwtp_seed_vertex_idxs, name="wwtp_seed")
    road_graph_voronoi.vs[:5]["wwtp_seed"]
    # WWTP road voronoi
    wtp_seed_vertex_idxs = [node_index_lcc[nid] for nid in wtp_gdf["id"] if nid in node_index_lcc]

    road_graph_voronoi = graph_voronoi(road_graph, wtp_seed_vertex_idxs, name="wtp_seed")
    road_graph_voronoi.vs[:5]["wtp_seed"]
    road_graph_voronoi.summary()
    nodes_seed_gdf = gpd.GeoDataFrame(
        main_nodes,
        geometry=gpd.points_from_xy(
            main_nodes["lon"],
            main_nodes["lat"],
        ),
        crs="epsg:4326",
    )

    #  Convert road_graph_voronoi igraph object to a GeoDataFrame
    voronoi_gdf = (
        gpd.GeoDataFrame(
            {
                "node_id": road_graph_voronoi.vs["id"],
                "lon": road_graph_voronoi.vs["lon"],
                "lat": road_graph_voronoi.vs["lat"],
                "voronoi_seed": road_graph_voronoi.vs["voronoi_seed"],
                "wwtp_seed": road_graph_voronoi.vs["wwtp_seed"],
                "wtp_seed": road_graph_voronoi.vs["wtp_seed"],
                # 'voronoi_distance': road_graph_voronoi.vs['voronoi_distance'],
            },
            geometry=gpd.points_from_xy(road_graph_voronoi.vs["lon"], road_graph_voronoi.vs["lat"]),
            crs="EPSG:4326",
        )
        .fillna(
            {
                "voronoi_seed": -1,
                "wwtp_seed": -1,
                "wtp_seed": -1,
            }
        )
        .astype(
            {
                "voronoi_seed": str,
                "wwtp_seed": str,
                "wtp_seed": str,
                "node_id": str,
            }
        )
        # .merge(
        #     main_nodes,
        #     left_on='node_id',
        #     right_on='id',
        #     suffixes=("",'_seed')
        # )
    )

    voronoi_esri = voronoi_gdf.to_crs("ESRI:54009")

    region_small_box_esri = gpd.GeoSeries(region_small_box, crs="EPSG:4326").to_crs("ESRI:54009")

    limits_esri = region_small_box_esri.total_bounds

    bbox_x, bbox_y = (
        np.mean(limits[0::2]),
        np.mean(limits[1::2]),
    )

    dx = (
        gpd.GeoSeries(
            shp.Point(
                bbox_x + 0.5,
                bbox_y,
            ),
            crs="epsg:4326",
        )
        .to_crs("esri:54009")
        .distance(
            gpd.GeoSeries(
                shp.Point(
                    bbox_x - 0.5,
                    bbox_y,
                ),
                crs="epsg:4326",
            ).to_crs("esri:54009")
        )[0]
    )
    markersizes: float = 5
    color: str = "darkred"

    fig, axs = plt.subplots(
        ncols=3,
        nrows=1,
        figsize=(15, 5),
    )

    [(ax.set_xlim(limits[0::2]), ax.set_ylim(limits[1::2])) for ax in axs]
    [ax.set_axis_off() for ax in axs]

    voronoi_gdf.set_geometry("geometry").plot(
        ax=axs[0],
        column="voronoi_seed",
        cmap="Set2",
        s=0.1,
    )
    axs[0].set(title="Main road access")

    voronoi_gdf.set_geometry("geometry").plot(
        ax=axs[1],
        column="wwtp_seed",
        cmap="Set2",
        s=0.1,
    )
    # plotting.annotate_radially(
    #     ax=axs[1],
    #     annotations=wwtp_gdf.set_index('name')['geometry'],
    #     shift=.5,
    # )
    wwtp_gdf.plot(
        ax=axs[1],
        markersize=markersizes,
        color=color,
    )
    axs[1].set(title="Waste Water Treatment Plants")
    # push_text_free(fig, axs[1])

    voronoi_gdf.set_geometry("geometry").plot(
        ax=axs[2],
        column="wtp_seed",
        cmap="Set2",
        s=0.1,
    )
    # plotting.annotate_radially(
    #     ax=axs[2],
    #     annotations=wtp_gdf.set_index('name')['geometry'],
    #     shift=.5,
    # )
    wtp_gdf.plot(
        ax=axs[2],
        markersize=markersizes,
        color=color,
    )
    axs[2].set(title="Water Treatment Plants")
    # push_text_free(fig, axs[2])

    scalebar_ = ScaleBar(
        dx=dx,
        # label="",
        location="upper right",  # in relation to the whole plot
        # label_loc="left",
        scale_loc="bottom",  # in relation to the line
    )
    axs[2].add_artist(scalebar_)

    fig.suptitle("Catchment clusters for utilities")

    plt.tight_layout()
    plt.savefig(voronoids_fig, dpi=300)
    plt.close()

    ### Adding the econ outputs
    econ = (
        conn.read_parquet(
            "./data/incoming/Socio-economic/GDP/downscaling/macle_mauritius_h3_8_s_CDEFGHIJKLMNOPQRS/*/*_output.parquet",
            union_by_name=True,
        )
        .select(
            ~s.cols("geometry"),
        )
        .mutate(
            s.across(
                s.matches("osm_[A-U]_var"),
                lambda col: col.fill_null(0),
            )
        )
        .group_by(
            "h3_id",
        )
        .agg(
            s.across(
                s.matches("osm_[A-U]_var"),
                lambda col: col.sum(),
            )
        )
        .pipe(
            snoo.add_centr,
        )
    )
    econ.describe().execute().sample(5)
    econ.count(), econ.head()
    # pull econ ibis table into a GeoDataFrame (geom column expected)
    econ_gdf = econ.execute().set_crs("EPSG:4326", allow_override=True).set_geometry("geom")

    # find nearest node for each econ geometry
    econ_with_node_df = gpd.sjoin_nearest(
        econ_gdf.to_crs("esri:54009"),
        voronoi_gdf[["voronoi_seed", "wwtp_seed", "wtp_seed", "geometry"]].to_crs("esri:54009"),
        how="left",
        distance_col="nearest_dist",
    )

    econ_with_node_df.merge

    # (optional) register back to the connection as a new ibis table
    # (optional) register back to the connection as a new ibis table
    econ_with_node_ = conn.create_table("econ_with_node", obj=econ_with_node_df, overwrite=True)

    output_cols = set(
        econ_with_node_.select(
            s.matches("osm_[A-U]_var"),
        ).columns
    )

    econ_with_node = econ_with_node_.pipe(
        snoo.reduce,
        columns={
            "total_var": output_cols,
        },
        exclude=False,
    )

    ### Plotting Sector Access
    ### Features that need to be included for different sectors
    # education = ox.features_from_place("",tags={"amenity" : ["education", 'university']})
    # reverse_geocoded = ox.features_from_point(
    #     geocoder_xy('aeroport, Mauritius')[::-1],
    #     tags={
    #         "aeroway" : ['aerodrome'],
    #         "landuse" : ['industrial', 'commercial'],
    #         "place" : ["subdistrict", 'municipality','city']
    #     },
    #     dist=500,
    # )
    # reverse_geocoded
    # ox.features_from_place(query='Mauritius', tags=dict(amenity="university"))
    # MAP_FEATURES['Section B']
    # for row in pd.DataFrame(pd.Series(MAP_FEATURES['Section H'], name='coords')).iterrows():
    #     print(row)

    port = geocoder_xy("Port, Port-Louis, Mauritius")

    airport = geocoder_xy("airport, Mauritius")

    university = geocoder_xy("universite, Mauritius")
    MAP_FEATURES: dict[str, dict[str, shp.Geometry]] = {
        "Section C": {},
        "Section D": {},
        "Section E": {},
        "Section F": {},
        "Section G": {},
        "Section H": {
            "Airport": airport,
            "Port": port,
        },
        "Section I": {},
        "Section J": {},
        "Section K": {},
        "Section L": {},
        "Section M": {},
        "Section N": {},
        "Section O": {},
        "Section P": {
            "University": university,
        },
        "Section Q": {},
        "Section R": {},
        "Section S": {},
    }

    def geocode_coords(coords, suffix: bool = True):
        res = rg.search(coords)[0]
        # print(res)
        if suffix:
            return ", ".join([res["name"], res["cc"]])
        else:
            return res["name"]

    seed_vars = [
        "voronoi_seed",
        "wwtp_seed",
        "wtp_seed",
    ]

    seed_full_label = {
        "voronoi_seed": "Road network",
        "wwtp_seed": "Waste Water Treatment",
        "wtp_seed": "Waste Treatment",
    }
    plot_root = np.sqrt(len(output_cols))

    # nrow, ncol = int(np.floor(plot_root)), int(np.ceil(plot_root))

    seed_vars = [
        "voronoi_seed",
        "wwtp_seed",
        "wtp_seed",
    ]

    seed_full_label = {
        "voronoi_seed": "Road network",
        "wwtp_seed": "Waste Water Treatment",
        "wtp_seed": "Waste Treatment",
    }

    output_cols.add("total_var")

    output_cols_list = list(output_cols)

    for var in output_cols_list:
        fig, axs = plt.subplots(
            nrows=1,
            ncols=len(seed_vars),
            figsize=(
                5 * len(seed_vars),
                5,
            ),
        )

        if var == "total":
            fig.suptitle(var.capitalize())
        else:
            fig.suptitle("".join(make_long_labels([var], ISIC_CODES)))

        for seed_, ax in zip(seed_vars, axs):
            ax.set_xlim(limits[0::2])
            ax.set_ylim(limits[1::2])

            main_node_output_df = (
                econ_with_node.group_by(seed_)
                .agg(
                    s.across(output_cols_list, lambda col: col.sum()),
                )
                .select(
                    seed_,
                    *output_cols_list,
                )
            ).execute()  # .set_crs('EPSG:4326', allow_override=True)

            min_, max_ = 1, main_node_output_df[output_cols_list].max().max()

            main_node_output_df = gpd.GeoDataFrame(
                main_node_output_df.merge(nodes_gdf[["id", "geometry"]], left_on=seed_, right_on="id"),
                geometry="geometry",
            )

            output_norm = matc.LogNorm(
                vmin=min_,
                vmax=max_,
                clip=False,
            )

            section_title = f"Section {_clean_sector_token(var)}"

            sector_data = main_node_output_df[[var, seed_, "geometry"]].sort_values(
                by=var, ascending=True, inplace=False
            )

            values = sector_data[var].fillna(0).astype(float)

            sizes = (values.clip(lower=0)) / (np.max(values)) * 70 + 5

            gadm.plot(
                ax=ax,
                linewidth=0.7,
                facecolor="none",
            )

            if FULL_PLOTS:
                roads.plot(
                    ax=ax,
                    linewidth=0.1,
                    color="dimgray",
                    alpha=0.3,
                )

            if seed_ == "voronoi_seed":
                ax.legend()

                edges_main.plot(
                    ax=ax,
                    linewidth=0.7,
                    color="red",
                    linestyle="-",
                    label="Main Roads",
                )

                try:
                    map_features = pd.Series(MAP_FEATURES[section_title], name="coordinates")
                    plotting.annotate_radially(
                        ax,
                        map_features,
                        shift=0.7,  # 0 = centre, 1 = axes border
                        curvature=0.1,  # arrow curvature
                        text_color="black",  # inherit the active text color
                        arrow_color="0.35",  # neutral grey
                        text_size=9,  # inherit the active font size
                        line_width=0.7,  # arrow line width in points
                        arrowhead_size=7,  # Matplotlib mutation scale
                        arrowprops=None,  # optional advanced arrow overrides
                        # Additional Matplotlib text options can be added here:
                        # fontweight="normal",
                        # alpha=1.0,
                        backgroundcolor="white",
                    )
                    # print(map_features)

                except Exception as a:
                    pass

                settlements = sector_data.tail(3)["geometry"].copy()
                settlements.index = settlements.apply(lambda row: geocode_coords((row.y, row.x), suffix=False))
                settlements = settlements.loc[~settlements.index.duplicated(keep="first")]

                plotting.annotate_radially(
                    ax,
                    settlements,
                    shift=0.9,  # 0 = centre, 1 = axes border
                    curvature=0.03,  # arrow curvature
                    text_color="dimgray",  # inherit the active text color
                    arrow_color="0.35",  # neutral grey
                    text_size=6,  # inherit the active font size
                    line_width=0.3,  # arrow line width in points
                    arrowhead_size=7,  # Matplotlib mutation scale
                    arrowprops=None,  # optional advanced arrow overrides
                    # Additional Matplotlib text options can be added here:
                    # fontweight="normal",
                    # alpha=1.0,
                )

                ax.legend()

                # push_text_free(fig, ax)

            if seed_ == "wwtp_seed":
                # print((wtp_gdf.head(),sector_data.head()))
                # break

                wwtp_annotations = wwtp_gdf.merge(sector_data[[seed_, var]], left_on="id", right_on=seed_, how="left")
                wwtp_annotations.index = wwtp_gdf["name"]
                wwtp_annotations = wwtp_annotations.sort_values(
                    by=var,
                    ascending=False,
                    inplace=False,
                )["geometry"]

                plotting.annotate_radially(
                    ax,
                    wwtp_annotations.head(3),
                    shift=0.9,  # 0 = centre, 1 = axes border
                    curvature=0.03,  # arrow curvature
                    text_color="dimgray",  # inherit the active text color
                    arrow_color="0.35",  # neutral grey
                    text_size=6,  # inherit the active font size
                    line_width=0.3,  # arrow line width in points
                    arrowhead_size=7,  # Matplotlib mutation scale
                    arrowprops=None,  # optional advanced arrow overrides
                    # Additional Matplotlib text options can be added here:
                    # fontweight="normal",
                    # alpha=1.0,
                )

                # push_text_free(fig, ax)

            if seed_ == "wtp_seed":
                wtp_annotations = wtp_gdf.merge(sector_data[[seed_, var]], left_on="id", right_on=seed_, how="left")
                wtp_annotations.index = wtp_gdf["name"]
                wtp_annotations = wtp_annotations.sort_values(
                    by=var,
                    ascending=False,
                    inplace=False,
                )["geometry"]

                plotting.annotate_radially(
                    ax,
                    wtp_annotations.head(3),
                    shift=0.9,  # 0 = centre, 1 = axes border
                    curvature=0.03,  # arrow curvature
                    text_color="dimgray",  # inherit the active text color
                    arrow_color="0.35",  # neutral grey
                    text_size=6,  # inherit the active font size
                    line_width=0.3,  # arrow line width in points
                    arrowhead_size=7,  # Matplotlib mutation scale
                    arrowprops=None,  # optional advanced arrow overrides
                    # Additional Matplotlib text options can be added here:
                    # fontweight="normal",
                    # alpha=1.0,
                )

                # push_text_free(fig, ax)

            scatter = ax.scatter(
                sector_data.geometry.x,
                sector_data.geometry.y,
                s=sizes,
                c=values,
                norm=output_norm,
                cmap="viridis_r",
                alpha=1,
                linewidth=0,
                zorder=4,
            )

            ax.set_title(seed_full_label[seed_])

            ax.axis("off")

        fig.colorbar(
            scatter,
            ax=axs[-1],
            fraction=0.02,
            pad=0.04,
            label="Total Output (USD $)",
        )

        scalebar_ = ScaleBar(
            dx=dx,
            # label="",
            location="lower right",  # in relation to the whole plot
            # label_loc="left",
            scale_loc="bottom",  # in relation to the line
        )
        axs[-1].add_artist(scalebar_)

        plt.savefig(econ_utilities_figs / f"{var}.png", dpi=300)
        plt.close()


if __name__ == "__main__":
    """Pass workflow inputs and settings to the preprocessing function."""
    import logging
    from pathlib import Path

    snakemake = globals()["snakemake"]

    log_path = Path(str(snakemake.log[0]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    econ_utilities(
        wtp_path=snakemake.input.wtp_path,
        wwtp_path=snakemake.input.wwtp_path,
        roads_path=snakemake.input.roads_path,
        nodes_path=snakemake.input.nodes_path,
        edges_path=snakemake.input.edges_path,
        main_roads=snakemake.params.main_roads,
        voronoids_fig=snakemake.output.voronoids_fig_path,
        econ_utilities_figs=snakemake.output.figures,
    )
