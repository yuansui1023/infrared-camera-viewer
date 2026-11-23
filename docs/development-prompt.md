# Development Prompt: Windows Infrared Camera Viewer

Use this specification when application implementation is explicitly authorized. The repository is currently in the planning phase; no application code or hardware validation is implied.

## Goal and scope

Develop an English-only Windows x64 desktop viewer for an Allied Vision infrared monochrome camera using the inspected Vimba X SDK. Prioritize responsive live viewing, easy exposure adjustment, interactive high-contrast display, and TIFF snapshots.

Support one active camera selected from a device list. Exclude video recording, drawing, annotations, measurement, layers, and general image-editing tools.

## 1. Verified SDK and chosen stack

Consult [SDK assessment](sdk-assessment.md) for facts established from the supplied package.

Use Python 3.12 x64, the bundled VmbPy 1.2.3 wheel, PySide6, NumPy, pyqtgraph, and tifffile. The supplied SDK release is Vimba X 2026-3. Do not silently replace the inspected camera API with an incompatible release.

The supplied directory is an installed SDK copy, not an existing business backend. Integrate camera calls behind an adapter; keep display mapping and UI behavior in the application.

The package also includes VmbNET 1.3.4, but do not maintain a second .NET implementation in the first release.

The actual physical camera is unknown. Discover its model, serial number, interface, pixel formats, and features at connection time. Do not infer hardware from the simulator's G1-030_VSWIR preset.

All user-facing labels, documentation, comments, tooltips, logs, and errors must be in English.

## 2. Window layout and image interaction

Use a quiet dark theme with a large central image viewport that preserves aspect ratio.

The top toolbar contains device selection, Refresh, Connect/Disconnect, Start/Stop Acquisition, Freeze/Resume Preview, Snapshot, and Fullscreen.

The right sidebar contains collapsible Camera Controls, Histogram and Display, and Snapshot Settings panels.

The status bar shows connection state, model, image dimensions, pixel format and effective bit depth, acquisition/display frame rates, zoom, and relevant incomplete-frame counts.

Provide:

- Mouse-centered wheel zoom and drag-to-pan.
- Fit to Window, 100% native size, and Fullscreen.
- Rotate Left/Right by 90 degrees, arbitrary-angle input, horizontal/vertical flips, and Reset Orientation.
- No interpolation for multiples of 90 degrees.
- An expanded canvas with black padding for arbitrary rotations; do not silently crop image corners.
- Cursor coordinates mapped back to the original acquired image, with original DN intensity. Padding has no valid pixel value.
- Freeze the displayed frame and its histogram independently of acquisition. Contrast controls and snapshots remain available while frozen.
- Preserve the last complete image after acquisition stops.

## 3. Histogram and display contrast

Preserve full-precision raw grayscale data. Apply display mapping in a separate pipeline. Never reduce the source to Mono8 before calculating statistics or applying levels.

The histogram uses original DN values and provides draggable black level B and white level W, with synchronized numeric inputs.

Provide display gamma, inversion, Reset Display, and Full Range. Default gamma is 1.0; its allowed UI range is 0.1 to 5.0.

Define the convention explicitly:

- u = clip((x - B) / (W - B), 0, 1)
- brightness = u ** (1 / gamma)
- Apply inversion after gamma.

Enforce B < W. Handle constant images, empty input, invalid frames, and very narrow dynamic ranges without division errors or flashing.

Provide three distinct modes:

1. Manual: fixed black/white levels.
2. Auto Once: compute levels once from the current image, then hold them.
3. Continuous Auto: update levels as new frames arrive.

Default automatic limits to the 1st and 99th percentiles. Allow advanced adjustment and smooth continuous updates over time, with an initial time constant around 0.5 seconds.

Any manual drag or numeric change switches immediately to Manual. Background automatic processing must not override the user's levels.

Allow histogram-axis zoom for fine adjustment in a narrow DN range. Axis zoom must not change the levels.

The visible image, histogram source, and cursor readout must refer to the same displayed frame. A histogram refresh rate around 10 Hz and spatial sampling are acceptable if needed for responsiveness.

Calculate statistics over acquired pixels only, excluding rotation padding. Viewport zoom and pan do not change the statistics.

If showing clipped-pixel percentages, label them as display clipping rather than sensor saturation.

Do not enable histogram equalization, CLAHE, sharpening, denoising, or false color by default.

## 4. Camera controls

Exposure is a primary control: provide a slider, exact numeric entry, and clear us/ms units. Use a slider mapping suitable for exposure values spanning several orders of magnitude.

Expose gain, Auto Exposure Off/Once/Continuous, auto gain, frame-rate control, and numeric acquisition ROI only when supported. Put less common settings in an advanced section; no ROI drawing tool is required.

Query real feature capabilities through get_range(), get_increment(), get_unit(), is_readable(), is_writeable(), and get_available_entries(). A floating-point increment may be None.

Look up actual features, preferring ExposureTime and considering ExposureTimeAbs only when present. Read the real units and limits rather than guessing from feature names.

Read back the effective value after writes. Refresh dependent limits and access states after changes. Make automatic-exposure state clear and prevent manual edits from fighting automatic control.

Serialize and coalesce hardware-control writes so slider movement does not flood the SDK with blocking calls.

Use Camera.get_pixel_formats(), get_pixel_format(), and set_pixel_format(). Stop acquisition before changing pixel format, then restore the previous acquisition state. Apply the same safe flow to other settings that require stopped acquisition.

Show errors accurately and retain a reliable application state after a failed write.

Display black/white levels and display gamma must not be directly bound to hardware BlackLevel, Gamma, or ContrastControl.

Do not automatically modify camera calibration, NUC, firmware, or temperature-control settings. Do not assume an infrared camera measures temperature.

## 5. SDK lifecycle and image ownership

Maintain one appropriately scoped VmbSystem session per process. Refreshing the device list or starting/stopping preview should not repeatedly recreate the system session.

Perform camera and Feature operations within valid system/camera contexts.

Use Camera.start_streaming(handler, buffer_count=...) for asynchronous acquisition. Do not loop over get_frame() in the GUI thread.

The handler signature is handler(cam, stream, frame). Check frame.get_status(), publish complete frames only, and reliably requeue acquired frames during a valid streaming lifecycle.

Buffer rules:

- Frame.as_numpy_ndarray() returns an SDK buffer view, typically H x W x 1 for monochrome.
- Do not pass that view to asynchronous consumers after queue_frame(frame).
- For unpacked data, obtain an owned copy before requeueing.
- For packed data, safely copy the Frame before requeueing if conversion will happen later in a worker.
- Reusable conversion buffers need explicit ownership and lifetime management.
- Extract supported Chunk metadata while the original frame is valid; converted frames do not retain the original Chunk data.
- Keep callbacks short. Do not perform file I/O or GUI work in SDK callbacks.

## 6. High-bit-depth and packed formats

Handle formats according to the actual camera:

| Format | Processing representation |
| --- | --- |
| Mono8 | uint8 |
| Mono10, Mono12, Mono14, Mono16 | uint16 storage with separately recorded effective bit depth |
| Mono10p | SDK conversion to compatible unpacked Mono10 |
| Mono12p, Mono12Packed | SDK conversion to compatible unpacked Mono12 |

Use convert_pixel_format() and query compatible conversions. Do not guess packed byte layouts or treat Mono12p and Mono12Packed as interchangeable.

Do not normalize raw values to 65535 or equate uint16 storage with 16 effective bits. Preserve and record source pixel format, effective bit depth, and alignment.

The Image Transform manual documents little-endian multi-byte input and least-significant-bit alignment for increasing-bit-depth conversions. Make conversion/alignment handling explicit.

Preserve the current supported monochrome acquisition format by default. Permit a quality/bandwidth choice rather than forcing Mono16.

## 7. TIFF snapshots

Support TIFF only. Save one TIFF per action, using the selected mode; do not automatically generate duplicate files.

Provide a persistent Save Content selector:

- Display Appearance (default): a full-resolution, single-channel 8-bit grayscale TIFF matching the current levels, gamma, inversion, rotation, and flips. Label it as a processed image.
- Original Grayscale: no display enhancement or geometric transformation. Save uint8 for 8-bit input and uint16 for higher-bit-depth input, preserving the original effective DN values.

Save the currently displayed complete frame at the time of the action, including a frozen frame. Do not trigger a new acquisition and present it as the frame the user saw.

Render Display Appearance from the full-resolution image, not from a desktop screenshot. Exclude controls, borders, and viewport-dependent cropping. Zoom and pan must not reduce or crop the saved image.

Arbitrary-angle interpolation is acceptable only for the processed snapshot; original snapshots remain unchanged.

Take an atomic snapshot of the image and display settings before background saving so metadata and pixels cannot refer to different frames or configurations.

Store appropriate TIFF metadata, including:

- Host capture/save timestamps and source camera timestamp where actually available, with their meaning distinguished.
- Camera model and serial number.
- Frame ID and source pixel format.
- Effective bit depth and alignment.
- Save mode and display settings.
- Exposure/gain with provenance: per-frame Chunk metadata when available, otherwise clearly identified cached readback.

Provide directory selection, filename prefix, millisecond timestamps, collision-safe numbering, a Snapshot button, and Ctrl+S.

Use a save worker. Show a lightweight success notification and an option to open the destination folder. Report failures accurately.

## 8. Concurrency and reliability

Separate camera control, image processing, GUI rendering, and snapshot saving.

Use bounded queues or latest-frame slots. Keep only a small number of pending preview frames, for example one or two, instead of accumulating image-bearing Qt events.

A camera faster than the display must not make the preview drift increasingly behind real time. Count discarded stale preview frames separately from incomplete transport frames.

Keep the GUI responsive and distinguish long-exposure waiting from disconnection.

Provide specific messages for missing devices, device ownership conflicts, missing SDK/TL components, unsupported formats, unwritable features, disconnection, and disk errors.

On exit, coordinate consumers and pending saves, stop acquisition, close the camera, and shut down the system session without accessing released resources.

## 9. Windows deployment and runtime diagnostics

Installing the Python wheel alone is insufficient. It includes API DLLs but not transport layers or camera drivers.

Install/configure matching Vimba X components, required drivers, and the Visual C++ runtime on the Windows target. An SDK directory copy is not proof of correct driver installation.

Allow SDK/TL path configuration. Before initialization, VmbSystem.set_path_configuration() can select the actual cti directory. Do not assume the default installation path.

The supplied bin/VmbC.xml contains a default C:\Program Files\Allied Vision\Vimba X\cti path, while the wheel has its own configuration. Diagnose what is actually loaded.

Do not add the SDK's bundled Qt5 libraries/plugins to the PySide6/Qt6 search path.

The development Mac does not change the Windows x64 target. Do not try loading Windows DLLs on macOS.

Startup diagnostics should identify VmbPy/VmbC versions, loaded transport layers, and detected cameras, with actionable errors.

## 10. Settings

Remember window layout, orientation, display levels/gamma, auto-contrast mode, snapshot directory, and save mode.

Use SDK-supported XML persistence for camera presets where appropriate, while storing application display settings separately.

Revalidate camera settings before restoring them. SDK release notes indicate that not every hardware LUT setting is preserved by XML, so XML persistence must not replace application display-state persistence.

## 11. Validation and acceptance

First validate the real SDK acquisition path on Windows using the supplied Camera Simulator TL. Clearly identify simulated devices; simulator feature sets and timing do not prove hardware behavior.

Use synthetic high-bit-depth/narrow-range data to validate display mapping and TIFF preservation. The simulator's custom 8-bit BMP input alone cannot prove a high-bit-depth path.

Acceptance criteria:

1. Exposure writes take effect and the UI displays correct units and readback values.
2. Levels and gamma reveal details in narrow raw DN ranges and update the preview promptly.
3. Manual levels are never overwritten by automatic processing.
4. Outliers, constant images, and invalid frames do not cause flashing, errors, or divide-by-zero failures.
5. Packed and high-bit-depth data retain precision; Original Grayscale TIFF readback equals the correctly unpacked source DN array.
6. Display processing never modifies raw data; Display Appearance TIFF agrees with the preview state captured for saving.
7. SDK buffer reuse cannot tear images, mix frames, or overwrite queued snapshots.
8. Rotation, flipping, padding, and cursor coordinate transforms are correct; viewport pan/zoom cannot accidentally crop snapshots.
9. Repeated start/stop cycles, pixel-format changes, disconnects, and shutdown remain stable without continuously growing memory use.
10. Physical-camera testing is performed separately on the Windows target. Static inspection, synthetic tests, or simulator results are never presented as completed hardware validation.

When implementation is authorized, deliver source code, dependency/version information, Windows setup and launch instructions, a practical packaging approach, and an accurate validation record.
