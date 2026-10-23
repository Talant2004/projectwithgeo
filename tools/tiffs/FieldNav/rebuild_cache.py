"""Пересобрать graph_cache.pkl из GPKG (для урезанной сети дельты Или)."""
import os
import pickle
import time

import geopandas as gpd
import networkx as nx

GPKG = os.environ.get("GPKG_PATH", "field_roads_ili.gpkg")
CACHE = os.environ.get("CACHE_PATH", "graph_cache_ili.pkl")

SPEED = {
    "motorway": 90, "motorway_link": 60, "trunk": 80, "trunk_link": 55,
    "primary": 65, "primary_link": 45, "secondary": 55, "secondary_link": 40,
    "tertiary": 45, "tertiary_link": 35, "unclassified": 30, "residential": 25,
    "track": 18, "default": 20,
}

t0 = time.time()
gdf = gpd.read_file(GPKG)
if gdf.crs is None:
    gdf.set_crs("EPSG:4326", inplace=True)
if gdf.crs.is_geographic:
    metric = gdf.estimate_utm_crs().to_string()
    gdf = gdf.to_crs(metric)
else:
    metric = gdf.crs.to_string()

G = nx.Graph()
highways = gdf["highway"].fillna("default").astype(str).str.lower().tolist() if "highway" in gdf.columns else ["default"] * len(gdf)
for geom, r_type in zip(gdf.geometry, highways):
    if geom is None or geom.is_empty:
        continue
    lines = [geom] if geom.geom_type == "LineString" else (list(geom.geoms) if geom.geom_type == "MultiLineString" else [])
    speed_ms = SPEED.get(r_type, SPEED["default"]) / 3.6
    for line in lines:
        coords = line.coords
        for i in range(len(coords) - 1):
            p1 = (round(coords[i][0], 1), round(coords[i][1], 1))
            p2 = (round(coords[i + 1][0], 1), round(coords[i + 1][1], 1))
            if p1 == p2:
                continue
            dist = ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
            weight = dist / speed_ms
            if G.has_edge(p1, p2):
                if weight < G[p1][p2]["weight"]:
                    G[p1][p2].update(weight=weight, length=dist, highway=r_type)
            else:
                G.add_edge(p1, p2, weight=weight, length=dist, highway=r_type)

# Оставляем крупнейшую связную компоненту — иначе маршрут часто «не связан».
if G.number_of_nodes():
    biggest = max(nx.connected_components(G), key=len)
    G = G.subgraph(biggest).copy()
nodes = list(G.nodes)
with open(CACHE, "wb") as f:
    pickle.dump({"graph": G, "nodes": nodes, "metric_crs": metric}, f)
print(f"OK {CACHE}: nodes={len(nodes)} edges={G.number_of_edges()} {time.time()-t0:.1f}s size={os.path.getsize(CACHE)/1e6:.1f}MB")
