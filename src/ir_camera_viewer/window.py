"""The camera viewer window. Hardware and disk work remain in workers."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import base64
import logging
import platform
import sys
import time

from PySide6.QtCore import QByteArray, Qt, QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QToolButton, QComboBox, QCheckBox, QDoubleSpinBox, QSlider,
    QScrollArea, QSplitter, QFrame, QLineEdit, QFileDialog, QDialog, QDialogButtonBox,
    QPlainTextEdit, QMessageBox, QStatusBar)

from . import __version__
from .controller import CameraController
from .imaging import ImageProcessor, RenderPacket
from .models import DisplayOptions, Orientation, FrameData, LatestSlot, utc_now
from .settings import (atomic_json, load_preferences, save_display_preset, load_display_preset)
from .snapshots import SnapshotRequest, SnapshotService
from .widgets import HistogramWidget, ImageView, NumericControl

LOG = logging.getLogger(__name__)

STYLE = """
QWidget { background: #141b25; color: #dbe5ef; font-family: 'Segoe UI', 'Arial'; font-size: 12px; }
QMainWindow, QStatusBar { background: #10161f; }
QPushButton, QToolButton, QComboBox { background: #223042; border: 1px solid #344458; border-radius: 5px; padding: 6px 9px; }
QPushButton:hover, QToolButton:hover { background: #2d4055; border-color: #55b4cb; }
QPushButton:disabled, QToolButton:disabled { color: #637184; background: #1b2532; border-color: #263344; }
QPushButton:checked { background: #214c61; border-color: #58bad0; }
QPushButton#snapshotButton { background: #126b81; border-color: #3196ac; font-weight: 600; }
QLineEdit, QDoubleSpinBox, QPlainTextEdit { background: #0d141e; border: 1px solid #344458; border-radius: 4px; padding: 5px; selection-background-color: #245f79; }
QLineEdit:focus, QDoubleSpinBox:focus { border-color: #54b5cc; }
QDoubleSpinBox:disabled { color: #6b7889; }
QComboBox QAbstractItemView { background: #172230; selection-background-color: #285b72; }
QLabel#muted, QLabel#readback { color: #8595aa; font-size: 11px; background: transparent; }
QLabel#brand { font-size: 15px; font-weight: 700; color: #c5edf6; padding: 0 8px 0 0; }
QLabel#stateBadge { color: #74d5c5; font-weight: 700; background: transparent; padding: 3px; }
QFrame#section { background: #19222f; border: 1px solid #2b3849; border-radius: 6px; }
QWidget#sectionContent { background: #19222f; }
QToolButton#sectionHeader { text-align: left; border: none; background: transparent; font-weight: 600; padding: 9px; }
QScrollArea { border: none; background: #141b25; }
QSplitter::handle { background: #263344; width: 4px; }
QSlider::groove:horizontal { height: 4px; background: #344458; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: #64b7cd; border-radius: 5px; }
QCheckBox { background: transparent; spacing: 6px; }
QMenuBar { background: #10161f; }
QMenuBar::item:selected, QMenu::item:selected { background: #285b72; }
QMenu { background: #19222f; border: 1px solid #344458; padding: 4px; }
QToolTip { background: #223042; color: #e5eef5; border: 1px solid #4b647e; padding: 5px; }
"""


def section(title: str, content: QWidget, expanded: bool = True) -> QFrame:
    frame = QFrame()
    frame.setObjectName("section")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 5)
    layout.setSpacing(0)
    header = QToolButton()
    header.setObjectName("sectionHeader")
    header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    header.setText(title)
    header.setCheckable(True)
    header.setChecked(expanded)
    header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
    header.setSizePolicy(header.sizePolicy().horizontalPolicy().Expanding, header.sizePolicy().verticalPolicy())
    content.setObjectName("sectionContent")
    content.setVisible(expanded)
    def toggle(checked: bool) -> None:
        content.setVisible(checked)
        header.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
    header.toggled.connect(toggle)
    layout.addWidget(header)
    layout.addWidget(content)
    return frame


class MainWindow(QMainWindow):
    def __init__(self, settings_path: Path, demo: bool = False):
        super().__init__()
        self.setWindowTitle("Infrared Camera Viewer")
        self.setMinimumSize(1040, 720)
        self.resize(1360, 880)
        self.setStyleSheet(STYLE)
        self.settings_path = settings_path
        self.preferences = load_preferences(settings_path)
        self.options = DisplayOptions(**self.preferences.get("display", {}))
        self.orientation = Orientation(**self.preferences.get("orientation", {}))
        self.cti_path = str(self.preferences.get("cti_path", ""))
        self.frames: LatestSlot[FrameData] = LatestSlot()
        self.processor = ImageProcessor(self.frames, self.options)
        self.camera = CameraController(self.frames)
        self.saver = SnapshotService()
        self.packet: RenderPacket | None = None
        self._packet_sequence = -1
        self._expected_revision = 0
        self._updating = False
        self._device = None
        self._streaming = False
        self._closing = False
        self._shutdown_complete = False
        self._auto_connect_demo = demo
        self._diagnostics = "Camera SDK has not been initialized."
        self._last_format = None
        self._render_count = 0
        self._fps_time = time.monotonic()
        self._last_complete = 0
        self._acquisition_fps = 0.0
        self._last_state_time = time.monotonic()
        self._last_snapshot: Path | None = None
        self._cursor_position = None
        self._stream_started_at = time.monotonic()
        self._waiting_warned = False
        self._build_ui(demo)
        self._build_menus()
        self._connect_signals()
        geometry = self.preferences.get("geometry")
        if isinstance(geometry, str):
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except (ValueError, UnicodeError):
                LOG.info("Ignoring invalid stored window geometry")
        self.camera.start()
        self.processor.start()
        self.render_timer = QTimer(self)
        self.render_timer.setInterval(30)
        self.render_timer.timeout.connect(self._poll)
        self.render_timer.start()
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(100)
        self.shutdown_timer.timeout.connect(self._finish_shutdown)
        QTimer.singleShot(0, self.refresh_devices)

    @staticmethod
    def _button(text: str, name: str = "") -> QPushButton:
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        return button

    def _build_ui(self, demo: bool) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(9)
        row = QHBoxLayout()
        brand = QLabel("IR VIEWER")
        brand.setObjectName("brand")
        row.addWidget(brand)
        self.source = QComboBox()
        self.source.setObjectName("sourceSelector")
        self.source.addItem("Vimba X camera", "vimba")
        self.source.addItem("Synthetic demo", "demo")
        self.source.setCurrentIndex(1 if demo or self.preferences.get("source") == "demo" else 0)
        self.source.setMinimumWidth(150)
        row.addWidget(self.source)
        self.devices = QComboBox()
        self.devices.setObjectName("deviceSelector")
        self.devices.setMinimumWidth(210)
        row.addWidget(self.devices, 1)
        self.refresh_button = self._button("Refresh", "refreshButton")
        self.connect_button = self._button("Connect", "connectButton")
        self.start_button = self._button("Start", "startButton")
        self.freeze_button = self._button("Freeze", "freezeButton")
        self.freeze_button.setCheckable(True)
        self.snapshot_button = self._button("Snapshot", "snapshotButton")
        for button in (self.refresh_button, self.connect_button, self.start_button,
                       self.freeze_button, self.snapshot_button):
            row.addWidget(button)
        self.connect_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.freeze_button.setEnabled(False)
        self.snapshot_button.setEnabled(False)
        layout.addLayout(row)

        self.error_bar = QFrame()
        self.error_bar.setStyleSheet("QFrame { background: #442530; border-radius: 5px; } QLabel { background: transparent; color: #ffcad1; }")
        error_layout = QHBoxLayout(self.error_bar)
        error_layout.setContentsMargins(10, 6, 7, 6)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        error_layout.addWidget(self.error_label, 1)
        dismiss = self._button("Dismiss")
        dismiss.clicked.connect(self.error_bar.hide)
        error_layout.addWidget(dismiss)
        self.error_bar.hide()
        layout.addWidget(self.error_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(5)
        preview_top = QHBoxLayout()
        self.state_badge = QLabel("READY")
        self.state_badge.setObjectName("stateBadge")
        self.frame_label = QLabel("No frame")
        self.frame_label.setObjectName("muted")
        preview_top.addWidget(self.state_badge)
        preview_top.addStretch()
        preview_top.addWidget(self.frame_label)
        preview_layout.addLayout(preview_top)
        self.view = ImageView()
        preview_layout.addWidget(self.view, 1)
        navigation = QHBoxLayout()
        fit = self._button("Fit")
        native = self._button("1:1")
        left = self._button("Rotate -90")
        right = self._button("Rotate +90")
        self.angle = QDoubleSpinBox()
        self.angle.setObjectName("rotationAngle")
        self.angle.setRange(-3600, 3600)
        self.angle.setDecimals(1)
        self.angle.setSuffix(" deg")
        self.angle.setValue(self.orientation.angle)
        self.angle.setKeyboardTracking(False)
        self.flip_h = QCheckBox("Flip H")
        self.flip_h.setObjectName("flipHorizontal")
        self.flip_h.setChecked(self.orientation.flip_horizontal)
        self.flip_v = QCheckBox("Flip V")
        self.flip_v.setObjectName("flipVertical")
        self.flip_v.setChecked(self.orientation.flip_vertical)
        reset = self._button("Reset")
        fullscreen = self._button("Fullscreen")
        for widget in (fit, native, left, right, self.angle, self.flip_h, self.flip_v, reset):
            navigation.addWidget(widget)
        navigation.addStretch()
        navigation.addWidget(fullscreen)
        preview_layout.addLayout(navigation)
        self.view.set_orientation(self.orientation)
        fit.clicked.connect(self.view.fit_image)
        native.clicked.connect(self.view.native_size)
        left.clicked.connect(lambda: self.angle.setValue(self.angle.value()-90))
        right.clicked.connect(lambda: self.angle.setValue(self.angle.value()+90))
        self.angle.valueChanged.connect(self._orientation_changed)
        self.flip_h.toggled.connect(self._orientation_changed)
        self.flip_v.toggled.connect(self._orientation_changed)
        reset.clicked.connect(self._reset_orientation)
        fullscreen.clicked.connect(self.toggle_fullscreen)
        self.splitter.addWidget(preview)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(345)
        sidebar = QWidget()
        sidebar.setMaximumWidth(520)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(6, 0, 0, 0)
        side.setSpacing(9)
        side.addWidget(section("CAMERA CONTROLS", self._camera_panel()))
        side.addWidget(section("HISTOGRAM AND DISPLAY", self._display_panel()))
        side.addWidget(section("SNAPSHOT SETTINGS", self._snapshot_panel(), False))
        side.addStretch()
        scroll.setWidget(sidebar)
        self.splitter.addWidget(scroll)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([960, 380])
        layout.addWidget(self.splitter, 1)

        status = QStatusBar()
        self.setStatusBar(status)
        self.connection_label = QLabel("Not connected")
        self.connection_label.setObjectName("muted")
        self.cursor_label = QLabel("")
        self.cursor_label.setObjectName("muted")
        self.performance_label = QLabel("")
        self.performance_label.setObjectName("muted")
        self.zoom_label = QLabel("Fit")
        self.zoom_label.setObjectName("muted")
        status.addWidget(self.connection_label, 1)
        status.addPermanentWidget(self.cursor_label)
        status.addPermanentWidget(self.performance_label)
        status.addPermanentWidget(self.zoom_label)

    def _camera_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 10, 7)
        self.camera_hint = QLabel("Connect a camera to discover its controls.")
        self.camera_hint.setWordWrap(True)
        self.camera_hint.setObjectName("muted")
        layout.addWidget(self.camera_hint)
        self.numeric: dict[str, NumericControl] = {}
        self.enums: dict[str, QComboBox] = {}
        self.enum_rows: dict[str, QWidget] = {}
        for key, title in (("exposure", "Exposure"), ("gain", "Gain")):
            control = NumericControl(key, title, logarithmic=key == "exposure")
            control.edited.connect(self._write_control)
            layout.addWidget(control)
            self.numeric[key] = control
        layout.addWidget(self._enum_row("exposure_auto", "Auto exposure"))
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(10, 0, 10, 6)
        advanced_layout.addWidget(self._enum_row("gain_auto", "Auto gain"))
        self.fps_enable = QCheckBox("Enable frame-rate control")
        self.fps_enable.toggled.connect(lambda value: self._write_control("fps_enabled", value))
        advanced_layout.addWidget(self.fps_enable)
        fps = NumericControl("fps", "Frame rate")
        fps.edited.connect(self._write_control)
        self.numeric["fps"] = fps
        advanced_layout.addWidget(fps)
        advanced_layout.addWidget(self._enum_row("pixel_format", "Pixel format"))
        for key, title in (("width", "ROI width"), ("height", "ROI height"),
                           ("offset_x", "ROI offset X"), ("offset_y", "ROI offset Y")):
            control = NumericControl(key, title)
            control.edited.connect(self._write_control)
            self.numeric[key] = control
            advanced_layout.addWidget(control)
        advanced_layout.addWidget(self._enum_row("demo_pattern", "Demo pattern"))
        hint = QLabel("ROI and pixel-format changes briefly pause acquisition. Calibration and NUC settings are not modified.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        advanced_layout.addWidget(hint)
        layout.addWidget(section("Advanced acquisition", advanced, False))
        for control in self.numeric.values():
            control.hide()
        for row in self.enum_rows.values():
            row.hide()
        self.fps_enable.hide()
        return panel

    def _enum_row(self, key: str, title: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.addWidget(QLabel(title))
        combo = QComboBox()
        combo.setObjectName(key + "Selector")
        combo.setMinimumWidth(135)
        layout.addWidget(combo, 1)
        combo.currentTextChanged.connect(lambda value, key=key: self._write_control(key, value))
        self.enums[key] = combo
        self.enum_rows[key] = row
        return row

    def _display_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 10, 8)
        mode_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.setObjectName("contrastMode")
        for text, data in (("Manual", "manual"), ("Auto once", "once"), ("Continuous auto", "continuous")):
            self.mode.addItem(text, data)
        self.mode.setCurrentIndex(self.mode.findData(self.options.mode))
        mode_row.addWidget(self.mode, 1)
        once = self._button("Auto once", "autoOnceButton")
        once.clicked.connect(lambda: self._change_display(mode="once"))
        full = self._button("Full range", "fullRangeButton")
        full.clicked.connect(self._full_range)
        mode_row.addWidget(once)
        mode_row.addWidget(full)
        layout.addLayout(mode_row)
        self.histogram = HistogramWidget()
        self.histogram.levels_edited.connect(lambda black, white: self._change_display(black=black, white=white, mode="manual"))
        layout.addWidget(self.histogram)
        levels = QHBoxLayout()
        self.black = QDoubleSpinBox()
        self.black.setObjectName("blackLevel")
        self.white = QDoubleSpinBox()
        self.white.setObjectName("whiteLevel")
        for title, spin in (("Black", self.black), ("White", self.white)):
            spin.setRange(0, 65535)
            spin.setDecimals(2)
            spin.setKeyboardTracking(False)
            levels.addWidget(QLabel(title))
            levels.addWidget(spin, 1)
            spin.editingFinished.connect(self._manual_levels)
        self.black.setValue(self.options.black)
        self.white.setValue(self.options.white)
        layout.addLayout(levels)
        gamma_row = QHBoxLayout()
        gamma_row.addWidget(QLabel("Gamma"))
        self.gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.gamma_slider.setRange(10, 500)
        self.gamma_slider.setValue(round(self.options.gamma*100))
        self.gamma_slider.setObjectName("gammaSlider")
        gamma_row.addWidget(self.gamma_slider, 1)
        self.gamma = QDoubleSpinBox()
        self.gamma.setObjectName("displayGamma")
        self.gamma.setRange(0.1, 5)
        self.gamma.setDecimals(2)
        self.gamma.setSingleStep(0.05)
        self.gamma.setValue(self.options.gamma)
        self.gamma.setKeyboardTracking(False)
        gamma_row.addWidget(self.gamma)
        layout.addLayout(gamma_row)
        toggles = QHBoxLayout()
        self.invert = QCheckBox("Invert grayscale")
        self.invert.setObjectName("invertGrayscale")
        self.invert.setChecked(self.options.invert)
        toggles.addWidget(self.invert)
        toggles.addStretch()
        reset = self._button("Reset display")
        reset.clicked.connect(lambda: self._change_display(gamma=1.0, invert=False, mode="continuous"))
        toggles.addWidget(reset)
        layout.addLayout(toggles)
        self.histogram_stats = QLabel("Histogram uses original DN values, before display mapping.")
        self.histogram_stats.setObjectName("muted")
        self.histogram_stats.setWordWrap(True)
        layout.addWidget(self.histogram_stats)
        auto = QWidget()
        form = QFormLayout(auto)
        form.setContentsMargins(10, 0, 10, 4)
        self.percentile_low = QDoubleSpinBox()
        self.percentile_high = QDoubleSpinBox()
        self.smoothing = QDoubleSpinBox()
        for spin, value in ((self.percentile_low, self.options.low_percentile),
                            (self.percentile_high, self.options.high_percentile)):
            spin.setRange(0, 100)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.setSuffix(" %")
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(self._auto_options)
        self.smoothing.setRange(0, 10)
        self.smoothing.setDecimals(2)
        self.smoothing.setValue(self.options.smoothing_seconds)
        self.smoothing.setSuffix(" s")
        self.smoothing.setKeyboardTracking(False)
        self.smoothing.editingFinished.connect(self._auto_options)
        form.addRow("Lower percentile", self.percentile_low)
        form.addRow("Upper percentile", self.percentile_high)
        form.addRow("Auto smoothing", self.smoothing)
        layout.addWidget(section("Automatic contrast options", auto, False))
        self.mode.currentIndexChanged.connect(lambda: self._change_display(mode=self.mode.currentData()))
        self.gamma.valueChanged.connect(lambda value: self._change_display(gamma=value))
        self.gamma_slider.valueChanged.connect(lambda value: self._change_display(gamma=value/100))
        self.invert.toggled.connect(lambda value: self._change_display(invert=value))
        return panel

    def _snapshot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 10, 7)
        directory_row = QHBoxLayout()
        self.snapshot_directory = QLineEdit(str(self.preferences.get("snapshot_directory", Path.home()/"Pictures"/"InfraredCamera")))
        self.snapshot_directory.setObjectName("snapshotDirectory")
        directory_row.addWidget(self.snapshot_directory, 1)
        browse = self._button("Browse")
        browse.clicked.connect(self._browse_snapshot_directory)
        directory_row.addWidget(browse)
        layout.addLayout(directory_row)
        self.snapshot_mode = QComboBox()
        self.snapshot_mode.setObjectName("snapshotMode")
        self.snapshot_mode.addItem("Display appearance - 8-bit TIFF", "display")
        self.snapshot_mode.addItem("Original grayscale - source precision", "raw")
        self.snapshot_mode.setCurrentIndex(max(0, self.snapshot_mode.findData(self.preferences.get("snapshot_mode", "display"))))
        layout.addWidget(self.snapshot_mode)
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("Filename prefix"))
        self.prefix = QLineEdit(str(self.preferences.get("prefix", "capture")))
        self.prefix.setMaxLength(80)
        prefix_row.addWidget(self.prefix, 1)
        layout.addLayout(prefix_row)
        self.save_hint = QLabel("Ctrl+S saves the displayed frame at full image resolution. Zoom and pan do not crop the TIFF.")
        self.save_hint.setWordWrap(True)
        self.save_hint.setObjectName("muted")
        layout.addWidget(self.save_hint)
        open_folder = self._button("Open save folder")
        open_folder.clicked.connect(self._open_save_folder)
        layout.addWidget(open_folder)
        return panel

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        def action(menu, text, callback, shortcut=None):
            item = QAction(text, self)
            item.triggered.connect(callback)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            menu.addAction(item)
            return item
        action(file_menu, "Save TIFF snapshot", self.take_snapshot, "Ctrl+S")
        action(file_menu, "Open save folder", self._open_save_folder)
        file_menu.addSeparator()
        action(file_menu, "Load display preset...", self._load_display)
        action(file_menu, "Save display preset...", self._save_display)
        action(file_menu, "Load camera XML...", lambda: self._camera_xml(True))
        action(file_menu, "Save camera XML...", lambda: self._camera_xml(False))
        file_menu.addSeparator()
        action(file_menu, "Exit", self.close)
        view_menu = self.menuBar().addMenu("View")
        action(view_menu, "Fit to window", self.view.fit_image)
        action(view_menu, "Native size (1:1)", self.view.native_size)
        action(view_menu, "Fullscreen", self.toggle_fullscreen, "F11")
        tools = self.menuBar().addMenu("Tools")
        action(tools, "SDK / transport-layer path...", self._configure_sdk)
        action(tools, "Diagnostics...", self._show_diagnostics)
        help_menu = self.menuBar().addMenu("Help")
        action(help_menu, "About", lambda: QMessageBox.information(self, "About Infrared Camera Viewer",
            f"Infrared Camera Viewer {__version__}\n\nWindows x64 viewer using VmbPy 1.2.3.\n"
            "Demo images are synthetic and do not measure temperature.\n"
            "Real hardware operation must be validated on the target Windows machine."))

    def _connect_signals(self) -> None:
        self.source.currentIndexChanged.connect(self.refresh_devices)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.connect_button.clicked.connect(self._toggle_connection)
        self.start_button.clicked.connect(self._toggle_acquisition)
        self.freeze_button.toggled.connect(self._freeze)
        self.snapshot_button.clicked.connect(self.take_snapshot)
        self.view.cursor_pixel.connect(self._cursor)
        self.view.zoom_changed.connect(lambda zoom: self.zoom_label.setText(f"{zoom*100:.0f}%"))
        self.camera.devices_changed.connect(self._devices_changed)
        self.camera.controls_changed.connect(self._controls_changed)
        self.camera.state_changed.connect(self._state_changed)
        self.camera.message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.camera.error.connect(self.show_error)
        self.camera.diagnostics_changed.connect(self._set_diagnostics)
        self.saver.saved.connect(self._snapshot_saved)
        self.saver.failed.connect(self.show_error)

    def refresh_devices(self, *_args) -> None:
        if self._closing:
            return
        self.error_bar.hide()
        self.connect_button.setEnabled(False)
        self.devices.clear()
        self.camera.submit("scan", kind=self.source.currentData(), cti=self.cti_path)
        self.statusBar().showMessage("Discovering devices...")

    def _devices_changed(self, devices) -> None:
        blocker = QSignalBlocker(self.devices)
        self.devices.clear()
        for device in devices:
            self.devices.addItem(device.label, device.id)
        del blocker
        self.connect_button.setEnabled(bool(devices) or self._device is not None)
        self.statusBar().showMessage(f"Found {len(devices)} device(s).", 6000)
        if self._auto_connect_demo and devices:
            self._auto_connect_demo = False
            self._toggle_connection()

    def _toggle_connection(self) -> None:
        self.connect_button.setEnabled(False)
        if self._device:
            self.camera.submit("disconnect")
        elif self.devices.currentData():
            self.error_bar.hide()
            if self.freeze_button.isChecked():
                self.freeze_button.setChecked(False)
            self.camera.submit("connect", kind=self.source.currentData(), cti=self.cti_path,
                               device_id=self.devices.currentData(), start=True)

    def _toggle_acquisition(self) -> None:
        if self._device:
            self.camera.submit("stop" if self._streaming else "start")

    def _freeze(self, checked: bool) -> None:
        if checked and self.packet is None:
            return
        self._expected_revision = self.processor.freeze(self.packet.frame if checked and self.packet else None)
        self.freeze_button.setText("Resume" if checked else "Freeze")
        self._update_badge()

    def _write_control(self, key: str, value) -> None:
        if self._updating or not self._device or self._closing:
            return
        self.camera.submit("write", key=key, value=value)

    def _controls_changed(self, controls) -> None:
        self._updating = True
        try:
            self.camera_hint.setVisible(not bool(controls))
            for key, widget in self.numeric.items():
                widget.setVisible(key in controls)
                if key in controls:
                    widget.set_info(controls[key])
            for key, combo in self.enums.items():
                self.enum_rows[key].setVisible(key in controls)
                if key not in controls:
                    continue
                info = controls[key]
                blocker = QSignalBlocker(combo)
                if tuple(combo.itemText(i) for i in range(combo.count())) != info.entries:
                    combo.clear()
                    combo.addItems(info.entries)
                combo.setCurrentText(str(info.value))
                combo.setEnabled(info.writable or key == "pixel_format")
                del blocker
            self.fps_enable.setVisible("fps_enabled" in controls)
            if "fps_enabled" in controls:
                info = controls["fps_enabled"]
                blocker = QSignalBlocker(self.fps_enable)
                self.fps_enable.setChecked(bool(info.value))
                self.fps_enable.setEnabled(info.writable)
                del blocker
        finally:
            self._updating = False

    def _state_changed(self, state) -> None:
        previous_device = self._device
        was_streaming = self._streaming
        self._device = state["device"]
        self._streaming = state["streaming"]
        if self._streaming and (not was_streaming or previous_device != self._device):
            self._stream_started_at = time.monotonic()
            self._waiting_warned = False
            self.view.placeholder.setText("Waiting for a complete frame...\n\nLong exposures can take several seconds.")
        elif not self._streaming:
            self._waiting_warned = False
        self.connect_button.setText("Disconnect" if self._device else "Connect")
        self.connect_button.setEnabled(bool(self._device) or self.devices.count() > 0)
        self.start_button.setText("Stop" if self._streaming else "Start")
        self.start_button.setEnabled(self._device is not None)
        self.source.setEnabled(self._device is None)
        self.devices.setEnabled(self._device is None)
        self.refresh_button.setEnabled(self._device is None)
        if self._device:
            self.connection_label.setText(self._device.label)
        else:
            self.connection_label.setText("Not connected")
            if previous_device:
                self._controls_changed({})
        now = time.monotonic()
        elapsed = now-self._last_state_time
        if elapsed >= 0.5:
            self._acquisition_fps = max(0, state["complete"]-self._last_complete)/elapsed
            self._last_complete, self._last_state_time = state["complete"], now
        self._incomplete = state["incomplete"]
        self._update_badge()

    def _update_badge(self) -> None:
        if self.freeze_button.isChecked():
            text = "FROZEN"
        elif self._streaming:
            text = "WAITING" if self._waiting_warned else "LIVE"
        elif self.packet:
            text = "STOPPED"
        else:
            text = "READY"
        if self.packet and self.packet.frame.metadata.get("simulated"):
            text += "  |  SIMULATED"
        if self.packet and self._device and self.packet.frame.camera_id != self._device.id:
            text += "  |  LAST FRAME"
        self.state_badge.setText(text)

    def _change_display(self, **changes) -> None:
        if self._updating or self._closing:
            return
        try:
            self.options = replace(self.options, **changes)
            self._expected_revision = self.processor.configure(self.options)
            self._sync_display_controls()
        except (ValueError, TypeError) as exc:
            self.show_error(str(exc))
            self._sync_display_controls()

    def _sync_display_controls(self) -> None:
        self._updating = True
        widgets = (self.black, self.white, self.gamma, self.gamma_slider, self.invert,
                   self.mode, self.percentile_low, self.percentile_high, self.smoothing)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        try:
            if not self.black.hasFocus():
                self.black.setValue(self.options.black)
            if not self.white.hasFocus():
                self.white.setValue(self.options.white)
            if not self.gamma.hasFocus():
                self.gamma.setValue(self.options.gamma)
            self.gamma_slider.setValue(round(self.options.gamma*100))
            self.invert.setChecked(self.options.invert)
            self.mode.setCurrentIndex(self.mode.findData(self.options.mode))
            self.percentile_low.setValue(self.options.low_percentile)
            self.percentile_high.setValue(self.options.high_percentile)
            self.smoothing.setValue(self.options.smoothing_seconds)
        finally:
            del blockers
            self._updating = False

    def _manual_levels(self) -> None:
        self._change_display(black=self.black.value(), white=self.white.value(), mode="manual")

    def _auto_options(self) -> None:
        self._change_display(low_percentile=self.percentile_low.value(),
            high_percentile=self.percentile_high.value(), smoothing_seconds=self.smoothing.value())

    def _full_range(self) -> None:
        maximum = self.packet.frame.maximum if self.packet else 65535
        self._change_display(black=0, white=maximum, mode="manual")
        self.histogram.full_axis(maximum)

    def _orientation_changed(self, *_args) -> None:
        self.orientation = Orientation(self.angle.value(), self.flip_h.isChecked(), self.flip_v.isChecked())
        self.view.set_orientation(self.orientation)
        self._cursor(None)

    def _reset_orientation(self) -> None:
        for widget in (self.angle, self.flip_h, self.flip_v):
            widget.blockSignals(True)
        self.angle.setValue(0)
        self.flip_h.setChecked(False)
        self.flip_v.setChecked(False)
        for widget in (self.angle, self.flip_h, self.flip_v):
            widget.blockSignals(False)
        self._orientation_changed()

    def _poll(self) -> None:
        sequence, packet = self.processor.output.read(self._packet_sequence)
        self._packet_sequence = sequence
        if packet is not None and packet.revision >= self._expected_revision:
            self.packet = packet
            self.options = packet.options
            self.view.set_image(packet.image)
            self.histogram.update_data(packet.histogram, packet.options.black,
                                       packet.options.white, packet.frame.maximum)
            self._sync_display_controls()
            frame = packet.frame
            self.frame_label.setText(f"{frame.pixels.shape[1]} x {frame.pixels.shape[0]}  |  "
                                     f"{frame.pixel_format} ({frame.bit_depth}-bit)  |  Frame {frame.frame_id if frame.frame_id is not None else 'unknown'}")
            stats = packet.histogram
            self.histogram_stats.setText(f"DN {stats.minimum} .. {stats.maximum}  |  mean {stats.mean:.1f}\n"
                f"Display clipping: low {stats.clipped_low:.1%}, high {stats.clipped_high:.1%}")
            self.snapshot_button.setEnabled(True)
            self.freeze_button.setEnabled(True)
            self._render_count += 1
            self._cursor(self._cursor_position)
            if self._waiting_warned:
                self._waiting_warned = False
                self.statusBar().clearMessage()
            self._update_badge()
        error = self.processor.take_error()
        if error:
            self.show_error(error)
        now = time.monotonic()
        if self._streaming and not self.freeze_button.isChecked():
            last_frame = self.packet.frame.received_monotonic if self.packet and self._device and self.packet.frame.camera_id == self._device.id else 0
            exposure = self.numeric["exposure"].info
            exposure_seconds = float(exposure.value)*NumericControl.TIME_UNITS.get(exposure.unit, 0) if exposure else 0
            if now-max(last_frame, self._stream_started_at) > max(2.0, exposure_seconds*1.5+0.5):
                if not self._waiting_warned:
                    self._waiting_warned = True
                    self.statusBar().showMessage("Waiting for complete frames. Check exposure, trigger mode, transport settings, and the camera connection.")
                    self._update_badge()
        if now-self._fps_time >= 1:
            display_fps = self._render_count/(now-self._fps_time)
            self.performance_label.setText(f"Acq {self._acquisition_fps:.1f} fps  |  View {display_fps:.1f} fps  |  "
                f"Skipped {self.frames.overwritten}  |  Incomplete {getattr(self, '_incomplete', 0)}")
            self._render_count, self._fps_time = 0, now

    def _cursor(self, position) -> None:
        self._cursor_position = position
        if position is not None and self.packet:
            x, y = position
            frame = self.packet.frame
            if 0 <= y < frame.pixels.shape[0] and 0 <= x < frame.pixels.shape[1]:
                self.cursor_label.setText(f"x {x}, y {y}  |  {int(frame.pixels[y, x])} DN")
            else:
                self._cursor_position = None
                self.cursor_label.setText("")
        else:
            self.cursor_label.setText("")

    def take_snapshot(self) -> None:
        if not self.packet or self._closing:
            self.statusBar().showMessage("There is no displayed frame to save.", 5000)
            return
        directory = self.snapshot_directory.text().strip()
        if not directory:
            self.show_error("Choose a snapshot directory first.")
            return
        request = SnapshotRequest(self.packet, self.orientation, Path(directory), self.prefix.text(),
                                  self.snapshot_mode.currentData(), utc_now())
        if self.saver.submit(request):
            self.statusBar().showMessage("Saving TIFF...", 3000)

    def _snapshot_saved(self, path: str) -> None:
        self._last_snapshot = Path(path)
        self.save_hint.setText(f"Saved {self._last_snapshot.name}")
        self.statusBar().showMessage(f"Saved TIFF: {path}", 8000)

    def _browse_snapshot_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose snapshot directory", self.snapshot_directory.text())
        if path:
            self.snapshot_directory.setText(path)

    def _open_save_folder(self) -> None:
        path = Path(self.snapshot_directory.text()).expanduser()
        if not path.is_dir():
            self.show_error("The save folder does not exist yet. Save a snapshot or choose an existing folder.")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_bar.show()
        self.statusBar().showMessage(text, 10000)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)

    def _set_diagnostics(self, text: str) -> None:
        self._diagnostics = text

    def _show_diagnostics(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Viewer diagnostics")
        dialog.resize(720, 430)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(f"Infrared Camera Viewer {__version__}\nPython: {sys.version}\n"
            f"Platform: {platform.platform()}\nSettings: {self.settings_path}\n"
            f"Configured transport path: {self.cti_path or '(SDK default)'}\n\n{self._diagnostics}\n\n"
            "Synthetic demo checks are not real-camera or vendor-simulator validation.")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _configure_sdk(self) -> None:
        if self._device:
            self.show_error("Disconnect the camera before changing the SDK path.")
            return
        path = QFileDialog.getExistingDirectory(self, "Choose Vimba X installation or cti directory", self.cti_path)
        if path:
            self.cti_path = path
            self.refresh_devices()

    def _save_display(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save display preset", "display-preset.json", "JSON presets (*.json)")
        if path:
            try:
                save_display_preset(Path(path), self.options, self.orientation)
                self.statusBar().showMessage("Display preset saved.", 5000)
            except Exception as exc:
                self.show_error(f"Could not save display preset: {exc}")

    def _load_display(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load display preset", "", "JSON presets (*.json)")
        if path:
            try:
                self.options, self.orientation = load_display_preset(Path(path))
                self._expected_revision = self.processor.configure(self.options)
                self._sync_display_controls()
                orientation = self.orientation
                blockers = [QSignalBlocker(w) for w in (self.angle, self.flip_h, self.flip_v)]
                self.angle.setValue(self.orientation.angle)
                self.flip_h.setChecked(self.orientation.flip_horizontal)
                self.flip_v.setChecked(self.orientation.flip_vertical)
                del blockers
                self.orientation = orientation
                self.view.set_orientation(self.orientation)
            except Exception as exc:
                self.show_error(f"Could not load display preset: {exc}")

    def _camera_xml(self, load: bool) -> None:
        if not self._device or self.source.currentData() != "vimba":
            self.show_error("Connect a Vimba camera to use camera XML presets.")
            return
        picker = QFileDialog.getOpenFileName if load else QFileDialog.getSaveFileName
        path, _ = picker(self, "Load camera XML" if load else "Save camera XML", "", "Camera settings (*.xml)")
        if path:
            if not path.endswith(".xml"):
                path += ".xml"
            self.camera.submit("load_xml" if load else "save_xml", path=path)

    def _save_preferences(self) -> None:
        atomic_json(self.settings_path, {"schema_version": 1, "display": asdict(self.options),
            "orientation": asdict(self.orientation), "cti_path": self.cti_path,
            "source": self.source.currentData(), "snapshot_directory": self.snapshot_directory.text(),
            "snapshot_mode": self.snapshot_mode.currentData(), "prefix": self.prefix.text(),
            "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")})

    def closeEvent(self, event) -> None:
        if self._shutdown_complete:
            event.accept()
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            self.render_timer.stop()
            self.centralWidget().setEnabled(False)
            self.statusBar().showMessage("Closing: waiting for camera and pending TIFF saves...")
            self.processor.stop()
            self.camera.shutdown()
            self.saver.stop_accepting()
            self.shutdown_timer.start()

    def _finish_shutdown(self) -> None:
        if self.camera.isRunning() or self.processor.is_alive() or self.saver.pending:
            return
        self.shutdown_timer.stop()
        try:
            self._save_preferences()
        except Exception:
            LOG.exception("Could not save application preferences")
        self._shutdown_complete = True
        self.close()
