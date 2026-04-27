"""Pure-logic tests for the matcher. No OCCT dependency."""
from __future__ import annotations

import numpy as np
import pytest

from cadelta.matcher import Status, diff_parts
from cadelta.reader import Part
from cadelta.signature import Signature


def _identity() -> np.ndarray:
    return np.eye(4)


def _translation(dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = [dx, dy, dz]
    return m


def _rotation_z(deg: float) -> np.ndarray:
    rad = np.radians(deg)
    c, s = np.cos(rad), np.sin(rad)
    m = np.eye(4)
    m[:3, :3] = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    return m


def _make_part(name: str, transform: np.ndarray, sig: Signature | None = None) -> Part:
    return Part(
        name=name,
        shape=None,
        transform=transform,
        signature=sig or Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 6),
    )


def test_identical_assemblies_all_unchanged():
    sig = Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 6)
    v1 = [_make_part("A", _identity(), sig), _make_part("B", _translation(10), sig)]
    v2 = [_make_part("A", _identity(), sig), _make_part("B", _translation(10), sig)]

    result = diff_parts(v1, v2)
    statuses = [e.status for e in result.entries]
    assert statuses.count(Status.UNCHANGED) == 2
    assert all(s == Status.UNCHANGED for s in statuses)


def test_added_part_is_green_status():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _identity()), _make_part("B", _translation(10))]

    result = diff_parts(v1, v2)
    added = result.by_status(Status.ADDED)
    assert len(added) == 1
    assert added[0].part_v2.name == "B"


def test_removed_part_is_red_status():
    v1 = [_make_part("A", _identity()), _make_part("Gone", _translation(20))]
    v2 = [_make_part("A", _identity())]

    result = diff_parts(v1, v2)
    removed = result.by_status(Status.REMOVED)
    assert len(removed) == 1
    assert removed[0].part_v1.name == "Gone"


def test_translation_above_tolerance_is_moved():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _translation(0.5))]  # 0.5mm > 0.01mm default

    result = diff_parts(v1, v2)
    moved = result.by_status(Status.MOVED)
    assert len(moved) == 1
    assert moved[0].delta_mm == pytest.approx(0.5)
    assert moved[0].delta_deg == pytest.approx(0.0, abs=1e-9)


def test_translation_below_tolerance_is_unchanged():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _translation(0.005))]  # below 0.01mm tolerance

    result = diff_parts(v1, v2)
    assert result.entries[0].status == Status.UNCHANGED


def test_rotation_above_tolerance_is_moved():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _rotation_z(0.5))]  # 0.5° > 0.01° default

    result = diff_parts(v1, v2)
    assert result.entries[0].status == Status.MOVED
    assert result.entries[0].delta_deg == pytest.approx(0.5, abs=1e-6)


def test_rotation_below_tolerance_is_unchanged():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _rotation_z(0.005))]  # below 0.01° tolerance

    result = diff_parts(v1, v2)
    assert result.entries[0].status == Status.UNCHANGED


def test_signature_fallback_pairs_renamed_part():
    """A part renamed between versions should pair via geometry signature, not show as add+remove."""
    unique_sig = Signature.from_values(7.5, 11.0, 1.0, 2.0, 3.0, 6)
    common_sig = Signature.from_values(1.0, 2.0, 3.0, 4.0, 5.0, 6)

    v1 = [
        _make_part("Bracket", _translation(5), unique_sig),
        _make_part("Plate", _identity(), common_sig),
    ]
    v2 = [
        _make_part("Bracket_v2", _translation(5), unique_sig),  # renamed, same pose
        _make_part("Plate", _identity(), common_sig),
    ]

    result = diff_parts(v1, v2)
    assert len(result.by_status(Status.ADDED)) == 0
    assert len(result.by_status(Status.REMOVED)) == 0
    assert len(result.by_status(Status.UNCHANGED)) == 2


def test_custom_tolerances():
    v1 = [_make_part("A", _identity())]
    v2 = [_make_part("A", _translation(0.5))]

    # With looser tolerance, the same change should be unchanged.
    result = diff_parts(v1, v2, tol_mm=1.0, tol_deg=1.0)
    assert result.entries[0].status == Status.UNCHANGED


def test_multiple_parts_same_name_pair_greedily():
    """If a name appears multiple times, parts pair up greedily and any leftover is added/removed."""
    v1 = [_make_part("Bolt", _identity()), _make_part("Bolt", _translation(10))]
    v2 = [
        _make_part("Bolt", _identity()),
        _make_part("Bolt", _translation(10)),
        _make_part("Bolt", _translation(20)),
    ]

    result = diff_parts(v1, v2)
    assert len(result.by_status(Status.UNCHANGED)) == 2
    # The third Bolt has no signature-distinct pair in v1, falls through.
    # Note: the first two Bolts in v1 already paired by name, leaving v2's third Bolt
    # to try signature-match against any leftover v1 anon parts. v1 leftover is empty,
    # so the third Bolt is ADDED.
    assert len(result.by_status(Status.ADDED)) == 1
