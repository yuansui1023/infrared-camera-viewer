# Contributor Instructions

## Language

Use English for all repository content: filenames, documentation, code comments, commit messages, UI labels, tooltips, logs, and errors. Do not add Chinese text.

## Current scope

Application implementation has been explicitly authorized. Implement the English-only Windows viewer described in the development specification.

Use docs/development-prompt.md as the implementation specification and docs/sdk-assessment.md as the record of inspected SDK facts. A new user instruction takes precedence over these documents.

## Technical constraints

- Target Windows x64 and use the matching bundled VmbPy 1.2.3 API.
- Keep vendor SDK files, DLLs, drivers, wheels, installers, credentials, machine-specific settings, and camera captures outside version control.
- Preserve raw image precision and maintain a separate display pipeline.
- Never retain a live SDK buffer view after requeueing its frame.
- Resolve camera capabilities dynamically; do not infer physical hardware from simulator presets.
- Keep SDK callbacks short and keep blocking work off the GUI thread.
- Distinguish static checks, synthetic-data checks, Windows simulator checks, and real-camera validation. Report only checks actually performed.
- Do not run Windows DLLs on macOS.
- Do not modify camera calibration, NUC files, firmware, or temperature-control settings as part of the first release.
