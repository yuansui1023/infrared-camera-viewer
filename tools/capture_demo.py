"""Capture the application's synthetic demo for documentation, without hardware."""

from pathlib import Path
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from PySide6.QtWidgets import QApplication
from ir_camera_viewer.window import MainWindow


def main() -> int:
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images/demo.png")
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as directory:
        window = MainWindow(Path(directory)/"settings.json", demo=True)
        window.show()
        deadline = time.monotonic()+10
        while (window.packet is None or window.packet.frame.frame_id < 25) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        try:
            if window.packet is None:
                raise RuntimeError("The demo did not produce an image.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(destination)):
                raise RuntimeError("Could not write the demo screenshot.")
            print(destination.resolve())
        finally:
            window.close()
            deadline = time.monotonic()+10
            while not window._shutdown_complete and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            if not window._shutdown_complete:
                raise RuntimeError("The demo failed to shut down cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
