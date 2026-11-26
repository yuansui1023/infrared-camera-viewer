# Validation

## Local results

The 40-test suite passed on macOS with Python 3.13.11, NumPy 2.4.4, PySide6 6.11.1, and pyqtgraph 0.13.7. UI tests use Qt's offscreen platform and synthetic data. They do not load Windows SDK DLLs.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

| Area | Coverage | Boundary |
| --- | --- | --- |
| Numerical | Narrow 12-bit ranges, percentiles, constant frames, gamma, inversion, DN histogram, bounded sampling | Generated arrays |
| Frame flow | Bounded mailbox, manual persistence, Auto Once, freeze identity, format changes | Application workers |
| TIFF | Exact raw DN and dtype, processed appearance, orientation, filenames, cleanup | Local write/read round trips |
| Geometry | Right angles and flips, cursor mapping, arbitrary rotation extent | Qt transforms and image data |
| Backend | Packed conversion, copy before requeue, incomplete frames, legacy features, locked changes, reconnect, disconnect | Injected SDK contract double |
| UI | Startup without SDK, exposure units, histogram override, freeze snapshots, restart, preferences, pending-save shutdown | Offscreen synthetic UI |
| Windows bundle | Real VmbPy import, PyInstaller build, frozen synthetic launch | GitHub Windows workflow; inspect its result |
| Official vendor simulator | Actual transport-layer discovery and acquisition | Not performed locally |
| Physical camera | Device controls, live performance, transport, reconnect | Not performed; requires Windows and camera |

`tools/capture_demo.py` generates the synthetic interface screenshot. Neither the demo nor mocked SDK tests establish physical-camera compatibility.

## Hardware acceptance

On Windows, verify discovery, live images, exposure/gain readback, automatic modes, available monochrome formats, ROI, and unplug/reconnect. Confirm source bit depth and packed-format conversion against the actual camera.

Check manual levels, gamma, Auto Once, and stable Continuous Auto on a narrow-intensity scene. Freeze an identifiable frame, save both TIFF modes, and verify raw values and processed appearance independently. Check rotation, flips, full-resolution saves, and long exposures. Run sustained acquisition while inspecting diagnostics.

Record the actual camera model, interface, SDK/driver versions, supported features, frame rate, and limitations. Simulator presets do not establish physical hardware specifications.
