#!/usr/bin/env python3
"""CuscutaDetector — standalone RGB/HSV detector for dodder (Cuscuta) foci.

Loads an RGB GeoTIFF orthophoto, builds an HSV colour mask, allows brush edits,
and exports buffered polygons as KML for agrodrones.
"""
from __future__ import annotations

import sys

import cv2
import fiona
import geopandas as gpd
import numpy as np
import rasterio
from PyQt6.QtCore import Qt, pyqtSignal, QRect
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
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes, sieve
from rasterio.transform import Affine
from shapely.geometry import MultiPolygon, Polygon

fiona.drvsupport.supported_drivers["KML"] = "rw"
fiona.drvsupport.supported_drivers["LIBKML"] = "rw"

APP_TITLE = "CuscutaDetector"
APP_VERSION = "1.0.0"


class InteractivePreview(QWidget):
    mouse_dragged = pyqtSignal(int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet("background-color: #222; border: 1px solid #555;")
        self.setMouseTracking(False)
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

        canvas_rect = self.rect()
        scaled_size = self._raw_qimage.size().scaled(
            canvas_rect.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        x = (canvas_rect.width() - scaled_size.width()) // 2
        y = (canvas_rect.height() - scaled_size.height()) // 2
        self.image_rect = QRect(x, y, scaled_size.width(), scaled_size.height())
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
        img_x = pos.x() - self.image_rect.x()
        img_y = pos.y() - self.image_rect.y()
        scale_x = self.preview_shape[1] / self.image_rect.width()
        scale_y = self.preview_shape[0] / self.image_rect.height()
        real_x = int(img_x * scale_x)
        real_y = int(img_y * scale_y)
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.mouse_dragged.emit(real_x, real_y, 1)
        elif event.buttons() & Qt.MouseButton.RightButton:
            self.mouse_dragged.emit(real_x, real_y, 2)


def hsv_mask(rgb: np.ndarray, sensitivity: int) -> np.ndarray:
    """Yellow–orange Cuscuta mask; sensitivity 0..100 (same thresholds as project detect)."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h_min = int(max(5, 25 - sensitivity / 5.0))
    h_max = int(min(60, 35 + sensitivity / 5.0))
    s_min = int(max(20, 200 - sensitivity * 1.8))
    v_min = int(max(20, 200 - sensitivity * 1.8))
    mask = cv2.inRange(hsv, np.array([h_min, s_min, v_min]), np.array([h_max, 255, 255]))
    out = np.zeros(rgb.shape[:2], dtype=np.uint8)
    out[mask == 255] = 1
    return out


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
        self.final_gdf = None
        self.current_preview_bytes = None
        self.orig_bytes = None
        self._init_ui()

    def _init_ui(self) -> None:
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        settings = QWidget()
        settings.setFixedWidth(350)
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 0, 0)

        load = QGroupBox("1. Загрузка RGB")
        load_l = QVBoxLayout()
        self.btn_load_rgb = QPushButton("Загрузить RGB-ортофотоплан (GeoTIFF)")
        self.btn_load_rgb.clicked.connect(self.load_rgb)
        self.lbl_rgb_status = QLabel("RGB: не загружен")
        hint = QLabel("Только RGB. Детекция по цвету в пространстве HSV.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        load_l.addWidget(self.btn_load_rgb)
        load_l.addWidget(self.lbl_rgb_status)
        load_l.addWidget(hint)
        load.setLayout(load_l)
        layout.addWidget(load)

        search = QGroupBox("2. Интерактивный поиск (HSV)")
        search_l = QFormLayout()
        self.slider_thresh = QSlider(Qt.Orientation.Horizontal)
        self.slider_thresh.setRange(0, 100)
        self.slider_thresh.setValue(40)
        self.slider_thresh.valueChanged.connect(self.calculate_base_mask)
        self.slider_brush = QSlider(Qt.Orientation.Horizontal)
        self.slider_brush.setRange(1, 100)
        self.slider_brush.setValue(20)
        self.btn_clear_brush = QPushButton("Сбросить ручные правки")
        self.btn_clear_brush.clicked.connect(self.clear_user_mask)
        search_l.addRow("Чувствительность:", self.slider_thresh)
        search_l.addRow("Размер кисти:", self.slider_brush)
        search_l.addRow("", QLabel("<i>ЛКМ — добавить · ПКМ — стереть</i>"))
        search_l.addRow("", self.btn_clear_brush)
        search.setLayout(search_l)
        layout.addWidget(search)

        export = QGroupBox("3. Расчёт и экспорт KML")
        export_l = QFormLayout()
        self.spin_resolution = QDoubleSpinBox()
        self.spin_resolution.setValue(0.5)
        self.spin_resolution.setSingleStep(0.1)
        self.spin_sieve = QSpinBox()
        self.spin_sieve.setRange(1, 10000)
        self.spin_sieve.setValue(50)
        self.spin_buffer = QDoubleSpinBox()
        self.spin_buffer.setRange(0, 50)
        self.spin_buffer.setValue(2.0)
        self.chk_remove_holes = QCheckBox("Заливка внутренних пустот")
        self.chk_remove_holes.setChecked(True)
        self.btn_calc = QPushButton("Рассчитать полигоны (KML)")
        self.btn_calc.clicked.connect(self.calculate_polygons)
        self.btn_calc.setEnabled(False)
        self.btn_export = QPushButton("Сохранить файл KML")
        self.btn_export.clicked.connect(self.export_kml)
        self.btn_export.setEnabled(False)
        export_l.addRow("Разрешение (м):", self.spin_resolution)
        export_l.addRow("Мин. размер пятна (пикс.):", self.spin_sieve)
        export_l.addRow("Буфер (м):", self.spin_buffer)
        export_l.addRow("", self.chk_remove_holes)
        export_l.addRow("", self.btn_calc)
        export_l.addRow("", self.btn_export)
        export.setLayout(export_l)
        layout.addWidget(export)
        layout.addStretch()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        orig_w = QWidget()
        orig_l = QVBoxLayout(orig_w)
        orig_l.setContentsMargins(0, 0, 0, 0)
        ol = QLabel("Оригинальный снимок")
        ol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_l.addWidget(ol)
        self.canvas_orig = InteractivePreview()
        orig_l.addWidget(self.canvas_orig, 1)

        mask_w = QWidget()
        mask_l = QVBoxLayout(mask_w)
        mask_l.setContentsMargins(0, 0, 0, 0)
        ml = QLabel("Результат обнаружения (HSV)")
        ml.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mask_l.addWidget(ml)
        self.canvas_mask = InteractivePreview()
        mask_l.addWidget(self.canvas_mask, 1)

        splitter.addWidget(orig_w)
        splitter.addWidget(mask_w)
        self.canvas_orig.mouse_dragged.connect(self.apply_brush)
        self.canvas_mask.mouse_dragged.connect(self.apply_brush)

        main_layout.addWidget(settings)
        main_layout.addWidget(splitter, 1)

    def load_rgb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "RGB растр", "", "TIFF (*.tif *.tiff)")
        if not path:
            return
        self.rgb_path = path
        self.lbl_rgb_status.setText(f"RGB: {path.split('/')[-1].split(chr(92))[-1]}")
        self.btn_calc.setEnabled(True)
        self.prepare_realtime_data()

    def prepare_realtime_data(self) -> None:
        if not self.rgb_path:
            return
        target_size = 1000.0
        with rasterio.open(self.rgb_path) as src:
            self.orig_transform = src.transform
            self.orig_width = src.width
            self.orig_height = src.height
            self.orig_crs = src.crs
            scale = min(1.0, target_size / max(src.width, src.height))
            out_shape = (int(max(1, src.height * scale)), int(max(1, src.width * scale)))
            img = src.read((1, 2, 3), out_shape=out_shape, resampling=Resampling.bilinear)
            nodata_mask = src.read_masks(1, out_shape=out_shape, resampling=Resampling.nearest)
            self.valid_mask = nodata_mask > 0
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
        self.final_gdf = None
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
        final_mask = np.where(
            self.user_mask == 1, 1, np.where(self.user_mask == 2, 0, self.base_mask)
        )
        display = self.preview_rgb.copy()
        display[final_mask == 1] = [255, 0, 0]
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

    @staticmethod
    def fill_holes(geom):
        if geom.geom_type == "Polygon":
            return Polygon(geom.exterior)
        if geom.geom_type == "MultiPolygon":
            return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
        return geom

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
                target_res_units = (
                    target_res / 111320.0 if src.crs and src.crs.is_geographic else target_res
                )
                scale_factor = (
                    pixel_size / target_res_units if target_res_units > pixel_size else 1.0
                )
                new_width = max(1, int(src.width * scale_factor))
                new_height = max(1, int(src.height * scale_factor))
                rgb_data = src.read(
                    out_shape=(src.count, new_height, new_width), resampling=Resampling.bilinear
                )
                nodata_mask = src.read_masks(
                    1, out_shape=(new_height, new_width), resampling=Resampling.nearest
                )
                valid = nodata_mask > 0
                rgb_hwc = np.transpose(rgb_data, (1, 2, 0))
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
                    src.width / new_width, src.height / new_height
                )
                crs = src.crs

            final_mask = hsv_mask(rgb_hwc, sensitivity)
            final_mask[~valid] = 0
            if self.user_mask is not None and np.any(self.user_mask > 0):
                hires_user = cv2.resize(
                    self.user_mask, (new_width, new_height), interpolation=cv2.INTER_NEAREST
                )
                final_mask = np.where(
                    hires_user == 1, 1, np.where(hires_user == 2, 0, final_mask)
                )

            sieved = sieve(final_mask, size=self.spin_sieve.value(), connectivity=4)
            geoms = [
                {"properties": {"val": v}, "geometry": s}
                for s, v in shapes(sieved, mask=sieved == 1, transform=new_transform)
            ]
            if not geoms:
                QMessageBox.warning(self, "Пусто", "Очаги не найдены. Измените чувствительность.")
                return

            gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)
            buffer_m = self.spin_buffer.value()
            if buffer_m > 0:
                if gdf.crs.is_geographic:
                    gdf = gdf.to_crs("EPSG:3857")
                gdf.geometry = gdf.geometry.buffer(buffer_m)
                gdf = gdf.dissolve()
                if crs and crs.is_geographic:
                    gdf = gdf.to_crs(crs)
            if self.chk_remove_holes.isChecked():
                gdf.geometry = gdf.geometry.apply(self.fill_holes)

            self.final_gdf = gdf
            h, w = self.preview_rgb.shape[:2]
            preview_transform = self.orig_transform * Affine.scale(
                self.orig_width / w, self.orig_height / h
            )
            self.preview_kml_mask = rasterize(
                [(geom, 1) for geom in self.final_gdf.geometry],
                out_shape=(h, w),
                transform=preview_transform,
                fill=0,
                dtype=np.uint8,
            )
            self.update_display()
            self.btn_export.setEnabled(True)
            QMessageBox.information(
                self, "Готово", f"Найдено очагов: {len(self.final_gdf)}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка расчёта", str(exc))
        finally:
            self.btn_calc.setText("Рассчитать полигоны (KML)")

    def export_kml(self) -> None:
        if self.final_gdf is None or self.final_gdf.empty:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить KML", "dodder_zones.kml", "KML (*.kml)"
        )
        if not path:
            return
        try:
            self.final_gdf.to_crs("EPSG:4326")[["geometry"]].to_file(path, driver="KML")
            QMessageBox.information(self, "Готово", "KML сохранён.")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    window = DodderDetectorApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
