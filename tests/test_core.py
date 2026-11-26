"""Numerical and file-integrity tests independent of physical hardware."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import tifffile
from PySide6.QtCore import QPointF

from ir_camera_viewer.backends import quantize
from ir_camera_viewer.geometry import oriented_pixels, orientation_transform
from ir_camera_viewer.imaging import (automatic_levels, histogram, sample_pixels, tone_map,
                                      ImageProcessor, RenderPacket)
from ir_camera_viewer.models import DisplayOptions, FrameData, LatestSlot, Orientation
from ir_camera_viewer.settings import (load_display_preset, save_display_preset, load_preferences)
from ir_camera_viewer.snapshots import SnapshotRequest, save_snapshot


def wait_until(predicate, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for a worker result.")


class NumericalTests(unittest.TestCase):
    def test_narrow_12bit_range_is_not_quantized_to_8bit_first(self):
        raw = np.arange(1000, 1021, dtype=np.uint16).reshape(3, 7)
        before = raw.copy()
        result = tone_map(raw, DisplayOptions(1000, 1020, mode="manual"))
        self.assertEqual(int(result.min()), 0)
        self.assertEqual(int(result.max()), 255)
        self.assertEqual(len(np.unique(result)), 21)
        np.testing.assert_array_equal(raw, before)

    def test_gamma_convention_and_inversion(self):
        raw = np.array([[0, 1024, 4095]], np.uint16)
        base = tone_map(raw, DisplayOptions(0, 4095, mode="manual"))
        brighter = tone_map(raw, DisplayOptions(0, 4095, gamma=2, mode="manual"))
        self.assertGreater(int(brighter[0, 1]), int(base[0, 1]))
        inverted = tone_map(raw, DisplayOptions(0, 4095, invert=True, mode="manual"))
        np.testing.assert_array_equal(inverted, 255-base)

    def test_percentiles_reject_a_single_bright_outlier(self):
        data = np.tile(np.arange(1780, 1821, dtype=np.uint16), 100)
        data[0] = 4095
        black, white = automatic_levels(data, 4095, 1, 99)
        self.assertGreaterEqual(black, 1780)
        self.assertLessEqual(white, 1820)

    def test_constant_images_produce_valid_levels(self):
        for value in (0, 100, 65535):
            raw = np.full((9, 7), value, np.uint16)
            black, white = automatic_levels(raw, 65535, 1, 99)
            self.assertLess(black, white)
            self.assertGreaterEqual(black, 0)
            self.assertLessEqual(white, 65535)
            image = tone_map(raw, DisplayOptions(black, white))
            self.assertEqual(np.unique(image).size, 1)

    def test_histogram_remains_in_raw_dn_coordinates(self):
        raw = np.array([0, 1024, 2048, 4095], np.uint16)
        result = histogram(raw, 12, DisplayOptions(1000, 3000))
        self.assertEqual(int(result.counts.sum()), 4)
        self.assertEqual(result.edges[-1], 4096)
        self.assertEqual((result.minimum, result.maximum), (0, 4095))
        self.assertEqual(result.clipped_low, 0.25)
        self.assertEqual(result.clipped_high, 0.25)

    def test_sampling_has_a_bounded_size(self):
        raw = np.zeros((2048, 2592), np.uint16)
        self.assertLessEqual(sample_pixels(raw).size, 131072)

    def test_invalid_display_parameters_are_rejected(self):
        for values in ({"black": 9, "white": 9}, {"gamma": float("nan")},
                       {"low_percentile": 100}, {"invert": "false"}):
            with self.assertRaises((ValueError, TypeError)):
                DisplayOptions(**values)

    def test_camera_value_rounding_respects_native_steps(self):
        self.assertEqual(quantize(112, 10, 200, 5), 110)
        self.assertEqual(quantize(-3, 10, 200, None), 10)
        self.assertEqual(quantize(300, 10, 200, 7), 199)
        with self.assertRaises(ValueError):
            quantize(float("inf"), 10, 200, None)

    def test_mailbox_replaces_stale_frames_without_growing(self):
        slot = LatestSlot()
        for i in range(100):
            slot.publish(i)
        seq, result = slot.read()
        self.assertEqual(result, 99)
        self.assertEqual(slot.overwritten, 99)
        self.assertIsNone(slot.read(seq)[1])


class GeometryTests(unittest.TestCase):
    def test_right_angles_and_flips_match_pixel_center_mapping(self):
        image = np.arange(15, dtype=np.uint8).reshape(3, 5)
        for angle in (0, 90, -90, 180, 270):
            for horizontal in (False, True):
                for vertical in (False, True):
                    orientation = Orientation(angle, horizontal, vertical)
                    result = oriented_pixels(image, orientation)
                    transform = orientation_transform(5, 3, orientation)
                    for y in range(3):
                        for x in range(5):
                            point = transform.map(QPointF(x+0.5, y+0.5))
                            self.assertEqual(result[int(point.y()), int(point.x())], image[y, x])

    def test_arbitrary_rotation_preserves_extent_and_source(self):
        raw = np.arange(80, dtype=np.uint8).reshape(8, 10)
        original = raw.copy()
        result = oriented_pixels(raw, Orientation(45))
        self.assertGreater(result.shape[0], raw.shape[0])
        self.assertGreater(result.shape[1], raw.shape[1])
        self.assertEqual(result.dtype, np.uint8)
        np.testing.assert_array_equal(raw, original)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        raw = np.array([[0, 1, 256], [1023, 2048, 4095]], np.uint16)
        self.frame = FrameData(raw, 123, 12, "Mono12", model="Test camera",
                               metadata={"alignment": "LSB", "simulated": True})
        options = DisplayOptions(0, 4095, gamma=1.7, invert=True, mode="manual")
        self.packet = RenderPacket(self.frame, tone_map(raw, options), options,
            histogram(raw.reshape(-1), 12, options), 0, 0)

    def tearDown(self):
        self.directory.cleanup()

    def test_original_tiff_roundtrips_exact_dn_values(self):
        path = save_snapshot(SnapshotRequest(self.packet, Orientation(90, True),
                            Path(self.directory.name), mode="raw"))
        with tifffile.TiffFile(path) as tiff:
            np.testing.assert_array_equal(tiff.asarray(), self.frame.pixels)
            self.assertEqual(tiff.asarray().dtype, np.uint16)
            meta = json.loads(tiff.pages[0].description)
            self.assertEqual(meta["source_effective_bit_depth"], 12)
            self.assertFalse(meta["orientation_applied_to_pixels"])
            self.assertEqual(meta["frame_id"], 123)

    def test_processed_tiff_matches_full_resolution_display(self):
        orientation = Orientation(90, True, True)
        path = save_snapshot(SnapshotRequest(self.packet, orientation, Path(self.directory.name)))
        np.testing.assert_array_equal(tifffile.imread(path), oriented_pixels(self.packet.image, orientation))
        self.assertEqual(tifffile.imread(path).dtype, np.uint8)

    def test_8bit_original_stays_8bit(self):
        frame = FrameData(np.array([[0, 17, 255]], np.uint8), 1, 8, "Mono8")
        packet = replace(self.packet, frame=frame)
        path = save_snapshot(SnapshotRequest(packet, Orientation(), Path(self.directory.name), mode="raw"))
        np.testing.assert_array_equal(tifffile.imread(path), frame.pixels)

    def test_filenames_are_safe_and_do_not_overwrite(self):
        request = SnapshotRequest(self.packet, Orientation(), Path(self.directory.name), prefix='a:b/c*?')
        first = save_snapshot(request)
        second = save_snapshot(request)
        self.assertNotEqual(first, second)
        self.assertNotIn(":", first.name)
        self.assertTrue(first.is_file() and second.is_file())

    def test_failed_save_cleans_reserved_and_temporary_files(self):
        with patch("ir_camera_viewer.snapshots.tifffile.imwrite", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                save_snapshot(SnapshotRequest(self.packet, Orientation(), Path(self.directory.name)))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_display_presets_roundtrip_orientation_and_parameters(self):
        path = Path(self.directory.name)/"preset.json"
        orientation = Orientation(-37.5, True, True)
        save_display_preset(path, self.packet.options, orientation)
        options, restored = load_display_preset(path)
        self.assertEqual(options, self.packet.options)
        self.assertEqual(restored, orientation)

    def test_invalid_preferences_fall_back_to_defaults(self):
        path = Path(self.directory.name)/"settings.json"
        path.write_text('{"schema_version":1,"display":{"gamma":-4}}')
        self.assertEqual(load_preferences(path), {})


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.source = LatestSlot()
        self.processor = ImageProcessor(self.source)
        self.processor.start()

    def tearDown(self):
        self.processor.stop()
        self.processor.join(3)
        self.assertFalse(self.processor.is_alive())

    def frame(self, frame_id, offset=0):
        return FrameData(np.arange(1000+offset, 1100+offset, dtype=np.uint16).reshape(10, 10),
                         frame_id, 12, "Mono12", camera_id="test")

    def packet(self, predicate):
        result = []
        def check():
            _, packet = self.processor.output.read()
            if packet is not None and predicate(packet):
                result[:] = [packet]
                return True
            return False
        wait_until(check)
        return result[0]

    def test_manual_levels_survive_new_frames(self):
        revision = self.processor.configure(DisplayOptions(1000, 1200, mode="manual"))
        for i in range(1, 5):
            self.source.publish(self.frame(i, i))
            packet = self.packet(lambda p: p.frame.frame_id == i and p.revision == revision)
            self.assertEqual((packet.options.black, packet.options.white), (1000, 1200))

    def test_freeze_pins_the_exact_displayed_frame(self):
        first = self.frame(1)
        self.source.publish(first)
        self.packet(lambda p: p.frame.frame_id == 1)
        revision = self.processor.freeze(first)
        self.source.publish(self.frame(2, 50))
        packet = self.packet(lambda p: p.revision == revision)
        self.assertIs(packet.frame, first)
        self.processor.freeze(None)
        self.source.publish(self.frame(3, 80))
        self.packet(lambda p: p.frame.frame_id == 3)

    def test_auto_once_becomes_fixed_manual_levels(self):
        self.processor.configure(DisplayOptions(mode="once"))
        self.source.publish(self.frame(1))
        once = self.packet(lambda p: p.frame.frame_id == 1)
        self.assertEqual(once.options.mode, "manual")
        self.source.publish(self.frame(2, 200))
        next_packet = self.packet(lambda p: p.frame.frame_id == 2)
        self.assertEqual((once.options.black, once.options.white),
                         (next_packet.options.black, next_packet.options.white))

    def test_new_8bit_format_resets_an_invalid_manual_range(self):
        self.processor.configure(DisplayOptions(1000, 1200, mode="manual"))
        self.source.publish(self.frame(1))
        self.packet(lambda p: p.frame.frame_id == 1)
        self.source.publish(FrameData(np.array([[0, 128, 255]], np.uint8), 2, 8, "Mono8", camera_id="test"))
        packet = self.packet(lambda p: p.frame.frame_id == 2)
        self.assertEqual((packet.options.black, packet.options.white), (0, 255))
        np.testing.assert_array_equal(packet.image, [[0, 128, 255]])


if __name__ == "__main__":
    unittest.main()
