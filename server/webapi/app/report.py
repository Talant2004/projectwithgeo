"""PDF «Акт фитосанитарного обследования»."""
from __future__ import annotations

import io
import math
from datetime import datetime, timezone
from typing import Any

import requests
from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

WORKSPACE = "povilika"
MAP_W, MAP_H = 1200, 720

_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _register_fonts() -> tuple[str, str]:
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", _FONT_REG))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", _FONT_BOLD))
        return "DejaVu", "DejaVu-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


def _lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return x, y


def _feature_ring(geom: dict) -> list[tuple[float, float]]:
    """Return exterior ring as (lon, lat) list."""
    if not geom:
        return []
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    if t == "Polygon" and coords:
        return [(c[0], c[1]) for c in coords[0]]
    if t == "MultiPolygon" and coords:
        return [(c[0], c[1]) for c in coords[0][0]]
    return []


def _centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    if len(ring) < 3:
        return (0.0, 0.0)
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    return (
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    )


def _bbox_mercator(rings: list[list[tuple[float, float]]], pad: float = 0.08) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for ring in rings:
        for lon, lat in ring:
            x, y = _lonlat_to_mercator(lon, lat)
            xs.append(x)
            ys.append(y)
    if not xs:
        # Kazakhstan fallback
        return (6000000, 5000000, 9000000, 7000000)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    dx = max(maxx - minx, 50.0)
    dy = max(maxy - miny, 50.0)
    # keep aspect ~ MAP_W/MAP_H
    target = MAP_W / MAP_H
    if dx / dy < target:
        dx = dy * target
    else:
        dy = dx / target
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    dx *= 1 + pad
    dy *= 1 + pad
    return cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2


def fetch_foci(
    geoserver_url: str,
    *,
    survey_layer: str | None = None,
    fids: list[str] | None = None,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": f"{WORKSPACE}:foci",
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
    }
    if fids:
        # GeoServer feature id filter via FEATUREID
        params["featureid"] = ",".join(fids)
    elif survey_layer:
        safe = survey_layer.replace("'", "''")
        params["CQL_FILTER"] = f"survey_layer = '{safe}'"

    r = requests.get(f"{geoserver_url.rstrip('/')}/{WORKSPACE}/wfs", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return list(data.get("features") or [])


def fetch_map_png(
    geoserver_url: str,
    layer: str,
    bbox: tuple[float, float, float, float],
    width: int = MAP_W,
    height: int = MAP_H,
    timeout: int = 90,
) -> bytes | None:
    minx, miny, maxx, maxy = bbox
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": f"{WORKSPACE}:{layer}",
        "styles": "",
        "srs": "EPSG:3857",
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "width": str(width),
        "height": str(height),
        "format": "image/png",
        "bgcolor": "0xFFFFFF",
        "transparent": "false",
    }
    r = requests.get(f"{geoserver_url.rstrip('/')}/{WORKSPACE}/wms", params=params, timeout=timeout)
    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and ctype.startswith("image/"):
        return r.content
    return None


def _overlay_polygons(
    png: bytes | None,
    rings: list[list[tuple[float, float]]],
    bbox: tuple[float, float, float, float],
) -> bytes:
    minx, miny, maxx, maxy = bbox
    if png:
        img = Image.open(io.BytesIO(png)).convert("RGBA")
    else:
        img = Image.new("RGBA", (MAP_W, MAP_H), (245, 245, 245, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    dx = maxx - minx or 1.0
    dy = maxy - miny or 1.0

    def to_px(lon: float, lat: float) -> tuple[float, float]:
        x, y = _lonlat_to_mercator(lon, lat)
        px = (x - minx) / dx * w
        py = (maxy - y) / dy * h
        return px, py

    for ring in rings:
        if len(ring) < 3:
            continue
        pts = [to_px(lon, lat) for lon, lat in ring]
        draw.polygon(pts, outline=(198, 40, 40, 255), fill=(198, 40, 40, 70))
        draw.line(pts + [pts[0]], fill=(198, 40, 40, 255), width=3)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def build_act_pdf(
    *,
    geoserver_url: str,
    survey_layer: str,
    farm: str,
    field: str,
    survey_date: str,
    features: list[dict[str, Any]],
) -> bytes:
    font, font_bold = _register_fonts()
    rings = [_feature_ring(f.get("geometry") or {}) for f in features]
    rings = [r for r in rings if len(r) >= 3]
    bbox = _bbox_mercator(rings)
    base_png = fetch_map_png(geoserver_url, survey_layer, bbox) if survey_layer else None
    map_png = _overlay_polygons(base_png, rings, bbox)

    rows_data: list[tuple[int, float, float, float]] = []
    total_m2 = 0.0
    for i, f in enumerate(features, start=1):
        props = f.get("properties") or {}
        ring = _feature_ring(f.get("geometry") or {})
        lon, lat = _centroid(ring)
        area_ha = float(props.get("area_ha") or 0.0)
        area_m2 = area_ha * 10000.0
        total_m2 += area_m2
        rows_data.append((i, lon, lat, area_m2))

    # field center from all rings
    all_pts = [p for r in rings for p in r]
    if all_pts:
        field_lon = sum(p[0] for p in all_pts) / len(all_pts)
        field_lat = sum(p[1] for p in all_pts) / len(all_pts)
    else:
        field_lon = field_lat = 0.0

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Акт фитосанитарного обследования",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleRu",
        parent=styles["Title"],
        fontName=font_bold,
        fontSize=14,
        leading=18,
        spaceAfter=8,
    )
    body = ParagraphStyle(
        "BodyRu",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        leading=14,
    )
    small = ParagraphStyle(
        "SmallRu",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#555555"),
    )

    story: list[Any] = []
    story.append(Paragraph("Акт фитосанитарного обследования", title_style))
    now = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
    story.append(Paragraph(f"Дата формирования: {now}", body))
    story.append(Paragraph(f"Хозяйство: <b>{farm}</b> &nbsp;&nbsp; Поле: <b>{field}</b>", body))
    story.append(Paragraph(f"Дата съёмки: {survey_date}", body))
    story.append(
        Paragraph(
            f"Координаты участка (WGS-84): {field_lat:.6f}°, {field_lon:.6f}°",
            body,
        )
    )
    story.append(Paragraph("Вредитель: Cuscuta campestris (повилика полевая)", body))
    story.append(Spacer(1, 8))

    map_buf = io.BytesIO(map_png)
    story.append(RLImage(map_buf, width=170 * mm, height=102 * mm))
    story.append(Paragraph("Мини-карта выбранного участка с контурами очагов", small))
    story.append(Spacer(1, 10))

    table_data = [["№", "Долгота", "Широта", "Площадь, м²"]]
    for n, lon, lat, m2 in rows_data:
        table_data.append([str(n), f"{lon:.6f}", f"{lat:.6f}", f"{m2:.1f}"])
    table_data.append(["", "", "Итого", f"{total_m2:.1f}"])

    tbl = Table(table_data, colWidths=[15 * mm, 40 * mm, 40 * mm, 40 * mm])
    tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), font_bold),
                ("FONTNAME", (0, 1), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F5E9")),
                ("FONTNAME", (0, -1), (-1, -1), font_bold),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            f"Общая площадь заражения: <b>{total_m2:.1f} м²</b> ({total_m2 / 10000:.4f} га)",
            body,
        )
    )
    story.append(Spacer(1, 16))
    story.append(Paragraph("_________________ / _________________", body))
    story.append(Paragraph("Подпись ответственного лица", small))

    doc.build(story)
    return buf.getvalue()
