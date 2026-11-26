"""Offscreen application tests using the explicit synthetic demo source."""

from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np
import tifffile
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from ir_camera_viewer.geometry import oriented_pixels
from ir_camera_viewer.window import MainWindow


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def pump_until(self, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("The viewer did not reach the expected state in time.")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(Path(self.directory.name)/"settings.json", demo=True)
        self.window.snapshot_directory.setText(self.directory.name)
        self.window.show()
        self.pump_until(lambda: self.window.packet is not None)

    def tearDown(self):
        self.window.close()
        self.pump_until(lambda: self.window._shutdown_complete)
        self.assertFalse(self.window.camera.isRunning())
        self.assertFalse(self.window.processor.is_alive())
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def current_revision(self):
        self.pump_until(lambda: self.window.packet is not None and
                       self.window.packet.revision >= self.window._expected_revision)

    def test_demo_starts_without_loading_windows_sdk(self):
        self.assertTrue(self.window._streaming)
        self.assertIn("SIMULATED", self.window.state_badge.text())
        self.assertNotIn("vmbpy", sys.modules)
        self.assertGreater(self.window.packet.image.max(), self.window.packet.image.min())

    def test_exposure_units_do_not_change_camera_value(self):
        self.pump_until(lambda: self.window.numeric["exposure"].info is not None)
        exposure = self.window.numeric["exposure"]
        self.assertEqual(exposure.units.currentText(), "ms")
        self.assertAlmostEqual(exposure.spin.value(), 10)
        exposure.units.setCurrentText("us")
        self.assertAlmostEqual(exposure.spin.value(), 10000)
        self.assertAlmostEqual(exposure.info.value, 10000)

    def test_freeze_and_raw_snapshot_pin_the_displayed_frame(self):
        displayed = self.window.packet.frame
        self.window.freeze_button.setChecked(True)
        self.window._change_display(gamma=2.2, invert=True, mode="manual", black=1770, white=1920)
        self.window.angle.setValue(90)
        self.window.flip_h.setChecked(True)
        self.current_revision()
        self.assertIs(self.window.packet.frame, displayed)
        self.window.snapshot_mode.setCurrentIndex(self.window.snapshot_mode.findData("raw"))
        self.window.take_snapshot()
        self.pump_until(lambda: self.window._last_snapshot is not None and self.window.saver.pending == 0)
        with tifffile.TiffFile(self.window._last_snapshot) as tiff:
            np.testing.assert_array_equal(tiff.asarray(), displayed.pixels)
            meta = json.loads(tiff.pages[0].description)
            self.assertEqual(meta["frame_id"], displayed.frame_id)
            self.assertFalse(meta["orientation_applied_to_pixels"])
        self.window.freeze_button.setChecked(False)
        self.pump_until(lambda: self.window.packet.frame.frame_id > displayed.frame_id)

    def test_processed_snapshot_uses_full_resolution_and_exact_settings(self):
        self.window.freeze_button.setChecked(True)
        self.window._change_display(gamma=1.7, invert=True, mode="manual", black=1740, white=1950)
        self.window.angle.setValue(-90)
        self.window.flip_v.setChecked(True)
        self.current_revision()
        expected = oriented_pixels(self.window.packet.image, self.window.orientation)
        self.window.view.native_size()
        self.window.take_snapshot()
        self.pump_until(lambda: self.window._last_snapshot is not None and self.window.saver.pending == 0)
        np.testing.assert_array_equal(tifffile.imread(self.window._last_snapshot), expected)

    def test_manual_levels_are_not_overwritten_and_format_change_is_safe(self):
        self.window._change_display(black=1760, white=1930, mode="manual")
        self.current_revision()
        first_id = self.window.packet.frame.frame_id
        self.pump_until(lambda: self.window.packet.frame.frame_id >= first_id+3)
        self.assertEqual((self.window.options.black, self.window.options.white), (1760, 1930))
        self.window._write_control("pixel_format", "Mono8")
        self.pump_until(lambda: self.window.packet.frame.bit_depth == 8)
        self.assertEqual((self.window.options.black, self.window.options.white), (0, 255))
        self.assertTrue(self.window._streaming)

    def test_repeated_start_stop_preserves_preview_and_releases_threads(self):
        for _ in range(3):
            self.window._toggle_acquisition()
            self.pump_until(lambda: not self.window._streaming)
            self.assertIsNotNone(self.window.packet)
            frame_id = self.window.packet.frame.frame_id
            self.window._toggle_acquisition()
            self.pump_until(lambda: self.window._streaming and self.window.packet.frame.frame_id > frame_id)

    def test_preferences_restore_orientation_and_snapshot_mode(self):
        self.window.angle.setValue(31.5)
        self.window.flip_h.setChecked(True)
        self.window.snapshot_mode.setCurrentIndex(1)
        self.window._save_preferences()
        values = json.loads(self.window.settings_path.read_text())
        self.assertEqual(values["orientation"]["angle"], 31.5)
        self.assertTrue(values["orientation"]["flip_horizontal"])
        self.assertEqual(values["snapshot_mode"], "raw")

    def test_histogram_drag_switches_auto_to_persistent_manual(self):
        self.window.histogram.region.setRegion((1765, 1940))
        self.current_revision()
        frame_id = self.window.packet.frame.frame_id
        self.pump_until(lambda: self.window.packet.frame.frame_id >= frame_id+3)
        self.assertEqual(self.window.options.mode, "manual")
        self.assertEqual((self.window.options.black, self.window.options.white), (1765, 1940))

    def test_pending_snapshots_complete_before_window_closes(self):
        self.window.freeze_button.setChecked(True)
        self.current_revision()
        for _ in range(3):
            self.window.take_snapshot()
        self.window.close()
        self.pump_until(lambda: self.window._shutdown_complete)
        self.assertEqual(len(list(Path(self.directory.name).glob('*.tiff'))), 3)


if __name__ == "__main__":
    unittest.main()
