#!/usr/bin/env python3
"""Настройка GeoServer для приложения «Полевой маршрут» (инспектор).

  inspector_cli.py setup     — workspace inspector_data, пользователь, права
  inspector_cli.py publish   — опубликовать все TIFF из каталога слоёв
  inspector_cli.py list      — список слоёв workspace

Снимки и очаги повилики (workspace povilika) сюда не входят.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / "server" / ".env"
WORKSPACE = "inspector_data"
NAMESPACE_URI = "urn:inspector"
INSPECTOR_ROLE = "ROLE_INSPECTOR"

LAYER_TITLES = {
    "ili_delta_incubation_recurrence_2016_2025_60m": "Зоны многолетних очагов отрождения (2016–2025)",
    "ili_delta_total_hotspot_recurrence_2016_2025_60m": "Сводная повторяемость очагов (2016–2025)",
    "ili_delta_locust_recurrence_2017_2025_60m": "Повторяемость саранчи (2017–2025)",
    "ili_delta_hydro_indicators_2021_30m": "Гидрологические индикаторы (2021)",
}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


class GS:
    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.auth = (user, password)
        self.rest = f"{self.url}/rest"

    def _check(self, r: requests.Response, ok=(200, 201)) -> requests.Response:
        if r.status_code not in ok:
            raise SystemExit(f"GeoServer {r.request.method} {r.url} -> {r.status_code}: {r.text[:500]}")
        return r

    def get(self, path: str):
        return self.s.get(f"{self.rest}{path}", headers={"Accept": "application/json"}, timeout=60)

    def exists(self, path: str) -> bool:
        return self.get(path).status_code == 200

    def post_json(self, path: str, body: dict, ok=(200, 201)):
        return self._check(self.s.post(f"{self.rest}{path}", json=body, timeout=120), ok)

    def put_json(self, path: str, body: dict):
        return self._check(self.s.put(f"{self.rest}{path}", json=body, timeout=120))


def _cfg(env_file: Path):
    env = load_env(env_file)
    return env, GS(
        env.get("GEOSERVER_URL", "http://localhost:8080/geoserver"),
        env.get("GEOSERVER_ADMIN_USER", "admin"),
        env.get("GEOSERVER_ADMIN_PASSWORD", "geoserver"),
    )


def cmd_setup(env_file: Path) -> None:
    env, gs = _cfg(env_file)
    if gs.exists(f"/workspaces/{WORKSPACE}"):
        print(f"[=] workspace {WORKSPACE}")
    else:
        gs.post_json("/namespaces", {"namespace": {"prefix": WORKSPACE, "uri": NAMESPACE_URI}})
        print(f"[+] workspace {WORKSPACE}")

    user = env.get("INSPECTOR_USER", "inspector_app")
    password = env.get("INSPECTOR_PASSWORD")
    if not password:
        raise SystemExit("INSPECTOR_PASSWORD не задан в .env")

    roles = gs.get("/security/roles").json().get("roles", [])
    if INSPECTOR_ROLE not in roles:
        gs._check(gs.s.post(f"{gs.rest}/security/roles/role/{INSPECTOR_ROLE}", timeout=60))
        print(f"[+] role {INSPECTOR_ROLE}")

    users = gs.get("/security/usergroup/users").json().get("users", [])
    body = {"user": {"userName": user, "password": password, "enabled": True}}
    if any(u.get("userName") == user for u in users):
        gs.post_json(f"/security/usergroup/user/{user}", body)
        print(f"[=] user {user} (пароль обновлён)")
    else:
        gs.post_json("/security/usergroup/users", body)
        print(f"[+] user {user}")
    gs._check(gs.s.post(f"{gs.rest}/security/roles/role/{INSPECTOR_ROLE}/user/{user}", timeout=60))

    # Чтение слоёв инспектора — для ROLE_INSPECTOR и анонимно (тайлы на карте).
    # Запись — только админ. К workspace povilika этот пользователь не привязан.
    acl = gs.get("/security/acl/layers").json()
    rules = {
        f"{WORKSPACE}.*.r": f"*,{INSPECTOR_ROLE}",
        f"{WORKSPACE}.*.w": "ADMIN",
    }
    for rule, roles_s in rules.items():
        if rule in acl:
            gs.put_json("/security/acl/layers", {rule: roles_s})
        else:
            gs.post_json("/security/acl/layers", {rule: roles_s})
        print(f"[+] ACL {rule} -> {roles_s}")

    print("Готово. Опубликуйте слои: python3 tools/inspector_cli.py publish")


def _layer_name(path: Path) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")
    return name if name[0].isalpha() else f"s_{name}"


def publish_file(env_file: Path, src: Path, *, title: str | None = None, replace: bool = False,
                 progress=None) -> str:
    """Копирует TIFF в каталог слоёв и публикует в GeoServer. Возвращает имя слоя."""
    env, gs = _cfg(env_file)
    host_dir = Path(env.get("INSPECTOR_LAYERS_HOST_DIR", "/data/povilika/inspector_layers"))
    container_dir = env.get("INSPECTOR_LAYERS_CONTAINER_DIR", "/data/inspector").rstrip("/")
    host_dir.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    if src.suffix.lower() not in {".tif", ".tiff"}:
        raise SystemExit(f"Нужен файл .tif: {src}")
    if not gs.exists(f"/workspaces/{WORKSPACE}"):
        raise SystemExit("Сначала выполните: python3 tools/inspector_cli.py setup")

    store = _layer_name(src)
    title = title or LAYER_TITLES.get(store, store)
    dst = host_dir / src.name
    store_path = f"/workspaces/{WORKSPACE}/coveragestores/{store}"

    if gs.exists(store_path) and not replace:
        raise SystemExit(f"Слой уже опубликован: {store} (укажите --replace)")

    if gs.exists(store_path) and replace:
        gs._check(gs.s.delete(f"{gs.rest}{store_path}?recurse=true", timeout=120), ok=(200, 404))

    if src.resolve() != dst.resolve():
        total = src.stat().st_size
        done = 0
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            while chunk := fin.read(8 * 1024 * 1024):
                fout.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    elif progress:
        progress(1, 1)

    container_path = f"{container_dir}/{dst.name}"
    gs._check(gs.s.put(
        f"{gs.rest}/workspaces/{WORKSPACE}/coveragestores/{store}/external.geotiff",
        params={"configure": "first", "coverageName": store},
        data=container_path,
        headers={"Content-Type": "text/plain"},
        timeout=600,
    ))
    cov_path = f"{store_path}/coverages/{store}"
    cov = gs.get(cov_path).json()
    cov["coverage"]["title"] = title
    gs.put_json(cov_path, cov)
    return store


def remove_layer(env_file: Path, store: str) -> None:
    env, gs = _cfg(env_file)
    host_dir = Path(env.get("INSPECTOR_LAYERS_HOST_DIR", "/data/povilika/inspector_layers"))
    store_path = f"/workspaces/{WORKSPACE}/coveragestores/{store}"
    if gs.exists(store_path):
        gs._check(gs.s.delete(f"{gs.rest}{store_path}?recurse=true", timeout=120), ok=(200, 404))
    for p in host_dir.glob(f"{store}.*"):
        p.unlink(missing_ok=True)
    # исходное имя файла могло отличаться регистром — ищем по stem
    for p in host_dir.iterdir():
        if p.is_file() and _layer_name(p) == store:
            p.unlink(missing_ok=True)


def list_layers(env_file: Path) -> list[dict]:
    _, gs = _cfg(env_file)
    r = gs.get(f"/workspaces/{WORKSPACE}/layers.json")
    if r.status_code != 200:
        return []
    layers = r.json().get("layers", {}).get("layer", [])
    if isinstance(layers, dict):
        layers = [layers]
    out = []
    for layer in layers:
        name = layer.get("name", "")
        title = LAYER_TITLES.get(name, name)
        cov = gs.get(f"/workspaces/{WORKSPACE}/coveragestores/{name}/coverages/{name}")
        if cov.status_code == 200:
            title = cov.json().get("coverage", {}).get("title") or title
            bbox = cov.json().get("coverage", {}).get("latLonBoundingBox", {})
        else:
            bbox = {}
        out.append({"name": name, "title": title, "bbox": bbox})
    return out


def cmd_publish(env_file: Path, files: list[Path] | None = None, replace: bool = False) -> None:
    env = load_env(env_file)
    host_dir = Path(env.get("INSPECTOR_LAYERS_HOST_DIR", "/data/povilika/inspector_layers"))
    if files:
        for f in files:
            store = publish_file(env_file, f, replace=replace)
            print(f"[+] {WORKSPACE}:{store}")
        return
    host_dir.mkdir(parents=True, exist_ok=True)
    tifs = sorted(host_dir.glob("*.tif")) + sorted(host_dir.glob("*.tiff"))
    if not tifs:
        raise SystemExit(f"В {host_dir} нет .tif файлов")
    for src in tifs:
        try:
            store = publish_file(env_file, src, replace=replace)
            print(f"[+] {WORKSPACE}:{store}")
        except SystemExit as e:
            if "уже опубликован" in str(e):
                print(f"[=] {WORKSPACE}:{_layer_name(src)}")
            else:
                raise
    print(f"Готово, файлов: {len(tifs)}")


def cmd_list(env_file: Path) -> None:
    layers = list_layers(env_file)
    if not layers:
        print("(слоёв нет)")
        return
    for layer in layers:
        print(f"{layer['name']}\t{layer['title']}")


def cmd_remove(env_file: Path, store: str) -> None:
    remove_layer(env_file, store)
    print(f"[-] снят {WORKSPACE}:{store}")


def main() -> None:
    p = argparse.ArgumentParser(description="Полевой маршрут — слои инспектора")
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    pub = sub.add_parser("publish")
    pub.add_argument("files", nargs="*", type=Path)
    pub.add_argument("--replace", action="store_true")
    sub.add_parser("list")
    rm = sub.add_parser("remove")
    rm.add_argument("layer")
    args = p.parse_args()
    if args.cmd == "setup":
        cmd_setup(args.env_file)
    elif args.cmd == "publish":
        cmd_publish(args.env_file, args.files or None, replace=args.replace)
    elif args.cmd == "list":
        cmd_list(args.env_file)
    elif args.cmd == "remove":
        cmd_remove(args.env_file, args.layer)


if __name__ == "__main__":
    main()
