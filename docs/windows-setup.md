# Windows setup

## Requirements

- Windows x64.
- Allied Vision Vimba X 2026-3 with the camera's transport layer and driver.
- Python 3.12 x64 for source installation. The supplied VmbPy 1.2.3 supports Python 3.10 through 3.14. Python 3.15 and 32-bit Python are not supported by this setup.
- A working camera connection. Install the Microsoft Visual C++ runtime if requested by the vendor installer.

## Source installation

Extract the complete repository into a writable directory. Install Python with the launcher enabled, then double-click `Setup-Windows.cmd`. It creates a virtual environment, bootstraps pip, and installs the application. It looks for the VmbPy wheel under `%ProgramFiles%\Allied Vision\Vimba X\api\python`.

For another SDK directory, open a terminal in the project and run:

```bat
Setup-Windows.cmd "C:\path\to\Vimba X"
```

Open `Demo-Windows.cmd` for synthetic images. This does not exercise the vendor SDK. Open `Launch-Windows.cmd` for a real camera.

Manual installation:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m ensurepip --upgrade
.venv\Scripts\python -m pip install .
.venv\Scripts\python -m pip install "C:\path\to\Vimba X\api\python\vmbpy-1.2.3-py3-none-win_amd64.whl"
.venv\Scripts\python -m ir_camera_viewer
```

## Connect a camera

1. Confirm the camera works in the vendor viewer, then close that viewer to release it.
2. Select the Vimba X source. If discovery fails, use **Tools > SDK / transport-layer path** to select the SDK installation or its `cti` directory.
3. Refresh devices, select one, and click **Connect**. Acquisition starts automatically. The application selects continuous acquisition and disables triggering where available and writable.
4. Click **Auto Once** for a useful display range. This changes contrast mapping, not exposure.
5. Adjust exposure or gain. Disable its automatic mode before making manual adjustments. Unsupported controls are disabled.

Pixel format and ROI changes may stop and restart acquisition. Controls use the device's actual ranges, increments, and writable state. The official Vimba simulator is a transport-layer device, separate from the application's synthetic demo.

## Windows bundle

A successful Windows CI run provides an `InfraredCameraViewer-Windows-x64` artifact. Extract the ZIP and retain the entire application folder, including supporting files. Run `InfraredCameraViewer.exe`. Install vendor transport layers and camera drivers separately for hardware acquisition.

To build locally, install `.[camera,build]` in a Windows x64 Python environment and run:

```powershell
./Build-Windows.ps1 -Python .venv\Scripts\python.exe
```

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Python or pip cannot be found | Install Python 3.12 x64 with the launcher; rerun setup. Setup uses ensurepip automatically. |
| Existing virtual environment is incompatible | Rename `.venv` and rerun setup with a supported Python version. |
| VmbPy missing or wrong version | Install the supplied `vmbpy-1.2.3-py3-none-win_amd64.whl` with `.venv\Scripts\python -m pip`. |
| No devices | Check transport path, drivers, cable, power, and vendor viewer discovery. For GigE, check camera and adapter network configuration. |
| Camera is busy | Close other programs using it, then reconnect. |
| Manual exposure or gain disabled | Disable its automatic mode. Some cameras also lock features during acquisition. |
| No complete frames | Check exposure duration, transport settings, and connection. Read diagnostics for SDK errors. |
| Image black or washed out | Use Auto Once, then inspect raw DN values and exposure. |
| Original TIFF looks dark elsewhere | Native values are preserved. Use high-bit-depth level controls or save Display appearance. |

**Tools > Diagnostics** shows source, SDK information, settings path, and diagnostic details. The log is alongside the settings JSON with a `.log` extension. Display presets are portable JSON; camera settings use vendor XML and depend on device capabilities.
