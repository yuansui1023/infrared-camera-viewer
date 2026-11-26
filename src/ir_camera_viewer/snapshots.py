"""Snapshot the displayed frame and settings, then write TIFF outside the GUI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import json
import os
import re
import tempfile

import tifffile
from PySide6.QtCore import QObject, Signal

from .geometry import oriented_pixels
from .imaging import RenderPacket
from .models import Orientation, utc_now


@dataclass(frozen=True)
class SnapshotRequest:
    packet: RenderPacket
    orientation: Orientation
    directory: Path
    prefix: str = "capture"
    mode: str = "display"
    requested_utc: str = ""


def safe_prefix(prefix: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", prefix).strip(" .")[:80] or "capture"


def save_snapshot(request: SnapshotRequest) -> Path:
    if request.mode not in ("display", "raw"):
        raise ValueError("Unknown snapshot mode.")
    directory = request.directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    packet = request.packet
    frame = packet.frame
    image = frame.pixels if request.mode == "raw" else oriented_pixels(packet.image, request.orientation)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    frame_name = frame.frame_id if frame.frame_id is not None else "unknown"
    stem = f"{safe_prefix(request.prefix)}_{timestamp}_f{frame_name}"
    target = directory / (stem + ".tiff")
    counter = 0
    while True:
        try:
            with target.open("xb"):
                pass
            break
        except FileExistsError:
            counter += 1
            target = directory / f"{stem}_{counter}.tiff"
    temporary: str | None = None
    try:
        handle, temporary = tempfile.mkstemp(prefix=".ir-snapshot-", suffix=".tmp", dir=directory)
        os.close(handle)
        description = {
            "application": "Infrared Camera Viewer", "schema_version": 1,
            "save_mode": "original_grayscale" if request.mode == "raw" else "display_appearance",
            "host_received_utc": frame.host_received_utc,
            "snapshot_requested_utc": request.requested_utc or utc_now(), "saved_utc": utc_now(),
            "frame_id": frame.frame_id, "camera_id": frame.camera_id,
            "camera_model": frame.model, "camera_serial": frame.serial,
            "source_pixel_format": frame.pixel_format, "source_effective_bit_depth": frame.bit_depth,
            "saved_storage_bits": image.dtype.itemsize * 8,
            "camera_timestamp": frame.camera_timestamp,
            "camera_timestamp_units": "device ticks; frequency not supplied" if frame.camera_timestamp is not None else None,
            "source_metadata": frame.metadata, "display": asdict(packet.options),
            "orientation": asdict(request.orientation),
            "orientation_applied_to_pixels": request.mode == "display",
        }
        tifffile.imwrite(temporary, image, photometric="minisblack", metadata=None,
                         description=json.dumps(description, ensure_ascii=True),
                         software="Infrared Camera Viewer 0.1.0")
        os.replace(temporary, target)
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise


class SnapshotService(QObject):
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tiff-save")
        self._lock = Lock()
        self._pending = 0
        self._accepting = True

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def submit(self, request: SnapshotRequest) -> bool:
        with self._lock:
            if not self._accepting or self._pending >= 3:
                self.failed.emit("Three snapshots are already pending. Wait for the current saves.")
                return False
            self._pending += 1
        try:
            future = self._executor.submit(save_snapshot, request)
        except Exception:
            with self._lock:
                self._pending -= 1
            raise

        def completed(result) -> None:
            try:
                self.saved.emit(str(result.result()))
            except Exception as exc:
                self.failed.emit(f"Could not save TIFF: {exc}")
            finally:
                with self._lock:
                    self._pending -= 1

        future.add_done_callback(completed)
        return True

    def stop_accepting(self) -> None:
        with self._lock:
            self._accepting = False
        self._executor.shutdown(wait=False, cancel_futures=False)
