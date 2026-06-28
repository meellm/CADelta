# CADelta

**Visual diff for STEP CAD files.**

Compare two versions of a mechanical assembly and get one colored output that
shows what was added, removed, moved, or left unchanged. CADelta ships both a
scriptable CLI and a PySide6 desktop app for point-and-click comparisons.

<p align="center">
  <a href="https://github.com/meellm/CADelta/releases"><img alt="latest release" src="https://img.shields.io/github/v/release/meellm/CADelta"></a>
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <img alt="platforms" src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-success">
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue">
</p>

> **Want the easiest install?** Download `CADelta.exe` from the
> [latest release](https://github.com/meellm/CADelta/releases/latest).
> The prebuilt desktop release is Windows-only. The Python package runs on
> Windows, macOS, and Linux.

---

## Highlights

- Visual STEP diffs with a color-coded output model
- Preserves v2's XCAF document structure instead of rebuilding from scratch
- Geometry-signature matching, so STEP entity renumbering and part renames do
  not create noisy false positives
- Centroid and orientation checks for moved-part detection
- CLI output options: colored STEP, optional GLB, optional JSON report
- PySide6 desktop app with drag-and-drop file selection and saved settings
- Optional Excel report from the desktop app
- No telemetry, no accounts, no cloud service

---

## Install

```bash
git clone https://github.com/meellm/CADelta.git
cd CADelta
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

On Windows:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

OCCT (OpenCascade) bindings come from `cadquery-ocp`, so no conda install is
required.

---

## First five minutes

```bash
# Compare two STEP files and write a colored STEP diff
cadelta v1.step v2.step -o diff.step

# Also export a GLB for browser-based inspection
cadelta v1.step v2.step -o diff.step --gltf diff.glb

# Also write a JSON report
cadelta v1.step v2.step -o diff.step --report diff.json
```

Open `diff.step` in your CAD application, or drag `diff.glb` into a glTF
viewer such as <https://gltf-viewer.donmccurdy.com>. Local files stay local;
nothing is uploaded.

For the desktop app:

```bash
cadelta-gui
# or
python -m cadelta.gui.app
```

Drop or browse to `v1.step` and `v2.step`, click **Compare**, then choose where
to save `diff.step`. The diff runs on a background thread so the window stays
responsive. The **Settings** page lets you adjust colors, output toggles, and
tolerances; settings are saved to `~/.cadelta/settings.json`.

---

## What the colors mean

| Color | Status | Meaning |
|---|---|---|
| Cyan | added | A part exists in v2 but not in v1 |
| Yellow | moved | A matching part moved; shown at the new v2 position |
| Hot pink ghost | moved-from | The old v1 position for a moved part |
| Bright red | removed | A part exists in v1 but not in v2 |
| Original color | unchanged | Matching geometry and same pose |

---

## Commands

| Command | Purpose |
|---|---|
| `cadelta V1 V2 -o OUT.step` | Compare V1 against V2 and write a colored STEP file |
| `cadelta V1 V2 -o OUT.step --gltf OUT.glb` | Also export a binary glTF/GLB file |
| `cadelta V1 V2 -o OUT.step --report OUT.json` | Also emit a JSON report |
| `cadelta V1 V2 -o OUT.step --tol-mm 0.05 --tol-deg 0.1` | Loosen movement tolerance |
| `cadelta --help` | Show all CLI options |
| `cadelta-gui` | Launch the PySide6 desktop app |

---

## How matching works

For each leaf part in v1 and v2, CADelta builds:

- a geometry signature: `(volume, surface area, sorted bbox dims, face count)`,
  computed on the master shape so it is pose-invariant
- a world-space centroid, so movement is detected whether position lives in an
  XCAF transform or in the geometry itself
- a world-space orientation from the principal axes of inertia

Parts with identical geometry signatures are grouped, then paired one-to-one by
closest centroid. Names are intentionally ignored because STEP exports often
renumber entities when an assembly changes. After pairing, CADelta compares
centroid and orientation deltas against the configured tolerances. Unmatched v2
parts are **added**; unmatched v1 parts are **removed**.

---

## Performance

Measured locally on synthetic flat assemblies:

| Parts | Read v1+v2 | Diff | Write STEP | Total | Peak RSS |
|---:|---:|---:|---:|---:|---:|
| 200 | 0.6 s | 0.03 s | 0.1 s | 1.5 s | 355 MB |
| 1,000 | 2.9 s | 0.9 s | 0.6 s | 5.6 s | 712 MB |
| 3,000 | 9.6 s | 8.8 s | 1.9 s | 25 s | 2.4 GB |

The benchmark is intentionally harsh: every part shares the same geometry
signature, so the candidate-pair sort does more work than a typical assembly.
Real assemblies usually have varied geometry, which keeps matching buckets
small.

---

## Tests

```bash
pytest

# Headless GUI test run
QT_QPA_PLATFORM=offscreen pytest

# Synthetic assembly benchmark
python tests/bench.py 1000
```

The current suite covers the matcher, STEP round trips, writer flags, JSON/Excel
reporting, GUI settings, the Qt worker bridge, and headless app construction.

---

## Building the Windows app

The release executable is built with PyInstaller:

```bash
python build/build_exe.py
```

The spec lives at [`build/cadelta_gui.spec`](build/cadelta_gui.spec). PyInstaller
does not cross-compile, so the Windows `.exe` must be built on Windows. The
GitHub release workflow runs on `windows-latest`, tests the project, builds
`dist/CADelta.exe`, uploads it as a workflow artifact, and attaches it to tagged
releases.

---

## Project layout

| Layer | Module | Purpose |
|---|---|---|
| CLI | [`src/cadelta/cli.py`](src/cadelta/cli.py) | Click entry point |
| Reader | [`src/cadelta/reader.py`](src/cadelta/reader.py) | STEP loading and part extraction |
| Signature | [`src/cadelta/signature.py`](src/cadelta/signature.py) | Pose-invariant geometry fingerprint |
| Matcher | [`src/cadelta/matcher.py`](src/cadelta/matcher.py) | Pairing and status classification |
| Writer | [`src/cadelta/writer.py`](src/cadelta/writer.py) | Colored STEP/GLB output |
| GUI | [`src/cadelta/gui/`](src/cadelta/gui/) | PySide6 desktop app |

---

## Known limitations

- Multiple identical parts pair greedily by nearest centroid. Ambiguous swaps
  may still show as moved.
- Pure rotations of a part whose pose is baked into geometry are not always
  detectable. Translations are.
- Names containing `/` can collide with the path separator used for assembly
  hierarchy labels.
- Sheet bodies and other zero-volume shapes have undefined centroids; results
  may be unreliable for those parts.

---

## FAQ

**Does CADelta upload my CAD files?**
No. CADelta runs locally and does not contact a remote service.

**Why is the release executable Windows-only?**
The current release workflow targets Windows because that is the supported
prebuilt desktop artifact. The Python package still runs on macOS and Linux.

**Why is the executable large?**
It bundles Qt and OCCT, both of which are substantial native runtimes. The size
is expected for a single-file CAD desktop tool.

**Can I use only the CLI?**
Yes. `cadelta` is independent from the desktop workflow. The GUI is an extra
entry point over the same diff engine.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, build
notes, and the PR checklist. There are issue templates for
[bugs](.github/ISSUE_TEMPLATE/bug_report.md) and
[features](.github/ISSUE_TEMPLATE/feature_request.md).

For security-sensitive reports, see [SECURITY.md](SECURITY.md).

## License

MIT - see [LICENSE](LICENSE).
