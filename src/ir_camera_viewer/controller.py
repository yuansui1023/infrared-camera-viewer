"""Serialized hardware control without image-bearing Qt event queues."""

from __future__ import annotations

from collections import deque
from threading import Condition
from typing import Any
import logging
import time

from PySide6.QtCore import QThread, Signal

from .backends import DemoBackend, VimbaBackend
from .models import FrameData, LatestSlot

LOG = logging.getLogger(__name__)


class CameraController(QThread):
    devices_changed = Signal(object)
    controls_changed = Signal(object)
    state_changed = Signal(object)
    message = Signal(str)
    error = Signal(str)
    diagnostics_changed = Signal(str)

    def __init__(self, frames: LatestSlot[FrameData]):
        super().__init__()
        self.frames = frames
        self._condition = Condition()
        self._commands: deque[tuple[str, dict[str, Any]]] = deque()
        self._writes: dict[str, Any] = {}
        self._quit = False
        self._backend: DemoBackend | VimbaBackend | None = None
        self._kind = ""
        self._cti = ""
        self._diagnostics = "Camera SDK has not been initialized."

    def submit(self, command: str, **arguments: Any) -> None:
        with self._condition:
            if self._quit:
                return
            if command == "write":
                self._writes[arguments["key"]] = arguments["value"]
            else:
                if len(self._commands) >= 64:
                    self.error.emit("Too many pending camera operations. Wait for the current operation.")
                    return
                self._commands.append((command, arguments))
            self._condition.notify()

    def shutdown(self) -> None:
        with self._condition:
            self._quit = True
            self._condition.notify()

    def _emit_state(self) -> None:
        backend = self._backend
        complete, incomplete = backend.counters.read() if backend else (0, 0)
        self.state_changed.emit({"device": backend.device if backend else None,
            "streaming": bool(backend and backend.streaming), "complete": complete,
            "incomplete": incomplete, "backend": self._kind})

    def _select(self, kind: str, cti: str) -> None:
        if self._backend is not None and (kind != self._kind or cti != self._cti):
            # A path change is explicit. Normal refresh/start/stop keeps the API session alive.
            self._backend.shutdown()
            self._backend = None
            self.frames.clear()
        if self._backend is None:
            factory = DemoBackend if kind == "demo" else VimbaBackend
            self._backend = factory(self.frames.publish, cti)
            self._kind, self._cti = kind, cti
            self._diagnostics = self._backend.diagnostics
        self.diagnostics_changed.emit(self._diagnostics)

    def _execute(self, command: str, args: dict[str, Any]) -> None:
        if command == "scan":
            self._select(args.get("kind", "vimba"), args.get("cti", ""))
            assert self._backend is not None
            self.devices_changed.emit(self._backend.list_devices())
        elif command == "connect":
            self._select(args.get("kind", "vimba"), args.get("cti", ""))
            assert self._backend is not None
            self._backend.open(args["device_id"])
            self.frames.clear()
            self.controls_changed.emit(self._backend.controls())
            if args.get("start", False):
                self._backend.start()
                self.message.emit("Connected. Acquiring live images.")
            else:
                self.message.emit("Connected. Start acquisition to view images.")
        elif self._backend is None:
            raise RuntimeError("Select a source and refresh the device list first.")
        elif command == "disconnect":
            self._backend.close()
            self.controls_changed.emit({})
            with self._condition:
                self._writes.clear()
            self.message.emit("Disconnected. The last displayed image is retained.")
        elif command == "start":
            self._backend.start()
            self.controls_changed.emit(self._backend.controls())
        elif command == "stop":
            self._backend.stop()
            self.controls_changed.emit(self._backend.controls())
            self.message.emit("Acquisition stopped. The last image remains available.")
        elif command == "write":
            self._backend.write(args["key"], args["value"])
            self.controls_changed.emit(self._backend.controls())
        elif command in ("load_xml", "save_xml"):
            self._backend.preset(args["path"], command == "load_xml")
            self.controls_changed.emit(self._backend.controls())
            self.message.emit("Camera XML preset loaded." if command == "load_xml" else "Camera XML preset saved.")
        self._emit_state()

    def run(self) -> None:
        next_poll = time.monotonic()
        last_error = ""
        last_error_time = 0.0
        try:
            while True:
                with self._condition:
                    if not self._commands and not self._writes and not self._quit:
                        self._condition.wait(0.015)
                    if self._quit:
                        break
                    if self._commands:
                        command, arguments = self._commands.popleft()
                    elif self._writes:
                        key = next(iter(self._writes))
                        command, arguments = "write", {"key": key, "value": self._writes.pop(key)}
                    else:
                        command, arguments = "", {}
                try:
                    if command:
                        self._execute(command, arguments)
                    if self._backend:
                        self._backend.tick()
                    now = time.monotonic()
                    if now >= next_poll:
                        self._emit_state()
                        if self._backend and self._backend.device:
                            self.controls_changed.emit(self._backend.controls())
                        next_poll = now + 1.0
                except Exception as exc:
                    LOG.exception("Camera operation failed")
                    now = time.monotonic()
                    text = str(exc)
                    if text != last_error or now-last_error_time >= 3:
                        self.error.emit(text)
                        last_error, last_error_time = text, now
                    if command == "scan":
                        self.devices_changed.emit([])
                    self._emit_state()
        finally:
            if self._backend:
                try:
                    self._backend.shutdown()
                except Exception:
                    LOG.exception("Camera shutdown failed")
