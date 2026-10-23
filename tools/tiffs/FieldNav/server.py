import os
import time
import pickle
import xml.etree.ElementTree as ET
from urllib.request import urlopen
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import networkx as nx
from scipy.spatial import cKDTree
from pyproj import Transformer

# ================= КОНФИГУРАЦИЯ =================
GPKG_PATH = os.getenv("GPKG_PATH", "field_roads.gpkg")
CACHE_PATH = os.getenv("CACHE_PATH", "graph_cache.pkl")

# Настройки GeoServer (ограничение конкретной папкой / рабочей областью)
GEOSERVER_URL = os.getenv("GEOSERVER_URL", "http://localhost:8080/geoserver")
GEOSERVER_WORKSPACE = os.getenv("GEOSERVER_WORKSPACE", "inspector_data")

SPEED_CONFIG = {
    'motorway': 90, 'motorway_link': 60,
    'trunk': 80, 'trunk_link': 55,
    'primary': 65, 'primary_link': 45,
    'secondary': 55, 'secondary_link': 40,
    'tertiary': 45, 'tertiary_link': 35,
    'unclassified': 30,
    'residential': 25,
    'track': 18,
    'default': 20
}

# Глобальное состояние
state = {
    "graph": None,
    "tree": None,
    "nodes": [],
    "metric_crs": None,
    "to_metric": None,
    "to_wgs84": None
}


def build_or_load_graph():
    """Загружает граф из кэша или строит заново из GeoPackage."""
    if os.path.exists(CACHE_PATH) and os.path.exists(GPKG_PATH):
        if os.path.getmtime(CACHE_PATH) >= os.path.getmtime(GPKG_PATH):
            print(f"[*] Загрузка готового графа из кэша {CACHE_PATH}...")
            t0 = time.time()
            with open(CACHE_PATH, "rb") as f:
                cached = pickle.load(f)
            state["graph"] = cached["graph"]
            state["nodes"] = cached["nodes"]
            state["metric_crs"] = cached["metric_crs"]
            state["tree"] = cKDTree(state["nodes"])
            state["to_metric"] = Transformer.from_crs("EPSG:4326", state["metric_crs"], always_xy=True)
            state["to_wgs84"] = Transformer.from_crs(state["metric_crs"], "EPSG:4326", always_xy=True)
            print(f"[+] Кэш загружен за {time.time() - t0:.2f} сек. Узлов: {len(state['nodes'])}")
            return

    if not os.path.exists(GPKG_PATH):
        print(f"[!] ВНИМАНИЕ: Файл {GPKG_PATH} не найден. Положите его рядом с server.py.")
        return

    print(f"[*] Чтение {GPKG_PATH} и сборка графа...")
    t0 = time.time()
    import geopandas as gpd  # только при пересборке кэша
    gdf = gpd.read_file(GPKG_PATH)

    # Определяем метрическую проекцию (UTM) для точного расчета метров
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)

    if gdf.crs.is_geographic:
        metric_crs = gdf.estimate_utm_crs().to_string()
        gdf = gdf.to_crs(metric_crs)
    else:
        metric_crs = gdf.crs.to_string()

    G = nx.Graph()
    highways = gdf['highway'].fillna('default').astype(str).str.lower().tolist() if 'highway' in gdf.columns else ['default'] * len(gdf)

    for geom, r_type in zip(gdf.geometry, highways):
        if geom is None or geom.is_empty:
            continue
        lines = [geom] if geom.geom_type == 'LineString' else (list(geom.geoms) if geom.geom_type == 'MultiLineString' else [])
        
        speed_kmh = SPEED_CONFIG.get(r_type, SPEED_CONFIG['default'])
        speed_ms = speed_kmh / 3.6

        for line in lines:
            coords = line.coords
            for i in range(len(coords) - 1):
                # Округление до 10 см склеивает микро-погрешности топологии OSM
                p1 = (round(coords[i][0], 1), round(coords[i][1], 1))
                p2 = (round(coords[i + 1][0], 1), round(coords[i + 1][1], 1))
                if p1 == p2:
                    continue

                dx = p1[0] - p2[0]
                dy = p1[1] - p2[1]
                dist = (dx * dx + dy * dy) ** 0.5
                weight = dist / speed_ms

                # Если ребро уже есть, оставляем вариант с меньшим временем проезда
                if G.has_edge(p1, p2):
                    if weight < G[p1][p2]['weight']:
                        G[p1][p2].update(weight=weight, length=dist, highway=r_type)
                else:
                    G.add_edge(p1, p2, weight=weight, length=dist, highway=r_type)

    nodes = list(G.nodes)
    state["graph"] = G
    state["nodes"] = nodes
    state["metric_crs"] = metric_crs
    state["tree"] = cKDTree(nodes)
    state["to_metric"] = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    state["to_wgs84"] = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)

    print(f"[*] Сохранение кэша графа в {CACHE_PATH}...")
    with open(CACHE_PATH, "wb") as f:
        pickle.dump({"graph": G, "nodes": nodes, "metric_crs": metric_crs}, f)

    print(f"[+] Граф собран за {time.time() - t0:.1f} сек! Узлов: {len(nodes)}, Ребер: {G.number_of_edges()}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    build_or_load_graph()
    yield


app = FastAPI(title="Field Inspector Navigator API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/route")
def calculate_route(
    start_lon: float = Query(..., description="Долгота старта (WGS84)"),
    start_lat: float = Query(..., description="Широта старта (WGS84)"),
    end_lon: float = Query(..., description="Долгота цели (WGS84)"),
    end_lat: float = Query(..., description="Широта цели (WGS84)")
):
    if state["graph"] is None or len(state["nodes"]) == 0:
        raise HTTPException(status_code=503, detail="Граф дорог не загружен на сервере")

    # 1. Переводим входные координаты WGS84 в метрическую систему графа
    sx, sy = state["to_metric"].transform(start_lon, start_lat)
    ex, ey = state["to_metric"].transform(end_lon, end_lat)

    # 2. Ищем ближайшие узлы на дорожной сети
    start_snap_dist, start_idx = state["tree"].query((sx, sy))
    end_snap_dist, end_idx = state["tree"].query((ex, ey))

    start_node = state["nodes"][start_idx]
    end_node = state["nodes"][end_idx]

    # 3. Поиск оптимального маршрута по графу
    try:
        path = nx.shortest_path(state["graph"], source=start_node, target=end_node, weight="weight")
    except nx.NetworkXNoPath:
        raise HTTPException(
            status_code=404,
            detail="Невозможно построить маршрут: выбранные участки дорог не связаны между собой"
        )

    # 4. Расчет длины и времени по дорогам
    road_dist_m = 0.0
    road_time_s = 0.0
    G = state["graph"]

    for u, v in zip(path[:-1], path[1:]):
        edge = G[u][v]
        road_dist_m += edge["length"]
        road_time_s += edge["weight"]

    # 5. Перевод вершин маршрута обратно в WGS84 [lon, lat]
    road_coords_wgs84 = [list(state["to_wgs84"].transform(x, y)) for x, y in path]

    # Точки входа и схода с дороги (для отрисовки пунктира по полю)
    road_entry_wgs84 = road_coords_wgs84[0]
    road_exit_wgs84 = road_coords_wgs84[-1]

    # Время пешком/по полю со скоростью 5 км/ч (1.39 м/с) от дороги до точки замера
    offroad_time_s = (start_snap_dist + end_snap_dist) / 1.39
    total_time_min = round((road_time_s + offroad_time_s) / 60, 1)

    return {
        "status": "success",
        "metrics": {
            "road_distance_km": round(road_dist_m / 1000, 2),
            "offroad_start_m": round(float(start_snap_dist), 0),
            "offroad_target_m": round(float(end_snap_dist), 0),
            "duration_min": total_time_min
        },
        "road_geojson": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": road_coords_wgs84
            }
        },
        "offroad_geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"type": "start_approach"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[start_lon, start_lat], road_entry_wgs84]
                    }
                },
                {
                    "type": "Feature",
                    "properties": {"type": "target_approach"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [road_exit_wgs84, [end_lon, end_lat]]
                    }
                }
            ]
        }
    }


@app.get("/api/geoserver/layers")
def list_workspace_layers(
    geoserver_url: str = Query(GEOSERVER_URL),
    workspace: str = Query(GEOSERVER_WORKSPACE)
):
    """
    Запрашивает список слоев только из разрешенного Workspace (папки) GeoServer
    через изолированный Virtual WMS Endpoint.
    """
    wms_caps_url = f"{geoserver_url.rstrip('/')}/{workspace}/wms?service=WMS&version=1.3.0&request=GetCapabilities"
    try:
        with urlopen(wms_caps_url, timeout=5) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        ns = {'wms': 'http://www.opengis.net/wms'}
        layers = []
        for layer_el in root.findall('.//wms:Layer/wms:Layer', ns):
            name_el = layer_el.find('wms:Name', ns)
            title_el = layer_el.find('wms:Title', ns)
            if name_el is not None and name_el.text:
                name = name_el.text
                full_name = f"{workspace}:{name}" if ":" not in name else name
                layers.append({
                    "name": full_name,
                    "title": title_el.text if title_el is not None else name
                })
        return {"wms_url": f"{geoserver_url.rstrip('/')}/{workspace}/wms", "layers": layers}
    except Exception as e:
        return {
            "wms_url": f"{geoserver_url.rstrip('/')}/{workspace}/wms",
            "layers": [],
            "warning": f"GeoServer не ответил ({str(e)}). Вы можете ввести имя слоя вручную."
        }


if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
