"""Full-precision display mapping and bounded background processing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Event, Lock, Thread
import math
import time

import numpy as np

from .models import DisplayOptions, FrameData, LatestSlot


def sample_pixels(image: np.ndarray, limit: int = 131072) -> np.ndarray:
    flat = image.reshape(-1)
    return flat[::max(1, math.ceil(flat.size / limit))]


def automatic_levels(sample: np.ndarray, maximum: int, low: float, high: float) -> tuple[float, float]:
    if not sample.size:
        return 0.0, float(maximum)
    black, white = (float(v) for v in np.percentile(sample, (low, high)))
    black = min(max(black, 0.0), float(maximum - 1))
    white = min(max(white, black + 1.0), float(maximum))
    return black, white


def tone_map(image: np.ndarray, options: DisplayOptions) -> np.ndarray:
    """Use a DN-indexed LUT; the input image is never modified."""
    maximum = 255 if image.dtype == np.uint8 else 65535
    domain = np.arange(maximum + 1, dtype=np.float32)
    values = np.clip((domain - options.black) / (options.white - options.black), 0, 1)
    values = values ** (1.0 / options.gamma)
    if options.invert:
        values = 1.0 - values
    table = np.rint(values * 255).astype(np.uint8)
    return table[image]


@dataclass(frozen=True)
class HistogramData:
    edges: np.ndarray
    counts: np.ndarray
    minimum: int
    maximum: int
    mean: float
    clipped_low: float
    clipped_high: float


def histogram(sample: np.ndarray, bit_depth: int, options: DisplayOptions) -> HistogramData:
    shift = max(0, bit_depth - 10)
    bins = 1 << min(bit_depth, 10)
    indices = np.minimum(sample.astype(np.uint32) >> shift, bins - 1)
    counts = np.bincount(indices, minlength=bins)
    edges = np.arange(bins + 1, dtype=np.float64) * (1 << shift)
    return HistogramData(edges, counts, int(sample.min()), int(sample.max()),
                         float(sample.mean()), float(np.mean(sample <= options.black)),
                         float(np.mean(sample >= options.white)))


@dataclass(frozen=True)
class RenderPacket:
    frame: FrameData
    image: np.ndarray
    options: DisplayOptions
    histogram: HistogramData
    revision: int
    processing_ms: float


class ImageProcessor(Thread):
    """Process the latest frame, or the exact frame pinned by Freeze."""

    def __init__(self, source: LatestSlot[FrameData], options: DisplayOptions | None = None):
        super().__init__(name="image-processing", daemon=True)
        self.source = source
        self.output: LatestSlot[RenderPacket] = LatestSlot()
        self._lock = Lock()
        self._wake = Event()
        self._stop_event = Event()
        self._options = options or DisplayOptions()
        self._frozen: FrameData | None = None
        self._revision = 0
        self._error: str | None = None

    def configure(self, options: DisplayOptions) -> int:
        with self._lock:
            self._options = options
            self._revision += 1
            revision = self._revision
        self._wake.set()
        return revision

    def freeze(self, frame: FrameData | None) -> int:
        with self._lock:
            self._frozen = frame
            self._revision += 1
            revision = self._revision
        self._wake.set()
        return revision

    def take_error(self) -> str | None:
        with self._lock:
            error, self._error = self._error, None
        return error

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def run(self) -> None:
        sequence = -1
        processed_revision = -1
        current: FrameData | None = None
        smoothed: tuple[float, float] | None = None
        last_time: float | None = None
        last_format: tuple[str, int, str] | None = None
        while not self._stop_event.is_set():
            self._wake.wait(1 / 30)
            self._wake.clear()
            with self._lock:
                options, frozen, revision = self._options, self._frozen, self._revision
            incoming_sequence, incoming = self.source.read(sequence)
            if frozen is not None:
                candidate = frozen
            else:
                candidate = incoming if incoming is not None else current
            if candidate is None:
                sequence = incoming_sequence
                continue
            if candidate is current and revision == processed_revision:
                sequence = incoming_sequence
                continue
            started = time.monotonic()
            try:
                identity = (candidate.camera_id, candidate.bit_depth, candidate.pixel_format)
                if identity != last_format and options.mode == "manual" and options.white > candidate.maximum:
                    options = replace(options, black=0, white=candidate.maximum)
                    with self._lock:
                        if revision == self._revision:
                            self._options = options
                if identity != last_format or options.mode == "manual":
                    smoothed = None
                    last_time = None
                sample = sample_pixels(candidate.pixels)
                effective = options
                if options.mode != "manual":
                    target = automatic_levels(sample, candidate.maximum, options.low_percentile,
                                              options.high_percentile)
                    if smoothed is not None and options.mode == "continuous":
                        dt = max(0.0, started - (last_time or started))
                        alpha = 1.0 if not options.smoothing_seconds else -math.expm1(-dt / options.smoothing_seconds)
                        target = tuple(a + alpha * (b - a) for a, b in zip(smoothed, target))
                    smoothed, last_time = target, started
                    effective = replace(options, black=target[0], white=target[1],
                                        mode="manual" if options.mode == "once" else options.mode)
                image = tone_map(candidate.pixels, effective)
                packet = RenderPacket(candidate, image, effective,
                                      histogram(sample, candidate.bit_depth, effective), revision,
                                      (time.monotonic() - started) * 1000)
                with self._lock:
                    if revision != self._revision:
                        continue
                    if options.mode == "once":
                        self._options = effective
                self.output.publish(packet)
                current, sequence = candidate, incoming_sequence
                processed_revision, last_format = revision, identity
            except Exception as exc:
                with self._lock:
                    self._error = f"Image processing failed: {exc}"
                current, sequence, processed_revision = candidate, incoming_sequence, revision
