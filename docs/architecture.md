# Architecture

## Acquisition and ownership

`CameraController` owns the backend on a dedicated Qt thread. VmbPy is imported lazily for a real Windows camera. A system session remains open across discovery and reconnects until the source changes or the controller shuts down.

The SDK callback rejects incomplete frames, obtains available chunks, converts packed monochrome formats through the SDK, and copies the ndarray before requeueing the SDK frame in a `finally` block. Application frames own their memory. Immutable `FrameData` records effective source precision separately from storage dtype.

A single-frame mailbox replaces stale frames. Feature writes are coalesced and commands have a bounded queue. The GUI receives small state signals rather than an unbounded image-event stream. Disconnect callbacks set state for handling outside the SDK callback.

## Display processing

`ImageProcessor` consumes owned frames on a worker thread. Histogram and percentile sampling are bounded. A lookup table maps source DN values directly to 8-bit display data, preserving useful contrast in narrow high-bit-depth ranges. Gamma follows `output = normalized ** (1 / gamma)` before optional inversion.

Each `RenderPacket` contains its exact frame, options, histogram, and configuration revision. The GUI rejects stale revisions. Auto Once becomes fixed manual levels; Continuous Auto smooths percentile estimates. Dragging histogram handles switches to manual mode.

Freeze pins the exact displayed source frame while acquisition continues. Display controls reprocess that frame. Rotation and flips are view transforms; raw data remain unchanged. Cursor coordinates map back to original pixels.

## Snapshots and shutdown

Snapshot requests capture the current packet and orientation. Original mode writes unchanged uint8/uint16 data. Display mode renders full-resolution 8-bit grayscale and applies orientation. Right angles preserve pixel values; arbitrary angles use interpolation and an expanded canvas.

The snapshot worker has one writer and a bounded queue. Exclusive filename reservation prevents overwrites. A temporary TIFF is atomically replaced after a successful write. Failures clean up reserved and temporary files. Metadata distinguishes per-frame chunks from cached readbacks.

Window closure asynchronously stops acquisition and processing, waits for pending snapshots, saves preferences, and exits. The Qt event loop remains active for worker completion.

## Module map

| Module | Responsibility |
| --- | --- |
| backends.py | VmbPy integration and synthetic source |
| controller.py | Camera thread, commands, and state readback |
| models.py | Validated records and frame mailbox |
| imaging.py | Histogram, contrast mapping, processor worker |
| geometry.py | Coordinate transforms and saved orientation |
| widgets.py, window.py | Preview, histogram, application controls |
| snapshots.py | Bounded asynchronous TIFF saves |
| settings.py | Preferences and portable display presets |
| app.py | Startup, logging, command-line arguments |
