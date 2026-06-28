# Changelog

All notable changes are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-06-28

First public release.

### Added
- STEP visual diff engine that classifies leaf parts as added, removed,
  moved, or unchanged.
- Geometry-signature matching by volume, surface area, sorted bounding-box
  dimensions, and face count.
- World-space centroid and orientation checks for moved-part detection.
- Colored STEP output that preserves v2's XCAF document structure and original
  colors for unchanged parts.
- Color scheme for added, moved, moved-from ghost, removed, and unchanged
  statuses.
- `cadelta` CLI with STEP output, optional GLB export, optional JSON report,
  and configurable movement tolerances.
- PySide6 desktop app (`cadelta-gui`) with drag-and-drop file selection,
  background diff execution, persisted settings, configurable status colors,
  optional JSON/Excel reports, and tolerance controls.
- Optional Excel report writer powered by openpyxl.
- PyInstaller build path for standalone Windows `CADelta.exe`.
- GitHub Actions CI across Linux, macOS, and Windows on Python 3.11 and 3.13.
- Windows release workflow that tests, builds, uploads, and publishes
  `CADelta.exe` for version tags.
- Public repository files: issue templates, PR template, contributing guide,
  security policy, changelog, and gitignore.
