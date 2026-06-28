"""Desktop GUI for CADelta.

Thin PySide6 (Qt) wrapper around the engine in :mod:`cadelta.reader`,
:mod:`cadelta.matcher`, and :mod:`cadelta.writer`. The CLI
(:mod:`cadelta.cli`) and the GUI share the same engine; none of this
package's modules duplicate diff or write logic.

Entry point: :func:`cadelta.gui.app.main` (also exposed via the
``cadelta-gui`` console script in ``pyproject.toml``)."""
