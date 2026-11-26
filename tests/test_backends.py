"""A contract fake exercises VmbPy call ordering without loading vendor DLLs."""

from enum import Enum
from types import SimpleNamespace
import unittest

import numpy as np

from ir_camera_viewer.backends import DemoBackend, VimbaBackend


class FeatureError(Exception):
    pass


class Format(Enum):
    Mono8 = "Mono8"
    Mono10 = "Mono10"
    Mono10p = "Mono10p"
    Mono12 = "Mono12"
    Mono12p = "Mono12p"
    Mono12Packed = "Mono12Packed"
    Mono14 = "Mono14"
    Mono16 = "Mono16"

    def __str__(self):
        return self.value


class Number:
    def __init__(self, name, value, minimum=100.0, maximum=50000.0, increment=None, unit="us"):
        self.name, self.value = name, value
        self.minimum, self.maximum, self.increment, self.unit = minimum, maximum, increment, unit

    def get_name(self):
        return self.name

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def is_readable(self):
        return True

    def is_writeable(self):
        return True

    def get_unit(self):
        return self.unit

    def get_range(self):
        return self.minimum, self.maximum

    def get_increment(self):
        return self.increment


class EnumFeature(Number):
    def get_available_entries(self):
        return ("Off", "Once", "Continuous")


class Camera:
    def __init__(self):
        self.format = Format.Mono12
        self.features = {"ExposureTimeAbs": Number("ExposureTimeAbs", 1000.0),
                         "ExposureAuto": EnumFeature("ExposureAuto", "Off")}
        self.entered = 0
        self.exited = 0
        self.starts = 0
        self.stops = 0
        self.queued = 0
        self.running = False

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_args):
        self.exited += 1

    def get_id(self):
        return "mock-camera"

    def get_model(self):
        return "Mock monochrome camera"

    def get_serial(self):
        return "MOCK-001"

    def get_transport_layer(self):
        return SimpleNamespace(get_name=lambda: "Mock GigE TL", get_id=lambda: "MockTL")

    def get_feature_by_name(self, name):
        if name not in self.features:
            raise FeatureError(name)
        return self.features[name]

    def get_pixel_formats(self):
        return tuple(Format)

    def get_pixel_format(self):
        return self.format

    def set_pixel_format(self, fmt):
        if self.running:
            raise AssertionError("Pixel format changed during acquisition.")
        self.format = fmt

    def start_streaming(self, handler, buffer_count):
        self.starts += 1
        self.running = True
        self.handler = handler

    def stop_streaming(self):
        self.stops += 1
        self.running = False

    def queue_frame(self, frame):
        self.queued += 1
        frame.array[:] = 0


class System:
    def __init__(self, camera):
        self.camera = camera
        self.enters = 0
        self.exits = 0

    def __enter__(self):
        self.enters += 1
        return self

    def __exit__(self, *_args):
        self.exits += 1

    def get_all_cameras(self):
        return (self.camera,)

    def get_camera_by_id(self, device_id):
        return self.camera

    def get_all_transport_layers(self):
        return (SimpleNamespace(get_name=lambda: "Mock TL", get_path=lambda: "mock.cti"),)

    def register_camera_change_handler(self, handler):
        self.event_handler = handler

    def get_version(self):
        return "Mock VmbPy 1.2.3"


class Frame:
    def __init__(self, array, fmt=Format.Mono12, status="Complete", chunk=False):
        self.array = array
        self.fmt = fmt
        self.status = status
        self.chunk = chunk
        self.converted_to = None

    def get_status(self):
        return self.status

    def get_pixel_format(self):
        return self.fmt

    def as_numpy_ndarray(self):
        return self.array[:, :, None]

    def convert_pixel_format(self, target):
        self.converted_to = target
        return Frame(self.array, target)

    def get_id(self):
        return 42

    def get_timestamp(self):
        return 123456

    def contains_chunk_data(self):
        return self.chunk

    def access_chunk_data(self, callback):
        class Features:
            def get_feature_by_name(self, name):
                if name != "ChunkExposureTime":
                    raise FeatureError(name)
                return Number(name, 1500.0)
        callback(Features())


class VimbaContractTests(unittest.TestCase):
    def setUp(self):
        self.camera = Camera()
        self.system = System(self.camera)
        self.sdk = SimpleNamespace(VmbSystem=SimpleNamespace(get_instance=lambda: self.system),
            VmbFeatureError=FeatureError, PixelFormat=Format,
            FrameStatus=SimpleNamespace(Complete="Complete"))
        self.frames = []
        self.backend = VimbaBackend(self.frames.append, sdk=self.sdk)
        self.backend.open("mock-camera")
        self.backend.start()

    def tearDown(self):
        self.backend.shutdown()

    def test_array_is_owned_before_sdk_buffer_is_requeued(self):
        expected = np.array([[1, 1000], [2048, 4095]], np.uint16)
        frame = Frame(expected.copy())
        self.backend._on_frame(self.camera, None, frame)
        self.assertEqual(self.camera.queued, 1)
        self.assertFalse(frame.array.any())
        np.testing.assert_array_equal(self.frames[0].pixels, expected)
        self.assertFalse(self.frames[0].pixels.flags.writeable)
        self.assertEqual(self.frames[0].camera_timestamp, 123456)

    def test_both_12bit_packed_formats_use_sdk_conversion(self):
        for fmt in (Format.Mono12p, Format.Mono12Packed):
            expected = np.array([[11, 1234, 4095]], np.uint16)
            frame = Frame(expected.copy(), fmt, chunk=True)
            self.backend._on_frame(self.camera, None, frame)
            self.assertEqual(frame.converted_to, Format.Mono12)
            np.testing.assert_array_equal(self.frames[-1].pixels, expected)
            self.assertEqual(self.frames[-1].metadata["chunk"]["ChunkExposureTime"]["value"], 1500.0)

    def test_incomplete_or_failed_frames_are_requeued(self):
        self.backend._on_frame(self.camera, None, Frame(np.ones((2, 2), np.uint16), status="Incomplete"))
        self.backend._on_frame(self.camera, None, Frame(np.ones((2, 2), np.uint16), fmt="Rgb8"))
        self.assertEqual(self.camera.queued, 2)
        self.assertEqual(self.frames, [])
        self.assertEqual(self.backend.counters.read(), (0, 2))
        with self.assertRaisesRegex(RuntimeError, "Unsupported source format"):
            self.backend.tick()

    def test_legacy_exposure_name_and_missing_increment_work(self):
        control = self.backend.controls()["exposure"]
        self.assertEqual(control.name, "ExposureTimeAbs")
        self.assertEqual(control.unit, "us")
        self.assertIsNone(control.increment)
        self.backend.write("exposure", 1234.5)
        self.assertEqual(self.backend.controls()["exposure"].value, 1234.5)

    def test_manual_exposure_does_not_fight_automatic_control(self):
        self.camera.features["ExposureAuto"].value = "Continuous"
        with self.assertRaisesRegex(RuntimeError, "Turn off automatic exposure"):
            self.backend.write("exposure", 5000)

    def test_format_change_stops_before_writing_and_resumes(self):
        self.backend.write("pixel_format", "Mono8")
        self.assertEqual(self.camera.format, Format.Mono8)
        self.assertTrue(self.backend.streaming)
        self.assertEqual(self.camera.stops, 1)
        self.assertEqual(self.camera.starts, 2)

    def test_refresh_and_reconnect_keep_one_system_session(self):
        self.backend.list_devices()
        self.backend.close()
        self.backend.open("mock-camera")
        self.backend.list_devices()
        self.assertEqual(self.system.enters, 1)
        self.assertEqual(self.system.exits, 0)

    def test_disconnect_is_handled_outside_sdk_event_callback(self):
        self.system.event_handler(self.camera, SimpleNamespace(name="Missing"))
        self.assertTrue(self.backend.streaming)
        with self.assertRaises(ConnectionError):
            self.backend.tick()
        self.assertFalse(self.backend.streaming)
        self.assertIsNone(self.backend.device)


class DemoTests(unittest.TestCase):
    def test_demo_identifies_itself_and_preserves_effective_bit_depth(self):
        frames = []
        demo = DemoBackend(frames.append)
        demo.open("demo-001")
        demo.start()
        demo.tick()
        self.assertEqual(frames[-1].pixels.dtype, np.uint16)
        self.assertEqual(frames[-1].bit_depth, 12)
        self.assertTrue(frames[-1].metadata["simulated"])
        demo.write("pixel_format", "Mono8")
        demo._next_frame = 0
        demo.tick()
        self.assertEqual(frames[-1].pixels.dtype, np.uint8)
        self.assertEqual(frames[-1].bit_depth, 8)
        demo.shutdown()


if __name__ == "__main__":
    unittest.main()
