"""VmbPy integration and an explicitly synthetic, hardware-free demo source."""

from __future__ import annotations

from dataclasses import replace
from importlib import metadata
from pathlib import Path
from threading import Lock
from typing import Callable, Any
import logging
import math
import sys
import time

import numpy as np

from .models import ControlInfo, DeviceInfo, FrameData, utc_now

LOG = logging.getLogger(__name__)
MONO_BITS = {"Mono8": 8, "Mono10": 10, "Mono10p": 10, "Mono12": 12,
             "Mono12p": 12, "Mono12Packed": 12, "Mono14": 14, "Mono16": 16}
PACKED_TARGETS = {"Mono10p": "Mono10", "Mono12p": "Mono12", "Mono12Packed": "Mono12"}
FEATURES = {
    "exposure": ("ExposureTime", "ExposureTimeAbs"),
    "gain": ("Gain", "GainRaw"),
    "exposure_auto": ("ExposureAuto",),
    "gain_auto": ("GainAuto",),
    "fps_enabled": ("AcquisitionFrameRateEnable",),
    "fps": ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
    "width": ("Width",), "height": ("Height",),
    "offset_x": ("OffsetX",), "offset_y": ("OffsetY",),
}
STOP_KEYS = {"pixel_format", "width", "height", "offset_x", "offset_y"}


def quantize(value: float, minimum: float, maximum: float, increment: float | None) -> float:
    if not math.isfinite(value):
        raise ValueError("Camera parameter values must be finite.")
    value = min(max(value, minimum), maximum)
    if increment is not None and increment > 0:
        value = minimum + round((value - minimum) / increment) * increment
    return min(max(value, minimum), maximum)


class Counters:
    def __init__(self) -> None:
        self.lock = Lock()
        self.complete = 0
        self.incomplete = 0
        self.started = time.monotonic()

    def add(self, complete: bool) -> None:
        with self.lock:
            if complete:
                self.complete += 1
            else:
                self.incomplete += 1

    def read(self) -> tuple[int, int]:
        with self.lock:
            return self.complete, self.incomplete


class VimbaBackend:
    """All control operations belong to the camera-control thread."""

    def __init__(self, publish: Callable[[FrameData], None], cti_path: str = "", sdk: Any = None):
        if sdk is None:
            if sys.platform != "win32":
                raise RuntimeError("Real-camera mode requires Windows x64. Use Demo on this computer.")
            if sys.maxsize <= 2**32:
                raise RuntimeError("The camera SDK requires a 64-bit Python installation.")
            try:
                installed = metadata.version("vmbpy")
                if installed != "1.2.3":
                    raise RuntimeError(f"VmbPy {installed} is installed; install the supplied VmbPy 1.2.3 wheel.")
                import vmbpy as sdk
            except ImportError as exc:
                raise RuntimeError("VmbPy is unavailable. Run Setup-Windows.cmd with your Vimba X SDK path.") from exc
        self.sdk = sdk
        self.publish = publish
        self.camera = None
        self.device: DeviceInfo | None = None
        self.streaming = False
        self.counters = Counters()
        self._cache: dict[str, Any] = {}
        self._callback_error: str | None = None
        self._lost = False
        self._error_lock = Lock()
        self.system = sdk.VmbSystem.get_instance()
        if cti_path.strip():
            path = Path(cti_path).expanduser()
            if (path / "cti").is_dir():
                path = path / "cti"
            if not path.exists():
                raise RuntimeError(f"The SDK/transport-layer path does not exist: {path}")
            self.system.set_path_configuration(str(path))
        try:
            self.system.__enter__()
        except Exception as exc:
            raise RuntimeError(f"Could not initialize Vimba X: {exc}. Install the matching SDK and drivers, "
                               "then use Tools > SDK / transport-layer path to select its cti directory.") from exc
        try:
            self.system.register_camera_change_handler(self._camera_event)
            layers = self.system.get_all_transport_layers()
            layer_text = "; ".join(f"{tl.get_name()} ({tl.get_path()})" for tl in layers)
            self.diagnostics = self.system.get_version() + "\nTransport layers: " + (layer_text or "none")
            if not layers:
                self.diagnostics += "\nSet the Vimba X cti path or install the matching transport layers."
        except Exception:
            self.system.__exit__(None, None, None)
            raise

    def _camera_event(self, camera: Any, event: Any) -> None:
        if self.device and camera.get_id() == self.device.id:
            if getattr(event, "name", str(event)).lower() in ("missing", "unreachable"):
                self._lost = True

    def list_devices(self) -> list[DeviceInfo]:
        result = []
        for camera in self.system.get_all_cameras():
            tl = camera.get_transport_layer()
            simulated = "simulator" in (tl.get_name() + tl.get_id()).lower()
            result.append(DeviceInfo(camera.get_id(), camera.get_model(), camera.get_serial(),
                                     tl.get_name(), simulated))
        return result

    def open(self, device_id: str) -> None:
        self.close()
        devices = self.list_devices()
        self.device = next((d for d in devices if d.id == device_id), None)
        if self.device is None:
            raise RuntimeError("The selected camera is no longer available. Refresh the device list.")
        camera = self.system.get_camera_by_id(device_id)
        camera.__enter__()
        self.camera = camera
        try:
            formats = [f for f in camera.get_pixel_formats() if str(f) in MONO_BITS]
            if not formats:
                raise RuntimeError("This device does not expose a supported monochrome pixel format.")
            if str(camera.get_pixel_format()) not in MONO_BITS:
                camera.set_pixel_format(formats[0])
                LOG.info("Selected supported monochrome format %s", formats[0])
            self._lost = False
            self.controls()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            self.stop()
        finally:
            camera, self.camera = self.camera, None
            self.device = None
            if camera is not None:
                camera.__exit__(None, None, None)

    def shutdown(self) -> None:
        try:
            self.close()
        finally:
            self.system.__exit__(None, None, None)

    def _feature(self, key: str) -> Any:
        if self.camera is None:
            raise RuntimeError("Connect a camera first.")
        for name in FEATURES.get(key, (key,)):
            try:
                return self.camera.get_feature_by_name(name)
            except self.sdk.VmbFeatureError:
                continue
        raise KeyError(f"Camera feature '{key}' is not supported.")

    def controls(self) -> dict[str, ControlInfo]:
        if self.camera is None:
            return {}
        controls: dict[str, ControlInfo] = {}
        cache: dict[str, Any] = {"readback_utc": utc_now(), "source": "cached camera readback"}
        for key in FEATURES:
            try:
                feature = self._feature(key)
                if not feature.is_readable():
                    continue
                value = feature.get()
                writable = bool(feature.is_writeable())
                unit = feature.get_unit() or ""
                name = feature.get_name()
                if hasattr(feature, "get_available_entries"):
                    info = ControlInfo(key, name, str(value), kind="enum", writable=writable,
                                       entries=tuple(str(e) for e in feature.get_available_entries()))
                elif isinstance(value, bool):
                    info = ControlInfo(key, name, value, kind="bool", writable=writable)
                elif isinstance(value, (int, float)):
                    low, high = feature.get_range()
                    increment = feature.get_increment()
                    info = ControlInfo(key, name, value, "int" if isinstance(value, int) else "float",
                                       float(low), float(high), increment, unit, writable)
                else:
                    continue
                controls[key] = info
                cache[key] = {"value": info.value, "unit": info.unit}
            except (KeyError, self.sdk.VmbFeatureError, AttributeError) as exc:
                LOG.debug("Feature %s unavailable: %s", key, exc)
        formats = tuple(str(f) for f in self.camera.get_pixel_formats() if str(f) in MONO_BITS)
        controls["pixel_format"] = ControlInfo("pixel_format", "PixelFormat",
            str(self.camera.get_pixel_format()), kind="enum", entries=formats)
        self._cache = cache
        return controls

    def write(self, key: str, value: Any) -> None:
        if self.camera is None:
            raise RuntimeError("Connect a camera first.")
        if key in ("exposure", "gain"):
            try:
                if str(self._feature(key + "_auto").get()) != "Off":
                    raise RuntimeError(f"Turn off automatic {key} before entering a manual value.")
            except KeyError:
                pass
        was_streaming = self.streaming
        stopped = False
        try:
            if key in STOP_KEYS:
                self.stop()
                stopped = was_streaming
            if key == "pixel_format":
                if str(value) not in MONO_BITS:
                    raise ValueError("Select a supported monochrome pixel format.")
                fmt = getattr(self.sdk.PixelFormat, str(value))
                if fmt not in self.camera.get_pixel_formats():
                    raise ValueError("The camera does not support this pixel format.")
                self.camera.set_pixel_format(fmt)
            else:
                feature = self._feature(key)
                if not feature.is_writeable() and self.streaming:
                    self.stop()
                    stopped = True
                    feature = self._feature(key)
                if not feature.is_writeable():
                    raise RuntimeError(f"{feature.get_name()} is read-only in the current camera state.")
                current = feature.get()
                if isinstance(current, (int, float)) and not isinstance(current, bool):
                    low, high = feature.get_range()
                    value = quantize(float(value), low, high, feature.get_increment())
                    value = int(round(value)) if isinstance(current, int) else float(value)
                feature.set(value)
        finally:
            if stopped:
                self.start()
        self.controls()

    def start(self) -> None:
        if self.camera is None:
            raise RuntimeError("Connect a camera first.")
        if self.streaming:
            return
        # A continuous viewer explicitly leaves external-trigger acquisition mode.
        for name, value in (("TriggerMode", "Off"), ("AcquisitionMode", "Continuous")):
            try:
                feature = self.camera.get_feature_by_name(name)
                if feature.is_writeable() and hasattr(feature, "get_available_entries"):
                    if value in tuple(str(e) for e in feature.get_available_entries()):
                        feature.set(value)
            except self.sdk.VmbFeatureError:
                LOG.debug("No writable %s on this camera", name)
        self.streaming = True
        try:
            self.camera.start_streaming(self._on_frame, buffer_count=5)
        except Exception:
            self.streaming = False
            raise

    def stop(self) -> None:
        camera = self.camera
        if camera is not None and self.streaming:
            self.streaming = False
            camera.stop_streaming()

    def _on_frame(self, camera: Any, stream: Any, frame: Any) -> None:
        received_at, received_utc = time.monotonic(), utc_now()
        try:
            if not self.streaming:
                return
            if frame.get_status() != self.sdk.FrameStatus.Complete:
                self.counters.add(False)
                return
            fmt = str(frame.get_pixel_format())
            if fmt not in MONO_BITS:
                raise ValueError(f"Unsupported source format: {fmt}")
            metadata_values: dict[str, Any] = {"camera_controls": self._cache.copy(),
                                               "alignment": "LSB", "simulated": bool(self.device and self.device.simulated)}
            if frame.contains_chunk_data():
                def read_chunk(features: Any) -> None:
                    values = {}
                    for name in ("ChunkExposureTime", "ChunkExposureTimeAbs", "ChunkGain", "ChunkTimestamp"):
                        try:
                            feature = features.get_feature_by_name(name)
                            values[name] = {"value": feature.get(), "unit": feature.get_unit() or ""}
                        except self.sdk.VmbFeatureError:
                            continue
                    metadata_values["chunk"] = values
                frame.access_chunk_data(read_chunk)
            source = frame
            if fmt in PACKED_TARGETS:
                source = frame.convert_pixel_format(getattr(self.sdk.PixelFormat, PACKED_TARGETS[fmt]))
            array = source.as_numpy_ndarray()
            if array.ndim == 3 and array.shape[2] == 1:
                array = array[:, :, 0]
            # The SDK may overwrite its buffer immediately after queue_frame().
            owned = np.array(array, copy=True, order="C")
            device = self.device
            if device is None:
                return
            packet = FrameData(owned, frame.get_id(), MONO_BITS[fmt], fmt,
                               device.id, device.model, device.serial,
                               host_received_utc=received_utc, received_monotonic=received_at,
                               camera_timestamp=frame.get_timestamp(), metadata=metadata_values)
            self.publish(packet)
            self.counters.add(True)
        except Exception as exc:
            self.counters.add(False)
            with self._error_lock:
                self._callback_error = f"Frame conversion failed: {exc}"
        finally:
            try:
                camera.queue_frame(frame)
            except Exception:
                if self.streaming:
                    LOG.exception("Could not requeue an SDK frame")

    def tick(self) -> None:
        if self._lost and self.camera is not None:
            self.close()
            self._lost = False
            raise ConnectionError("Camera disconnected. Reconnect it, refresh the list, and connect again.")
        with self._error_lock:
            error, self._callback_error = self._callback_error, None
        if error:
            raise RuntimeError(error)

    def preset(self, path: str, load: bool) -> None:
        if self.camera is None:
            raise RuntimeError("Connect a camera first.")
        was_streaming = self.streaming
        self.stop()
        try:
            method = self.camera.load_settings if load else self.camera.save_settings
            method(path)
        finally:
            if was_streaming:
                self.start()
        self.controls()


class DemoBackend:
    """Synthetic 12-bit images. This is not the vendor camera simulator."""

    diagnostics = "Synthetic demo source. No camera, SDK, driver, or temperature measurement is involved."

    def __init__(self, publish: Callable[[FrameData], None], cti_path: str = ""):
        self.publish = publish
        self.device: DeviceInfo | None = None
        self.streaming = False
        self.counters = Counters()
        self.values: dict[str, Any] = {"exposure": 10000.0, "gain": 0.0, "fps": 30.0,
            "exposure_auto": "Off", "gain_auto": "Off", "fps_enabled": True,
            "width": 640, "height": 512, "offset_x": 0, "offset_y": 0,
            "pixel_format": "Mono12", "demo_pattern": "Low contrast"}
        self.rng = np.random.default_rng(2026)
        self.frame_id = 0
        self._next_frame = 0.0

    def list_devices(self) -> list[DeviceInfo]:
        return [DeviceInfo("demo-001", "Synthetic monochrome demo", "DEMO-001", "Software", True)]

    def open(self, device_id: str) -> None:
        if device_id != "demo-001":
            raise ValueError("Unknown demo device.")
        self.device = self.list_devices()[0]

    def close(self) -> None:
        self.stop()
        self.device = None

    def shutdown(self) -> None:
        self.close()

    def controls(self) -> dict[str, ControlInfo]:
        result = {
            "exposure": ControlInfo("exposure", "ExposureTime", self.values["exposure"],
                minimum=50, maximum=1000000, increment=1, unit="us",
                writable=self.values["exposure_auto"] == "Off"),
            "gain": ControlInfo("gain", "Gain", self.values["gain"], maximum=24, increment=0.1,
                unit="dB", writable=self.values["gain_auto"] == "Off"),
            "fps": ControlInfo("fps", "AcquisitionFrameRate", self.values["fps"], minimum=1, maximum=60,
                increment=0.1, unit="Hz", writable=self.values["fps_enabled"]),
            "fps_enabled": ControlInfo("fps_enabled", "AcquisitionFrameRateEnable",
                self.values["fps_enabled"], kind="bool"),
        }
        for key, name in (("exposure_auto", "ExposureAuto"), ("gain_auto", "GainAuto")):
            result[key] = ControlInfo(key, name, self.values[key], kind="enum", entries=("Off", "Once", "Continuous"))
        for key, maximum in (("width", 1280), ("height", 1024), ("offset_x", 1280-self.values["width"]),
                             ("offset_y", 1024-self.values["height"])):
            result[key] = ControlInfo(key, key, self.values[key], kind="int",
                minimum=64 if key in ("width", "height") else 0, maximum=maximum, increment=16)
        result["pixel_format"] = ControlInfo("pixel_format", "PixelFormat", self.values["pixel_format"],
            kind="enum", entries=("Mono8", "Mono10", "Mono12", "Mono14", "Mono16"))
        result["demo_pattern"] = ControlInfo("demo_pattern", "Demo pattern", self.values["demo_pattern"],
            kind="enum", entries=("Low contrast", "Gradient", "Constant"))
        return result

    def write(self, key: str, value: Any) -> None:
        info = self.controls()[key]
        if not info.writable:
            raise RuntimeError(f"{info.name} is read-only while its automatic mode is active.")
        if info.kind == "enum" and value not in info.entries:
            raise ValueError("Unsupported parameter value.")
        if info.kind in ("float", "int"):
            value = quantize(float(value), info.minimum, info.maximum, info.increment)
            if info.kind == "int":
                value = int(value)
        self.values[key] = value
        if key == "width":
            self.values["offset_x"] = min(self.values["offset_x"], 1280-int(value))
        if key == "height":
            self.values["offset_y"] = min(self.values["offset_y"], 1024-int(value))

    def start(self) -> None:
        if self.device is None:
            raise RuntimeError("Connect a demo device first.")
        self.streaming = True
        self._next_frame = time.monotonic()

    def stop(self) -> None:
        self.streaming = False

    def tick(self) -> None:
        now = time.monotonic()
        if not self.streaming or now < self._next_frame:
            return
        exposure = float(self.values["exposure"])
        fps = float(self.values["fps"]) if self.values["fps_enabled"] else 60
        self._next_frame = now + max(1 / fps, exposure / 1e6)
        for key, manual, target in (("exposure_auto", "exposure", 10000.0), ("gain_auto", "gain", 0.0)):
            mode = self.values[key]
            if mode != "Off":
                self.values[manual] += (target - self.values[manual]) * 0.3
                if mode == "Once":
                    self.values[key] = "Off"
        height, width = int(self.values["height"]), int(self.values["width"])
        y, x = np.mgrid[:height, :width].astype(np.float32)
        x += int(self.values["offset_x"])
        y += int(self.values["offset_y"])
        phase = self.frame_id * 0.025
        pattern = self.values["demo_pattern"]
        if pattern == "Gradient":
            signal = (x % width) / max(1, width - 1) * 4095
        elif pattern == "Constant":
            signal = np.full((height, width), 1800, np.float32)
        else:
            signal = 1800 + 45 * np.sin(x / 26 + phase) + 20 * np.cos(y / 18)
            signal += 85 * np.exp(-((x-width*0.55)**2+(y-height*0.5)**2)/(width*0.18)**2)
            signal += 18 * ((np.sin(x/7) + np.cos(y/9)) > 0)
            signal += self.rng.normal(0, 2, signal.shape)
        maximum = (1 << MONO_BITS[str(self.values["pixel_format"])] ) - 1
        signal = signal * float(self.values["exposure"]) / 10000 * 10**(float(self.values["gain"])/20)
        data = np.clip(np.rint(signal / 4095 * maximum), 0, maximum)
        data = data.astype(np.uint8 if maximum == 255 else np.uint16)
        self.frame_id += 1
        device = self.device
        assert device is not None
        self.publish(FrameData(data, self.frame_id, MONO_BITS[str(self.values["pixel_format"])],
            str(self.values["pixel_format"]), device.id, device.model, device.serial,
            metadata={"simulated": True, "source": "synthetic demo, not physical camera data",
                      "alignment": "LSB", "camera_controls": self.values.copy()}))
        self.counters.add(True)

    def preset(self, path: str, load: bool) -> None:
        raise RuntimeError("Camera XML presets require a Vimba device. Use display presets for the demo.")
