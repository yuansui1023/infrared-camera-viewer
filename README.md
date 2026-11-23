# Infrared Camera Viewer

A planned Windows x64 desktop application for viewing Allied Vision infrared monochrome cameras through Vimba X.

**Status: specification only.** The SDK package has been inspected and the implementation requirements are documented. Application code, Windows builds, and physical-camera validation have not started.

## Planned features

- Live monochrome preview with zoom, pan, fit-to-window, native size, and fullscreen.
- Rotation in 90-degree steps or by an arbitrary angle, horizontal/vertical flips, and orientation reset.
- Live raw-data histogram with adjustable black/white levels, display gamma, inversion, and automatic contrast.
- Exposure, gain, frame rate, and other controls discovered from the connected camera.
- Freeze/resume preview independently of acquisition.
- TIFF snapshots: either the current display appearance or original grayscale data.
- Correct handling of high-bit-depth and packed monochrome formats.
- English-only interface, documentation, comments, and messages.

Video recording, drawing, annotation, measurement, and image-editing tools are outside the scope.

## Proposed stack

| Component | Selection |
| --- | --- |
| Target | Windows x64 |
| Python | 3.12 x64 |
| Camera SDK | Allied Vision Vimba X 2026-3 |
| Python camera API | Bundled VmbPy 1.2.3 |
| Desktop UI | PySide6 |
| Image data | NumPy |
| Preview and histogram | pyqtgraph |
| TIFF output | tifffile |

The Python wheel includes native API libraries, but transport layers and camera drivers still need to be installed or configured on the Windows machine.

## Project documents

- [Development prompt](docs/development-prompt.md): implementation-ready product requirements, SDK integration constraints, and acceptance criteria.
- [SDK assessment](docs/sdk-assessment.md): findings from the supplied SDK directory.
- [Contributor instructions](AGENTS.md): language, scope, and implementation constraints.

## Snapshot behavior

Each click saves one TIFF file using the selected mode:

- **Display appearance (default):** an 8-bit grayscale TIFF with the current levels, gamma, inversion, rotation, and flips, rendered at full image resolution.
- **Original grayscale:** unchanged pixel values in uint8 or uint16 storage as appropriate, preserving the effective source bit depth.

The snapshot uses the frame currently displayed, including a frozen frame. It does not acquire a different frame or capture the application window.

## Development status

The current task is to establish the repository and preserve the approved planning work. Implementation requires a separate request.

The exact physical camera model and interface remain unconfirmed. The simulator configuration in the supplied package is not evidence of the physical device.

## SDK distribution

The vendor SDK, drivers, native binaries, license-manager files, local camera settings, and captured images are not included in this repository. Obtain and install the matching SDK separately on the target Windows machine.
