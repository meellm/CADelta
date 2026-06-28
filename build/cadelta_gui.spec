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
# - PySide6 is large; we let PyInstaller's bundled Qt hooks pull in the Qt
#   plugins/DLLs and only hint the handful of QtCore/QtGui/QtWidgets modules
#   the app touches, so the analyzer doesn't drag in the whole Qt surface.
#
# Output: dist/CADelta.exe on Windows (~250-320 MB, OCCT + Qt).
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

# --- PySide6 / Qt -------------------------------------------------------------
# PyInstaller ships first-class PySide6 hooks that bundle the Qt runtime, so we
# only enumerate the modules the app imports as hidden imports. Listing them
# keeps the analysis robust to PySide6's lazy submodule loading without pulling
# in WebEngine/Multimedia/etc. that CADelta never touches.
qt_hidden = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]

# --- App package --------------------------------------------------------------
# Explicit hiddenimports for the app's own modules. PyInstaller usually finds
# these via the entry-point import graph, but listing them survives
# import-style refactors.
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
    "cadelta.gui.qt_worker",
    "cadelta.gui.settings",
    "cadelta.gui.settings_view",
    "cadelta.gui.theme",
    "cadelta.gui.worker",
]


a = Analysis(
    [os.path.join(REPO_ROOT, "src", "cadelta", "gui", "app.py")],
    pathex=[os.path.join(REPO_ROOT, "src")],
    binaries=ocp_binaries,
    datas=ocp_data,
    hiddenimports=ocp_hidden + qt_hidden + app_hidden,
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
        # Keep the Qt footprint to PySide6 only: exclude the other bindings
        # and the unused Tk runtime so they can't get pulled in by accident.
        "PyQt5",
        "PyQt6",
        "PySide2",
        "tkinter",
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
    # UPX corrupts OCCT DLLs on Windows: the symptom is a silent failure to
    # import OCP at runtime. Leave compression off; the size cost is trivial
    # next to the OCCT footprint.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # GUI app: no console window on Windows. (On macOS this flag is
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
