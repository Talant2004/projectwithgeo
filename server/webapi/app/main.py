"""Povilika web API: local upload + phytosanitary act PDF."""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.auth import require_app_user
from app import report as report_mod

TOOLS = Path(os.environ.get("TOOLS_DIR", "/tools"))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

app = FastAPI(title="Povilika Web API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RASTER_EXT = {".tif", ".tiff"}
VECTOR_EXT = {".gpkg", ".shp"}
ALLOWED_EXT = RASTER_EXT | VECTOR_EXT


def _geoserver_url() -> str:
    return os.environ.get("GEOSERVER_URL", "http://geoserver:8080/geoserver").rstrip("/")


def _make_cfg():
    import povilika_cli as cli

    env_file = Path(os.environ.get("ENV_FILE", "/app/.env"))
    # Prefer environment (docker) over empty file
    host_dir = Path(os.environ.get("SURVEYS_HOST_DIR", "/data/surveys"))
    container_dir = os.environ.get("SURVEYS_CONTAINER_DIR", "/data/surveys").rstrip("/")
    cfg = cli.Config(
        url=_geoserver_url(),
        user=os.environ.get("GEOSERVER_ADMIN_USER", "admin"),
        password=os.environ.get("GEOSERVER_ADMIN_PASSWORD", "geoserver"),
        host_dir=host_dir,
        container_dir=container_dir,
        env=dict(os.environ),
    )
    return cli, cfg


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "geoserver": _geoserver_url()}


class ActRequest(BaseModel):
    survey_layer: str = Field(..., description="Имя растрового слоя для мини-карты")
    farm: str = ""
    field: str = ""
    survey_date: str = ""
    fids: list[str] = Field(default_factory=list, description="fid очагов; пусто = все по survey_layer")


@app.post("/api/report/act")
def report_act(body: ActRequest, _user: str = Depends(require_app_user)) -> Response:
    try:
        if body.fids:
            features = report_mod.fetch_foci(_geoserver_url(), fids=body.fids)
        else:
            features = report_mod.fetch_foci(_geoserver_url(), survey_layer=body.survey_layer)
    except Exception as e:
        raise HTTPException(502, f"Не удалось получить очаги: {e}") from e

    if not features:
        raise HTTPException(404, "Нет очагов для отчёта")

    # Fill farm/field/date from first feature if blank
    props0 = features[0].get("properties") or {}
    farm = body.farm or props0.get("farm") or ""
    field = body.field or props0.get("field") or ""
    survey_date = body.survey_date or (props0.get("created_at") or "")[:10]

    try:
        pdf = report_mod.build_act_pdf(
            geoserver_url=_geoserver_url(),
            survey_layer=body.survey_layer,
            farm=farm,
            field=field,
            survey_date=survey_date or dt.date.today().isoformat(),
            features=features,
        )
    except Exception as e:
        raise HTTPException(500, f"Ошибка PDF: {e}") from e

    name = f"akt_{body.survey_layer}_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/upload")
async def upload(
    farm: str = Form(...),
    field: str = Form(...),
    date: str = Form(..., description="YYYY-MM-DD"),
    title: str = Form(""),
    replace: str = Form("false"),
    file: UploadFile = File(...),
    _user: str = Depends(require_app_user),
) -> JSONResponse:
    """Сохранить файл на диск сервера и опубликовать в GeoServer.

    Только для локальной сети / прямого доступа к серверу — не через Cloudflare Tunnel
    (лимит тела запроса туннеля ~100 МБ).
    """
    filename = file.filename or "upload.bin"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Формат {ext} не поддерживается. Нужны: .tif/.tiff/.gpkg/.shp")

    try:
        survey_date = dt.date.fromisoformat(date)
    except ValueError as e:
        raise HTTPException(400, "Дата должна быть YYYY-MM-DD") from e

    do_replace = str(replace).lower() in ("1", "true", "yes", "on")
    cli, cfg = _make_cfg()
    cfg.host_dir.mkdir(parents=True, exist_ok=True)

    # Stream to temp then ingest (move into archive tree)
    suffix = ext
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=cfg.host_dir) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
    except Exception as e:
        raise HTTPException(500, f"Ошибка записи файла: {e}") from e

    logs: list[str] = []

    def log(msg: str) -> None:
        logs.append(msg)

    try:
        layers = cli.ingest_file(
            cfg,
            tmp_path,
            farm=farm.strip(),
            field=field.strip(),
            date=survey_date,
            title=title.strip() or None,
            move=True,
            replace=do_replace,
            log=log,
        )
    except SystemExit as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Ошибка публикации: {e}") from e

    return JSONResponse(
        {
            "ok": True,
            "layers": layers,
            "log": logs,
            "warning": (
                "Загрузка предназначена для локальной сети или сервера. "
                "Не используйте Cloudflare Tunnel для файлов больше ~100 МБ."
            ),
        }
    )


@app.get("/api/config")
def public_config() -> dict[str, Any]:
    """Публичные подсказки для фронта (без паролей)."""
    return {
        "workspace": "povilika",
        "geoserver_path": "/geoserver",
        "upload_note": "Загрузка больших TIFF — только по локальной сети, не через туннель.",
        "basemaps": ["osm", "esri"],
    }
