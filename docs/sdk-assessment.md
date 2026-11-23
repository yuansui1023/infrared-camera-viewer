# SDK Assessment

## Inspection scope

The supplied local Allied Vision directory was inspected before preparing this specification. The complete directory inventory contained 962 files, excluding macOS metadata. The relevant SDK documentation, Python API source inside the wheel, package metadata, transport-layer configuration, and simulator presets were examined. The Python wheel and .NET package also passed ZIP integrity checks.

The directory is a copy of an installed SDK, not an existing application backend. Windows executables were not run on the development Mac. No physical camera was connected or validated during this review.

## Verified versions

| Component | Version or package |
| --- | --- |
| SDK release | Vimba X 2026-3 |
| VmbPy | vmbpy-1.2.3-py3-none-win_amd64.whl |
| Python range documented by the bundled README | 3.10 through 3.14 |
| VmbNET | VmbNET.win-x64.1.3.4.nupkg |
| VmbC | 1.3.2 |
| VmbCPP | 1.3.2 |
| Image Transform | 2.3.0 |
| VmbNUC | 1.0.1 |

## Available components

The SDK contains C/C++ headers and C++ API source, a Python wheel, a .NET NuGet package, native DLLs, a viewer, driver tools, and compiled acquisition examples.

The cti directory includes GigE, USB, Camera Simulator, Camera Link configuration, and RDMA transport-layer components. Availability in the SDK does not identify the physical camera connection.

The separate example-source directory is absent from this copy. The SDK documentation gives its default Windows location as:

`C:\Users\Public\Documents\Allied Vision\Vimba X\Examples`

Do not assume standalone files such as asynchronous_grab.py or list_cameras.py are present in the supplied directory. The Python manual and API README contain usable acquisition examples.

## Inspected documentation

Paths below are relative to the Vimba X installation directory:

- api/python/README.md
- api/dotnet/README.md
- doc/Windows.html
- doc/pythonAPIManual.html
- doc/sdkManual.html
- doc/imagetransformManual.html
- doc/viewerGuide.html
- doc/camSimulator.html
- doc/examplesOverview.html
- doc/nucAPIManual.html
- doc/installation.html
- doc/compatibility.html
- doc/toolsCompatibility.html

Relevant Python sources inside the wheel include camera.py, frame.py, feature.py, featurecontainer.py, vmbsystem.py, and the image-transform bindings.

## Acquisition and buffer ownership

VmbPy exposes Camera.start_streaming(handler, buffer_count=...), Camera.stop_streaming(), and Camera.queue_frame(frame). The streaming handler receives (cam, stream, frame).

Frame.as_numpy_ndarray() constructs a view of the SDK frame buffer. Unpacked monochrome arrays have a trailing single-channel dimension. Keeping a NumPy view alive does not prevent the SDK from overwriting a buffer that has been requeued.

Copy the data or safely copy the Frame before requeueing it. When reading Chunk metadata, do so while the original acquired frame is still valid. Pixel-format conversion returns a new Frame without the original Chunk data.

The Windows release notes recommend one API session per process rather than excessive startup/shutdown cycles.

## Monochrome formats

The Python API lists Mono8, Mono10, Mono10p, Mono12, Mono12p, Mono12Packed, Mono14, and Mono16.

The direct NumPy mapping includes Mono8 and the unpacked Mono10/12/14/16 formats. Packed formats must first be converted through the SDK to a compatible unpacked representation. Mono12p and Mono12Packed have different layouts.

The Image Transform manual documents little-endian multi-byte transformations and least-significant-bit alignment when increasing bit depth. Effective source bit depth must be recorded separately from uint16 storage.

## Features and configuration

Camera features are discovered from device XML. The Python API exposes range, increment, unit, read/write access, and available-enumeration queries. Floating-point increments can be None.

Pixel format must be changed through the Camera convenience methods while acquisition is stopped. Other feature names and write permissions depend on the actual camera and state.

The installed bin/VmbC.xml contains a default transport-layer path:

`C:\Program Files\Allied Vision\Vimba X\cti`

The wheel includes a separate VmbC.xml with different path configuration. VmbSystem.set_path_configuration() can supply explicit paths before startup.

The wheel includes VmbC and Image Transform DLLs, but not camera transport layers or device drivers. Copying this SDK directory does not prove that the destination machine has a correct driver installation.

The bundled viewer uses Qt5. Its Qt DLLs and plugins must not contaminate the PySide6/Qt6 runtime.

## Simulator configuration

The supplied simulator XML selects preset G1-030_VSWIR for Cam1. The adjacent JSON defines that preset as:

| Property | Value |
| --- | --- |
| Width | 656 |
| Height | 520 |
| Sensor | mono |

This is a virtual-camera configuration, not proof of the physical camera model, resolution, interface, frame rate, or feature set. The simulator documentation explicitly distinguishes simulated capabilities and timing from hardware.

## Limits and open facts

- Physical camera model, serial number, interface, supported bit depths, and exposure limits remain unconfirmed.
- SDK support for Goldeye/NUC does not establish that the user's camera belongs to that family or supports radiometric temperature measurement.
- The supplied viewer defaults to 8-bit saves unless its 16-bit TIFF option is explicitly enabled; the new application must define its own clear snapshot modes.
- SDK XML persistence does not preserve every hardware LUT setting, so application display settings need separate persistence.
