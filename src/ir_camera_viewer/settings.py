"""Versioned JSON preferences and portable display presets."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import logging
import os
import tempfile

from .models import DisplayOptions, Orientation

LOG = logging.getLogger(__name__)


def atomic_json(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".viewer-settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(values, stream, indent=2, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("This settings file is unexpectedly large.")
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError("Unsupported settings file format.")
    return result


def load_preferences(path: Path) -> dict:
    try:
        values = read_json(path)
        DisplayOptions(**values.get("display", {}))
        Orientation(**values.get("orientation", {}))
        return values
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        LOG.info("Using default preferences: %s", exc)
        return {}


def save_display_preset(path: Path, display: DisplayOptions, orientation: Orientation) -> None:
    atomic_json(path, {"schema_version": 1, "type": "display_preset",
                       "display": asdict(display), "orientation": asdict(orientation)})


def load_display_preset(path: Path) -> tuple[DisplayOptions, Orientation]:
    values = read_json(path)
    if values.get("type") != "display_preset":
        raise ValueError("Choose a display preset exported by this application.")
    return DisplayOptions(**values["display"]), Orientation(**values["orientation"])
