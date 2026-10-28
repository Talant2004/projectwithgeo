#!/usr/bin/env python3
"""CuscutaDetector — лёгкое standalone-приложение: RGB GeoTIFF → HSV → KML."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import rasterio
from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyleOption,
    QVBoxLayout,
    QWidget,
)
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes, sieve
from rasterio.transform import Affine
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

APP_TITLE = "CuscutaDetector"
APP_VERSION = "1.1.0"


class InteractivePreview(QWidget):
    mouse_dragged = pyqtSignal(int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background-color: #222; border: 1px solid #555;")
        self.image_rect = None
        self._raw_qimage = None
        self.preview_shape = None

    def update_image(self, qimage, preview_shape) -> None:
        self.preview_shape = preview_shape
        self._raw_qimage = qimage
        self.update()

    def paintEvent(self, event) -> None:
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, p, self)
        if self._raw_qimage is None:
            p.setPen(QColor(200, 200, 200))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Превью появится здесь")
            return
        canvas = self.rect()
        scaled = self._raw_qimage.size().scaled(canvas.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (canvas.width() - scaled.width()) // 2
        y = (canvas.height() - scaled.height()) // 2
        self.image_rect = QRect(x, y, scaled.width(), scaled.height())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(self.image_rect, self._raw_qimage)

    def mousePressEvent(self, event) -> None:
        self._handle_mouse(event)

    def mouseMoveEvent(self, event) -> None:
        self._handle_mouse(event)

    def _handle_mouse(self, event) -> None:
        if not self._raw_qimage or not self.image_rect:
            return
        pos = event.pos()
        if not self.image_rect.contains(pos):
            return
        sx = self.preview_shape[1] / self.image_rect.width()
        sy = self.preview_shape[0] / self.image_rect.height()
        rx = int((pos.x() - self.image_rect.x()) * sx)
        ry = int((pos.y() - self.image_rect.y()) * sy)
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.mouse_dragged.emit(rx, ry, 1)
        elif event.buttons() & Qt.MouseButton.RightButton:
            self.mouse_dragged.emit(rx, ry, 2)


def hsv_mask(rgb: np.ndarray, sensitivity: int) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h_min = int(max(5, 25 - sensitivity / 5.0))
    h_max = int(min(60, 35 + sensitivity / 5.0))
    sv = int(max(20, 200 - sensitivity * 1.8))
    m = cv2.inRange(hsv, np.array([h_min, sv, sv]), np.array([h_max, 255, 255]))
    out = np.zeros(rgb.shape[:2], dtype=np.uint8)
    out[m == 255] = 1
    return out


def fill_holes(geom):
    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
    return geom


def to_wgs84(geom, crs) -> object:
    if crs is None:
        return geom
    crs_str = crs.to_string() if hasattr(crs, "to_string") else str(crs)
    if crs_str in ("EPSG:4326", "OGC:CRS84"):
        return geom
    fwd = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return shp_transform(lambda x, y, z=None: fwd.transform(x, y), geom)


def metric_buffer(geom, meters: float, crs):
    if meters <= 0:
        return geom
    if crs is not None and getattr(crs, "is_geographic", False):
        to_m = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)
        to_src = Transformer.from_crs("EPSG:3857", crs, always_xy=True)
        g = shp_transform(lambda x, y, z=None: to_m.transform(x, y), geom)
        g = g.buffer(meters)
        return shp_transform(lambda x, y, z=None: to_src.transform(x, y), g)
    return geom.buffer(meters)


def write_kml(path: str, geoms_wgs84: list) -> None:
    """Минимальный KML 2.2 без fiona/geopandas."""
    ns = "http://www.opengis.net/kml/2.2"
    ET.register_namespace("", ns)
    kml = ET.Element(f"{{{ns}}}kml")
    doc = ET.SubElement(kml, f"{{{ns}}}Document")
    ET.SubElement(doc, f"{{{ns}}}name").text = "CuscutaDetector"
    for i, geom in enumerate(geoms_wgs84, 1):
        if geom.is_empty:
            continue
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for j, poly in enumerate(parts):
            if poly.is_empty or poly.geom_type != "Polygon":
                continue
            pm = ET.SubElement(doc, f"{{{ns}}}Placemark")
            ET.SubElement(pm, f"{{{ns}}}name").text = f"focus_{i}" if j == 0 else f"focus_{i}_{j}"
            polyg = ET.SubElement(pm, f"{{{ns}}}Polygon")
            ET.SubElement(polyg, f"{{{ns}}}tessellate").text = "1"
            outer = ET.SubElement(polyg, f"{{{ns}}}outerBoundaryIs")
            ring = ET.SubElement(outer, f"{{{ns}}}LinearRing")
            coords = []
            for x, y in poly.exterior.coords:
                coords.append(f"{x:.8f},{y:.8f},0")
            ET.SubElement(ring, f"{{{ns}}}coordinates").text = " ".join(coords)
    Path(path).write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(kml, encoding="utf-8")
    )


class DodderDetectorApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION} — RGB / HSV")
        self.setGeometry(50, 50, 1400, 800)
        self.rgb_path = None
        self.preview_rgb = None
        self.base_mask = None
        self.user_mask = None
        self.valid_mask = None
        self.preview_kml_mask = None
        self.orig_transform = None
        self.orig_width = None
        self.orig_height = None
        self.orig_crs = None
        self.final_geoms = None  # list of shapely geoms in source CRS
        self.current_preview_bytes = None
        self.orig_bytes = None
        self._init_ui()

    def _init_ui(self) -> None:
        main = QWidget()
        self.setCentralWidget(main)
        root = QHBoxLayout(main)

        side = QWidget()
        side.setFixedWidth(350)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)

        g1 = QGroupBox("1. Загрузка RGB")
        l1 = QVBoxLayout()
        self.btn_load = QPushButton("Загрузить RGB-ортофотоплан (GeoTIFF)")
        self.btn_load.clicked.connect(self.load_rgb)
        self.lbl_rgb = QLabel("RGB: не загружен")
        hint = QLabel("Только RGB. Детекция по цвету HSV.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;font-size:11px;")
        l1.addWidget(self.btn_load)
        l1.addWidget(self.lbl_rgb)
        l1.addWidget(hint)
        g1.setLayout(l1)
        lay.addWidget(g1)

        g2 = QGroupBox("2. Интерактивный поиск (HSV)")
        l2 = QFormLayout()
        self.slider_thresh = QSlider(Qt.Orientation.Horizontal)
        self.slider_thresh.setRange(0, 100)
        self.slider_thresh.setValue(40)
        self.slider_thresh.valueChanged.connect(self.calculate_base_mask)
        self.slider_brush = QSlider(Qt.Orientation.Horizontal)
        self.slider_brush.setRange(1, 100)
        self.slider_brush.setValue(20)
        self.btn_clear = QPushButton("Сбросить ручные правки")
        self.btn_clear.clicked.connect(self.clear_user_mask)
        l2.addRow("Чувствительность:", self.slider_thresh)
        l2.addRow("Размер кисти:", self.slider_brush)
        l2.addRow("", QLabel("<i>ЛКМ — добавить · ПКМ — стереть</i>"))
        l2.addRow("", self.btn_clear)
        g2.setLayout(l2)
        lay.addWidget(g2)

        g3 = QGroupBox("3. Расчёт и экспорт KML")
        l3 = QFormLayout()
        self.spin_resolution = QDoubleSpinBox()
        self.spin_resolution.setValue(0.5)
        self.spin_resolution.setSingleStep(0.1)
        self.spin_sieve = QSpinBox()
        self.spin_sieve.setRange(1, 10000)
        self.spin_sieve.setValue(50)
        self.spin_buffer = QDoubleSpinBox()
        self.spin_buffer.setRange(0, 50)
        self.spin_buffer.setValue(2.0)
        self.chk_holes = QCheckBox("Заливка внутренних пустот")
        self.chk_holes.setChecked(True)
        self.btn_calc = QPushButton("Рассчитать полигоны (KML)")
        self.btn_calc.clicked.connect(self.calculate_polygons)
        self.btn_calc.setEnabled(False)
        self.btn_export = QPushButton("Сохранить файл KML")
        self.btn_export.clicked.connect(self.export_kml)
        self.btn_export.setEnabled(False)
        l3.addRow("Разрешение (м):", self.spin_resolution)
        l3.addRow("Мин. размер пятна (пикс.):", self.spin_sieve)
        l3.addRow("Буфер (м):", self.spin_buffer)
        l3.addRow("", self.chk_holes)
        l3.addRow("", self.btn_calc)
        l3.addRow("", self.btn_export)
        g3.setLayout(l3)
        lay.addWidget(g3)
        lay.addStretch()

        split = QSplitter(Qt.Orientation.Horizontal)
        ow, ol = QWidget(), QVBoxLayout()
        ol.setContentsMargins(0, 0, 0, 0)
        lab1 = QLabel("Оригинальный снимок")
        lab1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ol.addWidget(lab1)
        self.canvas_orig = InteractivePreview()
        ol.addWidget(self.canvas_orig, 1)
        ow.setLayout(ol)

        mw, ml = QWidget(), QVBoxLayout()
        ml.setContentsMargins(0, 0, 0, 0)
        lab2 = QLabel("Результат обнаружения (HSV)")
        lab2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ml.addWidget(lab2)
        self.canvas_mask = InteractivePreview()
        ml.addWidget(self.canvas_mask, 1)
        mw.setLayout(ml)

        split.addWidget(ow)
        split.addWidget(mw)
        self.canvas_orig.mouse_dragged.connect(self.apply_brush)
        self.canvas_mask.mouse_dragged.connect(self.apply_brush)
        root.addWidget(side)
        root.addWidget(split, 1)

    def load_rgb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "RGB растр", "", "TIFF (*.tif *.tiff)")
        if not path:
            return
        self.rgb_path = path
        self.lbl_rgb.setText(f"RGB: {Path(path).name}")
        self.btn_calc.setEnabled(True)
        self.prepare_realtime_data()

    def prepare_realtime_data(self) -> None:
        if not self.rgb_path:
            return
        with rasterio.open(self.rgb_path) as src:
            self.orig_transform = src.transform
            self.orig_width = src.width
            self.orig_height = src.height
            self.orig_crs = src.crs
            scale = min(1.0, 1000.0 / max(src.width, src.height))
            out_shape = (int(max(1, src.height * scale)), int(max(1, src.width * scale)))
            img = src.read((1, 2, 3), out_shape=out_shape, resampling=Resampling.bilinear)
            nodata = src.read_masks(1, out_shape=out_shape, resampling=Resampling.nearest)
            self.valid_mask = nodata > 0
            img = np.transpose(img, (1, 2, 0))
            if img.dtype != np.uint8:
                img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            black = (img[:, :, 0] == 0) & (img[:, :, 1] == 0) & (img[:, :, 2] == 0)
            white = (img[:, :, 0] == 255) & (img[:, :, 1] == 255) & (img[:, :, 2] == 255)
            self.valid_mask = self.valid_mask & ~black & ~white
            self.preview_rgb = img
        h, w, ch = self.preview_rgb.shape
        self.orig_bytes = np.ascontiguousarray(self.preview_rgb).tobytes()
        self.canvas_orig.update_image(
            QImage(self.orig_bytes, w, h, ch * w, QImage.Format.Format_RGB888), (h, w)
        )
        self.user_mask = np.zeros((h, w), dtype=np.uint8)
        self.calculate_base_mask()

    def reset_calculation(self) -> None:
        self.preview_kml_mask = None
        self.final_geoms = None
        self.btn_export.setEnabled(False)

    def calculate_base_mask(self) -> None:
        if self.preview_rgb is None:
            return
        self.reset_calculation()
        self.base_mask = hsv_mask(self.preview_rgb, self.slider_thresh.value())
        if self.valid_mask is not None:
            self.base_mask[~self.valid_mask] = 0
        self.update_display()

    def apply_brush(self, x, y, mode) -> None:
        if self.user_mask is None:
            return
        self.reset_calculation()
        cv2.circle(self.user_mask, (x, y), self.slider_brush.value(), mode, -1)
        self.update_display()

    def clear_user_mask(self) -> None:
        if self.user_mask is not None:
            self.reset_calculation()
            self.user_mask.fill(0)
            self.update_display()

    def update_display(self) -> None:
        if self.preview_rgb is None:
            return
        final = np.where(self.user_mask == 1, 1, np.where(self.user_mask == 2, 0, self.base_mask))
        display = self.preview_rgb.copy()
        display[final == 1] = [255, 0, 0]
        if self.preview_kml_mask is not None:
            contours, _ = cv2.findContours(
                self.preview_kml_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            cv2.drawContours(display, contours, -1, (0, 255, 255), 2)
        h, w, ch = display.shape
        display = np.ascontiguousarray(display)
        self.current_preview_bytes = display.tobytes()
        self.canvas_mask.update_image(
            QImage(self.current_preview_bytes, w, h, ch * w, QImage.Format.Format_RGB888),
            (h, w),
        )

    def calculate_polygons(self) -> None:
        if not self.rgb_path:
            return
        self.btn_calc.setText("Идёт расчёт…")
        QApplication.processEvents()
        target_res = self.spin_resolution.value()
        sensitivity = self.slider_thresh.value()
        try:
            with rasterio.open(self.rgb_path) as src:
                pixel_size = abs(src.transform[0])
                target_u = (
                    target_res / 111320.0 if src.crs and src.crs.is_geographic else target_res
                )
                scale = pixel_size / target_u if target_u > pixel_size else 1.0
                nw = max(1, int(src.width * scale))
                nh = max(1, int(src.height * scale))
                rgb = src.read(out_shape=(src.count, nh, nw), resampling=Resampling.bilinear)
                nodata = src.read_masks(1, out_shape=(nh, nw), resampling=Resampling.nearest)
                valid = nodata > 0
                rgb_hwc = np.transpose(rgb, (1, 2, 0))
                if rgb_hwc.dtype != np.uint8:
                    rgb_hwc = cv2.normalize(rgb_hwc, None, 0, 255, cv2.NORM_MINMAX).astype(
                        np.uint8
                    )
                black = (rgb_hwc[:, :, 0] == 0) & (rgb_hwc[:, :, 1] == 0) & (rgb_hwc[:, :, 2] == 0)
                white = (
                    (rgb_hwc[:, :, 0] == 255)
                    & (rgb_hwc[:, :, 1] == 255)
                    & (rgb_hwc[:, :, 2] == 255)
                )
                valid = valid & ~black & ~white
                new_transform = src.transform * src.transform.scale(
                    src.width / nw, src.height / nh
                )
                crs = src.crs

            mask = hsv_mask(rgb_hwc, sensitivity)
            mask[~valid] = 0
            if self.user_mask is not None and np.any(self.user_mask > 0):
                hu = cv2.resize(self.user_mask, (nw, nh), interpolation=cv2.INTER_NEAREST)
                mask = np.where(hu == 1, 1, np.where(hu == 2, 0, mask))

            sieved = sieve(mask, size=self.spin_sieve.value(), connectivity=4)
            polys = [
                shape(geom)
                for geom, val in shapes(sieved, mask=sieved == 1, transform=new_transform)
                if val == 1
            ]
            if not polys:
                QMessageBox.warning(self, "Пусто", "Очаги не найдены. Измените чувствительность.")
                return

            merged = unary_union(polys)
            buf = self.spin_buffer.value()
            if buf > 0:
                if merged.geom_type == "Polygon":
                    merged = metric_buffer(merged, buf, crs)
                else:
                    merged = unary_union(
                        [metric_buffer(g, buf, crs) for g in merged.geoms]
                    )
                merged = unary_union(merged)
            if self.chk_holes.isChecked():
                merged = fill_holes(merged)

            if merged.geom_type == "Polygon":
                self.final_geoms = [merged]
            else:
                self.final_geoms = [g for g in merged.geoms if not g.is_empty]

            h, w = self.preview_rgb.shape[:2]
            preview_tf = self.orig_transform * Affine.scale(
                self.orig_width / w, self.orig_height / h
            )
            self.preview_kml_mask = rasterize(
                [(g, 1) for g in self.final_geoms],
                out_shape=(h, w),
                transform=preview_tf,
                fill=0,
                dtype=np.uint8,
            )
            self.update_display()
            self.btn_export.setEnabled(True)
            QMessageBox.information(self, "Готово", f"Найдено очагов: {len(self.final_geoms)}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка расчёта", str(exc))
        finally:
            self.btn_calc.setText("Рассчитать полигоны (KML)")

    def export_kml(self) -> None:
        if not self.final_geoms:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить KML", "dodder_zones.kml", "KML (*.kml)"
        )
        if not path:
            return
        try:
            wgs = [to_wgs84(g, self.orig_crs) for g in self.final_geoms]
            write_kml(path, wgs)
            QMessageBox.information(self, "Готово", "KML сохранён.")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    DodderDetectorApp().show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
