"""Snapshot of the writer's default colors at GUI-package import time.

Why we capture them: the GUI lets the user "untick" a status to fall back to
the default color. The writer's ``COLOR_BY_STATUS`` is a mutable module dict —
if we read it after the user already mutated it once for an earlier diff,
"the default" would be whatever they last picked. Snapshotting once at import
gives a stable reference of the original, ship-time colors.
"""
from __future__ import annotations

from cadelta.matcher import Status
from cadelta.writer import COLOR_BY_STATUS, COLOR_MOVED_FROM


# Defensive copies so mutations to the writer dict can't bleed back into our
# defaults via shared references.
DEFAULT_COLORS: dict[str, tuple[float, float, float]] = {
    "moved": tuple(COLOR_BY_STATUS[Status.MOVED]),
    "added": tuple(COLOR_BY_STATUS[Status.ADDED]),
    "removed": tuple(COLOR_BY_STATUS[Status.REMOVED]),
    "moved_from": tuple(COLOR_MOVED_FROM),
}
