# Contributing to CADelta

Thanks for taking the time to contribute! This guide covers setting up
CADelta locally, running the tests, and getting a pull request landed.

## Code of Conduct

Be kind. Treat other contributors the way you'd want to be treated.
Disagreements are fine; personal attacks aren't.

## Quick start

```bash
git clone https://github.com/meellm/CADelta.git
cd CADelta

# Create a venv and install the package plus dev deps
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run the CLI
cadelta --help

# Run the desktop app
cadelta-gui                         # or: python -m cadelta.gui.app
```

OCCT (OpenCascade) bindings come from `cadquery-ocp`, which is pure pip -
no conda required.

## Project layout

```
src/cadelta/
|-- cli.py              # Click CLI entry point (cadelta)
|-- reader.py           # STEP -> list of Part(name, shape, transform, ...)
|-- signature.py        # Pose-invariant geometry fingerprint
|-- matcher.py          # Signature + nearest-centroid pairing; classifies pairs
|-- writer.py           # Builds the colored output STEP and optional GLB
`-- gui/                # PySide6 desktop app (cadelta-gui)
    |-- app.py          # GUI entry point (cadelta.gui.app:main)
    |-- main_view.py    # Compare page
    |-- settings_view.py# Settings page (colors, toggles, tolerances)
    |-- settings.py     # JSON settings persistence (~/.cadelta/settings.json)
    |-- worker.py       # Diff runner (pure, GUI-agnostic)
    |-- qt_worker.py    # Qt threading wrapper around worker
    |-- theme.py        # Qt styling / palette
    `-- excel_report.py # Optional Excel report writer

tests/                  # Unit + end-to-end tests
build/                  # build_exe.py + cadelta_gui.spec (PyInstaller)
.github/workflows/      # CI + release automation
```

## Running the tests

```bash
# Full suite
pytest

# GUI tests need a Qt platform plugin. On a headless machine, run them
# against the offscreen backend:
QT_QPA_PLATFORM=offscreen pytest

# Stress benchmark (synthetic assemblies, not part of the suite)
python tests/bench.py 1000
```

All checked-in tests should pass before you push. CI runs the suite on
Linux, macOS, and Windows across Python 3.11 and 3.13.

## Building the desktop executable

The standalone `CADelta.exe` is produced by PyInstaller:

```bash
python build/build_exe.py            # wraps build/cadelta_gui.spec
```

PyInstaller cannot cross-compile, so the Windows `.exe` must be built on
Windows (your own box or a `windows-latest` CI runner). The spec file
behaves identically on all platforms; only the artifact name differs.
Release builds are produced automatically when a version tag is pushed -
see `.github/workflows/release.yml`.

## Commit style

Conventional Commits - `fix:`, `feat:`, `refactor:`, `perf:`, `docs:`,
`chore:`. Scope optional, body explains *why*, not *what*. Keep the first
line under 70 characters.

Examples:
```
fix(reader): split batched-component compounds into individual parts
feat(writer): output now shows moved_from ghost position
perf(writer): mutate v2 doc in place to preserve master/instance sharing
```

## Pull request checklist

- [ ] Tests pass locally (`pytest`)
- [ ] New behaviour has tests
- [ ] No `print()` debug noise left in production code
- [ ] Commit messages follow the style above
- [ ] No secrets or personal config in the diff

## Reporting issues

See [SECURITY.md](SECURITY.md) for vulnerability reports. For everything
else, open a GitHub issue with the **Bug report** or **Feature request**
template.
