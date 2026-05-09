# PyInstaller spec for the CADelta desktop GUI.
#
# Build with `python build/build_exe.py` from the repo root, OR directly:
#     pyinstaller --clean --noconfirm build/cadelta_gui.spec
#
# Why this is a spec file rather than a long command line:
# - cadquery-ocp's OCP module loads its submodules lazily (`__getattr__`),
#   so PyInstaller's static analysis misses everything under OCP.* unless
#   we explicitly enumerate them via collect_submodules.
# - The OCCT C++ shared libraries that ship with cadquery-ocp need to be
#   bundled as binaries; collect_dynamic_libs picks them up.
# - tkinterdnd2 ships Tcl scripts + a platform-specific tkdnd shared lib
#   that PyInstaller's analyzer also misses; collect_data_files brings them.
#
# Output: dist/CADelta.exe on Windows (~180-230 MB, dominated by OCCT).
# `--onefile` makes it a single self-extracting binary; first launch unpacks
# OCCT into a temp dir which takes ~3-8 seconds.

import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


# Resolve repo root regardless of where pyinstaller is invoked from.
HERE = os.path.dirname(os.path.abspath(SPEC))      # type: ignore[name-defined]
REPO_ROOT = os.path.normpath(os.path.join(HERE, ".."))


# --- OCP / cadquery-ocp -------------------------------------------------------
# Three calls because PyInstaller divides bundling into "Python imports",
# "native shared libs", and "data files / Tcl scripts / etc." We need all three.
ocp_hidden = collect_submodules("OCP")
ocp_binaries = collect_dynamic_libs("OCP")
ocp_data = collect_data_files("OCP")

# --- tkinterdnd2 --------------------------------------------------------------
dnd_data = collect_data_files("tkinterdnd2")
dnd_binaries = collect_dynamic_libs("tkinterdnd2")

# --- App package --------------------------------------------------------------
# Explicit hiddenimports for our own modules — defensive; PyInstaller usually
# finds these via the entry-point script's import graph but listing them costs
# nothing and survives import-style refactors.
app_hidden = [
    "cadelta",
    "cadelta.cli",
    "cadelta.matcher",
    "cadelta.reader",
    "cadelta.signature",
    "cadelta.writer",
    "cadelta.gui.app",
    "cadelta.gui.defaults",
    "cadelta.gui.excel_report",
    "cadelta.gui.main_view",
    "cadelta.gui.settings",
    "cadelta.gui.settings_view",
    "cadelta.gui.worker",
]


a = Analysis(
    [os.path.join(REPO_ROOT, "src", "cadelta", "gui", "app.py")],
    pathex=[os.path.join(REPO_ROOT, "src")],
    binaries=ocp_binaries + dnd_binaries,
    datas=ocp_data + dnd_data,
    hiddenimports=ocp_hidden + app_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim weight: these get imported indirectly by some scientific libs but
    # CADelta never touches them. Excluding shaves a noticeable amount off
    # the final binary on Windows.
    excludes=[
        "matplotlib",
        "PIL",
        "pytest",
        "IPython",
        "tornado",
        "jupyter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CADelta",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts OCCT DLLs on Windows — symptom is a silent failure to
    # import OCP at runtime. Leave compression off; size cost is trivial
    # next to the OCCT footprint.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # GUI app — no console window on Windows. (On macOS this flag is
    # ignored; .app bundles handle their own window setup.)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # `--onefile` equivalent: bundle everything into a single self-
    # extracting binary the user double-clicks. Trades cold-start latency
    # (a few seconds) for distribution simplicity (one file, no installer).
    onefile=True,
)
