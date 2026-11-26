"""Application entry point; importing the UI never imports Windows SDK DLLs."""

from __future__ import annotations

import argparse
from pathlib import Path
import logging
import os
import sys

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infrared Camera Viewer - live grayscale viewing and TIFF snapshots.")
    parser.add_argument("--demo", action="store_true", help="Start the explicitly synthetic demo source.")
    parser.add_argument("--settings", type=Path, help="Use a specific JSON preferences file.")
    parser.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from PySide6.QtCore import QCoreApplication, QStandardPaths, QTimer
    from PySide6.QtWidgets import QApplication
    from .window import MainWindow

    QCoreApplication.setOrganizationName("InfraredCameraViewer")
    QCoreApplication.setApplicationName("Infrared Camera Viewer")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    settings_path = args.settings or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))/"settings.json"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings_path.with_suffix(".log"), encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    window = MainWindow(settings_path, demo=args.demo)
    window.show()
    if args.quit_after is not None:
        QTimer.singleShot(max(100, int(args.quit_after*1000)), window.close)
    return app.exec()
