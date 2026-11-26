"""English-only reusable camera, histogram, and image-view widgets."""

from __future__ import annotations

import math
import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QPixmap, QPainter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QComboBox, QSlider, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QSizePolicy)
import pyqtgraph as pg

from .backends import STOP_KEYS
from .geometry import grayscale_qimage, orientation_transform
from .imaging import HistogramData
from .models import ControlInfo, Orientation

pg.setConfigOptions(imageAxisOrder="row-major", antialias=False, background="#10151d", foreground="#9aa8ba")


class ImageView(QGraphicsView):
    cursor_pixel = Signal(object)
    zoom_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setObjectName("imageViewport")
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.scene().addItem(self.item)
        self.setBackgroundBrush(QColor("#080b10"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._shape = (0, 0)
        self._fit = True
        self._orientation = Orientation()
        self.placeholder = QLabel("No image yet\n\nConnect a camera and start acquisition, or select Synthetic demo.", self.viewport())
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #718096; font-size: 14px; background: transparent;")
        self.placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_image(self, image: np.ndarray) -> None:
        changed = self._shape != image.shape
        self._shape = image.shape
        self.item.setPixmap(QPixmap.fromImage(grayscale_qimage(image)))
        self.placeholder.hide()
        if changed:
            self.set_orientation(self._orientation)
        if self._fit:
            self.fit_image()

    def set_orientation(self, orientation: Orientation) -> None:
        self._orientation = orientation
        height, width = self._shape
        if not width:
            return
        self.item.setTransform(orientation_transform(width, height, orientation))
        self.scene().setSceneRect(self.item.sceneBoundingRect())
        if self._fit:
            self.fit_image()

    def fit_image(self) -> None:
        self._fit = True
        if self._shape[0]:
            self.fitInView(self.item.sceneBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self.zoom_changed.emit(self.transform().m11())

    def native_size(self) -> None:
        self._fit = False
        self.resetTransform()
        self.centerOn(self.item)
        self.zoom_changed.emit(1.0)

    def wheelEvent(self, event) -> None:
        if not self._shape[0]:
            return
        self._fit = False
        factor = 1.2 ** (event.angleDelta().y() / 120)
        target = min(max(self.transform().m11() * factor, 0.02), 64)
        self.scale(target / self.transform().m11(), target / self.transform().m11())
        self.zoom_changed.emit(self.transform().m11())
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.placeholder.setGeometry(self.viewport().rect())
        if self._fit:
            self.fit_image()

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        point = self.item.mapFromScene(self.mapToScene(event.position().toPoint()))
        x, y = math.floor(point.x()), math.floor(point.y())
        height, width = self._shape
        self.cursor_pixel.emit((x, y) if 0 <= x < width and 0 <= y < height else None)

    def leaveEvent(self, event) -> None:
        self.cursor_pixel.emit(None)
        super().leaveEvent(event)


class HistogramWidget(pg.PlotWidget):
    levels_edited = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setObjectName("rawHistogram")
        self.setMinimumHeight(165)
        self.setMaximumHeight(235)
        self.setLabel("bottom", "Raw intensity", units="DN")
        self.getAxis("bottom").enableAutoSIPrefix(False)
        self.setLabel("left", "Sample count")
        self.showGrid(x=True, y=True, alpha=0.12)
        self.setMenuEnabled(False)
        self.getViewBox().setMouseEnabled(x=True, y=False)
        self.curve = pg.PlotCurveItem([0, 1], [0], stepMode="center", fillLevel=0,
            brush=pg.mkBrush(50, 135, 170, 100), pen=pg.mkPen("#54abc5", width=1))
        self.addItem(self.curve)
        self.region = pg.LinearRegionItem(values=(0, 4095), bounds=(0, 65535),
            swapMode="block", brush=pg.mkBrush(77, 164, 181, 22),
            pen=pg.mkPen("#a2dbe7", width=2), hoverPen=pg.mkPen("#ffffff", width=2))
        self.addItem(self.region)
        self.region.sigRegionChanged.connect(self._edited)
        self._updating = False
        self._maximum = None

    def _edited(self) -> None:
        if self._updating:
            return
        black, white = self.region.getRegion()
        if white-black >= 0.01:
            self.levels_edited.emit(float(black), float(white))

    def update_data(self, data: HistogramData, black: float, white: float, maximum: int) -> None:
        self._updating = True
        try:
            self.curve.setData(data.edges, data.counts)
            if self._maximum != maximum:
                self._maximum = maximum
                self.region.setBounds((0, maximum))
                self.getViewBox().setLimits(xMin=0, xMax=maximum+1, minXRange=1)
                self.setXRange(0, maximum, padding=0.01)
            self.region.setRegion((black, white))
        finally:
            self._updating = False

    def full_axis(self, maximum: int) -> None:
        self.setXRange(0, maximum, padding=0.01)


class NumericControl(QWidget):
    edited = Signal(str, object)
    TIME_UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "\u00b5s": 1e-6, "\u03bcs": 1e-6, "ns": 1e-9}

    def __init__(self, key: str, label: str, logarithmic: bool = False):
        super().__init__()
        self.key = key
        self.logarithmic = logarithmic
        self.info: ControlInfo | None = None
        self.factor = 1.0
        self._updating = False
        self.setObjectName(key + "Control")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addStretch()
        self.spin = QDoubleSpinBox()
        self.spin.setObjectName(key + "Value")
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(115)
        self.spin.setMaximumWidth(155)
        row.addWidget(self.spin)
        self.units = QComboBox()
        self.units.setObjectName(key + "Units")
        self.units.setMaximumWidth(75)
        row.addWidget(self.units)
        layout.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setObjectName(key + "Slider")
        layout.addWidget(self.slider)
        self.readback = QLabel()
        self.readback.setObjectName("readback")
        self.readback.setWordWrap(True)
        layout.addWidget(self.readback)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._send)
        self.spin.editingFinished.connect(self._spin_edited)
        self.slider.valueChanged.connect(self._slider_edited)
        self.units.currentIndexChanged.connect(self._unit_changed)

    def _unit_changed(self) -> None:
        if self.info and not self._updating:
            self.set_info(self.info, force=True)

    def set_info(self, info: ControlInfo, force: bool = False) -> None:
        self.info = info
        self.readback.setText(f"{info.name}: {float(info.value):.6g} {info.unit or 'native units'}")
        enabled = info.writable or info.key in STOP_KEYS
        self.spin.setEnabled(enabled)
        self.slider.setEnabled(enabled and info.maximum > info.minimum)
        self.setToolTip(f"{info.name}: {info.minimum:g} to {info.maximum:g} {info.unit}. "
                        + ("Acquisition pauses briefly for this change." if info.key in STOP_KEYS else ""))
        if not force and (self.slider.isSliderDown() or self.spin.hasFocus()):
            return
        self._updating = True
        blockers = [QSignalBlocker(self.spin), QSignalBlocker(self.slider), QSignalBlocker(self.units)]
        try:
            if self.key == "exposure" and info.unit in self.TIME_UNITS:
                previous = self.units.currentText()
                if [self.units.itemText(i) for i in range(self.units.count())] != ["us", "ms", "s"]:
                    self.units.clear()
                    self.units.addItems(["us", "ms", "s"])
                    self.units.setCurrentText(previous if previous in ("us", "ms", "s") else "ms")
                self.factor = self.TIME_UNITS[self.units.currentText()] / self.TIME_UNITS[info.unit]
            else:
                self.units.clear()
                self.units.addItem(info.unit or "native")
                self.factor = 1.0
            self.units.setEnabled(self.key == "exposure" and info.unit in self.TIME_UNITS)
            self.spin.setDecimals(0 if info.kind == "int" else 6)
            self.spin.setRange(info.minimum/self.factor, info.maximum/self.factor)
            self.spin.setSingleStep(max(1e-6, (info.increment or (info.maximum-info.minimum)/1000 or 1)/self.factor))
            self.spin.setValue(float(info.value)/self.factor)
            self.slider.setValue(self._to_slider(float(info.value)))
        finally:
            del blockers
            self._updating = False

    def _from_slider(self, position: int) -> float:
        assert self.info is not None
        low, high = self.info.minimum, self.info.maximum
        t = position/1000
        if self.logarithmic and low > 0 and high > low:
            return math.exp(math.log(low) + t*math.log(high/low))
        return low + t*(high-low)

    def _to_slider(self, value: float) -> int:
        assert self.info is not None
        low, high = self.info.minimum, self.info.maximum
        if high <= low:
            return 0
        if self.logarithmic and low > 0:
            fraction = math.log(max(low, value)/low)/math.log(high/low)
        else:
            fraction = (value-low)/(high-low)
        return round(min(max(fraction, 0), 1)*1000)

    def _slider_edited(self, position: int) -> None:
        if self._updating or self.info is None:
            return
        blocker = QSignalBlocker(self.spin)
        self.spin.setValue(self._from_slider(position)/self.factor)
        del blocker
        self.timer.start()

    def _spin_edited(self) -> None:
        if self._updating or self.info is None:
            return
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(self._to_slider(self.spin.value()*self.factor))
        del blocker
        self.timer.stop()
        self._send()

    def _send(self) -> None:
        if self.info is not None and not self._updating:
            value = self.spin.value()*self.factor
            self.edited.emit(self.key, int(round(value)) if self.info.kind == "int" else value)
