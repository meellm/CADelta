"""Tests for the optional ``include_removed`` and ``include_moved_ghost`` flags
on :func:`cadelta.writer.write_diff`, plus the contract that mutating
module-level color constants between calls actually changes output colors.

These flags exist so the desktop GUI can suppress REMOVED parts and the
MOVED_FROM ghost overlay per the user's settings without re-implementing the
diff path."""
from __future__ import annotations

from pathlib import Path

from cadelta import writer
from cadelta.matcher import Status, diff_parts
from cadelta.reader import load_parts, load_parts_with_doc
from cadelta.writer import COLOR_BY_STATUS, write_diff

from .conftest import box_at, make_step


def _build_pair(tmp_path: Path):
    """Three boxes: A unchanged, B moved, C removed in v2 (D added).

    Same shape pattern used by the existing ``step_pair`` fixture, but inlined
    so this module doesn't depend on it."""
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40, 0, 0)),
        ("C", box_at(20, 20, 20, 80, 0, 0)),
    ])
    make_step(v2, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40.5, 0, 0)),  # moved
        ("D", box_at(12, 12, 12, 120, 0, 0)),   # added
    ])
    return v1, v2


def test_write_diff_excludes_removed_when_flag_false(tmp_path: Path):
    """``include_removed=False`` drops REMOVED bodies from the output STEP.
    ADDED, MOVED, and the MOVED_FROM ghost must still be present — this flag
    only gates REMOVED."""
    v1, v2 = _build_pair(tmp_path)
    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    out = tmp_path / "diff_no_removed.step"
    write_diff(result, out, doc_v2=doc_v2, include_removed=False)

    parts_diff = load_parts(out)
    names = [p.name for p in parts_diff]
    assert not any("REMOVED" in n for n in names), (
        f"REMOVED parts should be excluded; got names: {names}"
    )
    # The other diff categories must survive.
    assert any("[MOVED]" in n for n in names), "MOVED body lost"
    assert any("MOVED_FROM" in n for n in names), "MOVED_FROM ghost lost"
    assert any("ADDED" in n for n in names), "ADDED body lost"


def test_write_diff_excludes_moved_ghost_when_flag_false(tmp_path: Path):
    """``include_moved_ghost=False`` drops the v1-position ghost overlay.
    The MOVED body at v2's new position must still be emitted — the flag is
    asymmetric, only suppressing the ghost, not the moved entry itself."""
    v1, v2 = _build_pair(tmp_path)
    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    out = tmp_path / "diff_no_ghost.step"
    write_diff(result, out, doc_v2=doc_v2, include_moved_ghost=False)

    parts_diff = load_parts(out)
    names = [p.name for p in parts_diff]
    assert not any("MOVED_FROM" in n for n in names), (
        f"MOVED_FROM ghost should be excluded; got names: {names}"
    )
    # MOVED at the new position must survive.
    assert any("[MOVED]" in n for n in names), "MOVED body lost"
    # REMOVED and ADDED unaffected.
    assert any("REMOVED" in n for n in names)
    assert any("ADDED" in n for n in names)


def test_write_diff_excludes_both_when_both_false(tmp_path: Path):
    """Both flags off simultaneously: only ADDED, MOVED, and UNCHANGED bodies
    are present in the output."""
    v1, v2 = _build_pair(tmp_path)
    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    out = tmp_path / "diff_neither.step"
    write_diff(result, out, doc_v2=doc_v2,
               include_removed=False, include_moved_ghost=False)

    parts_diff = load_parts(out)
    names = [p.name for p in parts_diff]
    assert not any("REMOVED" in n for n in names)
    assert not any("MOVED_FROM" in n for n in names)
    assert any("[MOVED]" in n for n in names)
    assert any("ADDED" in n for n in names)


def test_write_diff_defaults_unchanged(tmp_path: Path):
    """Backward-compat guard: omitting the new kwargs keeps the legacy
    behavior — REMOVED bodies and MOVED_FROM ghosts both present."""
    v1, v2 = _build_pair(tmp_path)
    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    out = tmp_path / "diff_default.step"
    write_diff(result, out, doc_v2=doc_v2)  # no kwargs

    parts_diff = load_parts(out)
    names = [p.name for p in parts_diff]
    assert any("REMOVED" in n for n in names)
    assert any("MOVED_FROM" in n for n in names)
    assert any("[MOVED]" in n for n in names)
    assert any("ADDED" in n for n in names)


def test_color_constants_mutation_propagates(tmp_path: Path):
    """The GUI customizes diff colors by mutating ``writer.COLOR_BY_STATUS``
    between calls — this test pins down the contract that those mutations
    actually reach the writer's emit path. If a future refactor captured the
    colors at import time (e.g. into a closure or default arg), this would
    fail loudly.

    Uses an obvious sentinel green far from the default cyan so a tolerant
    color assertion can't false-positive."""
    v1, v2 = _build_pair(tmp_path)
    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    sentinel = (0.10, 0.90, 0.20)  # bright green — distinct from any default
    original_added = COLOR_BY_STATUS[Status.ADDED]
    try:
        writer.COLOR_BY_STATUS[Status.ADDED] = sentinel
        out = tmp_path / "diff_sentinel.step"
        write_diff(result, out, doc_v2=doc_v2)
    finally:
        # Restore the dict no matter what — this module's other tests share
        # the same singleton.
        writer.COLOR_BY_STATUS[Status.ADDED] = original_added

    parts_diff = load_parts(out)
    added = next(p for p in parts_diff if "ADDED" in p.name)
    assert added.color is not None, "ADDED body lost its color attribute"
    for got, want in zip(added.color, sentinel):
        assert abs(got - want) < 0.02, (
            f"ADDED color should reflect mutated COLOR_BY_STATUS={sentinel}; "
            f"got {added.color}. The writer is reading colors from somewhere "
            "other than the module-level dict — GUI color customization will "
            "silently fail."
        )
