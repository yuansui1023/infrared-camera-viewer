"""Immutable frame and display state shared across worker boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Generic, TypeVar
import math
import time

import numpy as np


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class FrameData:
    pixels: np.ndarray
    frame_id: int | None
    bit_depth: int
    pixel_format: str
    camera_id: str = ""
    model: str = ""
    serial: str = ""
    host_received_utc: str = field(default_factory=utc_now)
    received_monotonic: float = field(default_factory=time.monotonic)
    camera_timestamp: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pixels.ndim != 2 or not self.pixels.size:
            raise ValueError("A frame must contain a nonempty 2D monochrome image.")
        if self.pixels.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
            raise ValueError("Only uint8 and uint16 monochrome images are supported.")
        if not 1 <= self.bit_depth <= self.pixels.dtype.itemsize * 8:
            raise ValueError("The effective bit depth does not fit the image storage.")
        self.pixels.setflags(write=False)

    @property
    def maximum(self) -> int:
        return (1 << self.bit_depth) - 1


@dataclass(frozen=True)
class DisplayOptions:
    black: float = 0.0
    white: float = 4095.0
    gamma: float = 1.0
    invert: bool = False
    mode: str = "continuous"
    low_percentile: float = 1.0
    high_percentile: float = 99.0
    smoothing_seconds: float = 0.5

    def __post_init__(self) -> None:
        if type(self.invert) is not bool:
            raise ValueError("Grayscale inversion must be a boolean.")
        numbers = (self.black, self.white, self.gamma, self.low_percentile,
                   self.high_percentile, self.smoothing_seconds)
        if not all(math.isfinite(v) for v in numbers):
            raise ValueError("Display parameters must be finite numbers.")
        if not 0 <= self.black < self.white <= 65535:
            raise ValueError("Black level must be below white level within 0..65535 DN.")
        if not 0.1 <= self.gamma <= 5.0:
            raise ValueError("Display gamma must be between 0.1 and 5.0.")
        if not 0 <= self.low_percentile < self.high_percentile <= 100:
            raise ValueError("Automatic contrast percentiles must be ordered within 0..100.")
        if not 0 <= self.smoothing_seconds <= 10:
            raise ValueError("Smoothing must be between 0 and 10 seconds.")
        if self.mode not in ("manual", "once", "continuous"):
            raise ValueError("Unknown display contrast mode.")


@dataclass(frozen=True)
class Orientation:
    angle: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False

    def __post_init__(self) -> None:
        if type(self.flip_horizontal) is not bool or type(self.flip_vertical) is not bool:
            raise ValueError("Image flip settings must be booleans.")
        if not math.isfinite(self.angle) or abs(self.angle) > 36000:
            raise ValueError("The rotation angle must be finite and within 100 turns.")


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    model: str
    serial: str = ""
    interface: str = ""
    simulated: bool = False

    @property
    def label(self) -> str:
        suffix = f" - {self.serial}" if self.serial else ""
        return f"{self.model}{suffix}" + (" [SIMULATED]" if self.simulated else "")


@dataclass(frozen=True)
class ControlInfo:
    key: str
    name: str
    value: Any
    kind: str = "float"
    minimum: float = 0.0
    maximum: float = 1.0
    increment: float | None = None
    unit: str = ""
    writable: bool = True
    entries: tuple[str, ...] = ()
    description: str = ""


T = TypeVar("T")


class LatestSlot(Generic[T]):
    """A bounded single-item mailbox; publishing never waits for a consumer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._item: T | None = None
        self._sequence = 0
        self._read_sequence = 0
        self._overwritten = 0

    def publish(self, item: T) -> int:
        with self._lock:
            if self._item is not None and self._sequence > self._read_sequence:
                self._overwritten += 1
            self._sequence += 1
            self._item = item
            return self._sequence

    def read(self, after: int = -1) -> tuple[int, T | None]:
        with self._lock:
            if self._sequence == after:
                return self._sequence, None
            self._read_sequence = self._sequence
            return self._sequence, self._item

    def clear(self) -> None:
        with self._lock:
            self._item = None
            self._sequence += 1
            self._read_sequence = self._sequence

    @property
    def overwritten(self) -> int:
        with self._lock:
            return self._overwritten


def display_dict(options: DisplayOptions) -> dict[str, Any]:
    return asdict(options)
