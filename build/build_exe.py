"""Build the CADelta desktop GUI into a single executable.

Usage (from the repo root):

    python build/build_exe.py

Outputs:
    dist/CADelta.exe       (on Windows; what the user actually distributes)
    dist/CADelta           (on macOS/Linux; for local testing only)

PyInstaller cannot cross-compile, so the Windows .exe must be built on a
Windows machine — either the user's own box or a CI runner with
``windows-latest``. The spec file works identically on all three platforms;
only the resulting artifact's name differs.

This script intentionally stays trivial: all configuration lives in
``build/cadelta_gui.spec`` so `pyinstaller --clean build/cadelta_gui.spec`
behaves identically whether invoked through this script or directly.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys


HERE = pathlib.Path(__file__).resolve().parent
SPEC = HERE / "cadelta_gui.spec"
REPO_ROOT = HERE.parent


def main() -> int:
    if not SPEC.exists():
        print(f"error: spec file not found at {SPEC}", file=sys.stderr)
        return 2

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",        # wipe stale build/ directory before each build
        "--noconfirm",    # overwrite dist/ without prompting
        str(SPEC),
    ]
    print("Running:", " ".join(cmd))
    # Run from the repo root so relative paths in the spec resolve cleanly.
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        print(f"error: PyInstaller exited with status {result.returncode}",
              file=sys.stderr)
        return result.returncode

    # Friendly hint on success — PyInstaller's own output is verbose enough
    # that the final artifact path is easy to miss.
    dist = REPO_ROOT / "dist"
    if (dist / "CADelta.exe").exists():
        print("\nBuilt:", dist / "CADelta.exe")
    elif (dist / "CADelta").exists():
        print("\nBuilt:", dist / "CADelta")
    else:
        print("\nBuild completed but CADelta artifact not found in dist/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
