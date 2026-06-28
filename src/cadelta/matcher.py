from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

from .reader import Part

DEFAULT_TOL_MM = 0.01
DEFAULT_TOL_DEG = 0.01


class Status(str, Enum):
    UNCHANGED = "unchanged"
    MOVED = "moved"
    ADDED = "added"
    REMOVED = "removed"


@dataclass
class DiffEntry:
    status: Status
    part_v1: Optional[Part]
    part_v2: Optional[Part]
    delta_mm: float = 0.0
    delta_deg: float = 0.0


@dataclass
class DiffResult:
    entries: list[DiffEntry]

    def by_status(self, status: Status) -> list[DiffEntry]:
        return [e for e in self.entries if e.status == status]


def _translation_delta(p1: Part, p2: Part) -> float:
    """Distance between the parts' world-space centers of mass."""
    return float(np.linalg.norm(p2.centroid - p1.centroid))


def _rotation_delta_deg(p1: Part, p2: Part) -> float:
    """World-space rotation delta in degrees, robust to representation changes.

    Uses each part's `orientation` field (principal axes of inertia of the located
    shape), which is intrinsic to the geometry and therefore invariant to whether
    the rotation lives in an XCAF transform or in baked coordinates.

    Returns 0.0 when:
      - either part is axisymmetric (orientation is rotationally ambiguous), or
      - either part has no orientation set (synthetic parts that bypass the reader),
    falling back to the XCAF-transform-based comparison only when both parts come
    without inertia data.
    """
    if p1.axisymmetric or p2.axisymmetric:
        return 0.0
    if p1.orientation is None or p2.orientation is None:
        # Fallback: compare XCAF transforms. Used for synthetic/test parts that
        # weren't loaded through the reader.
        r1 = p1.transform[:3, :3]
        r2 = p2.transform[:3, :3]
        rel = r1.T @ r2
        cos_theta = (np.trace(rel) - 1.0) / 2.0
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        return float(np.degrees(np.arccos(cos_theta)))

    # Inertia eigenvectors carry sign ambiguity; try the four sign flips that
    # preserve right-handedness and take the smallest delta.
    R1 = p1.orientation
    R2 = p2.orientation
    best = 180.0
    for s in ((1, 1, 1), (-1, -1, 1), (-1, 1, -1), (1, -1, -1)):
        R2s = R2 * np.array(s)
        rel = R1.T @ R2s
        cos_theta = (np.trace(rel) - 1.0) / 2.0
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        best = min(best, float(np.degrees(np.arccos(cos_theta))))
    return best


def _classify_pair(p1: Part, p2: Part, tol_mm: float, tol_deg: float) -> DiffEntry:
    dmm = _translation_delta(p1, p2)
    ddeg = _rotation_delta_deg(p1, p2)
    status = Status.UNCHANGED if (dmm <= tol_mm and ddeg <= tol_deg) else Status.MOVED
    return DiffEntry(status=status, part_v1=p1, part_v2=p2, delta_mm=dmm, delta_deg=ddeg)


def _spatial_signature_pairing(
    v1: list[Part], v2: list[Part]
) -> tuple[list[tuple[Part, Part]], list[Part], list[Part]]:
    """Pair parts of identical geometry signature, preferring the closest world-space
    centroid match. One-to-one constrained: each part participates in at most one pair.

    Why this replaces the old name-then-signature pairing scheme:

    - STEP files exported from CAD tools typically tag parts with auto-generated
      identifiers (e.g. NAUO123, label tags, or path-style names like
      "Asm_v1/NAUO1/NAUO123"). When a few parts are removed in v2 these indices
      *renumber*, so v1's NAUO123 is paired with a different physical part of
      v2's NAUO123 by the old name pass — yielding many spurious MOVED entries
      with double-digit-mm deltas.
    - Pairing strictly by signature with greedy nearest-centroid assignment recovers
      the natural "this v1 part corresponds to that v2 part" mapping in the common
      case (most parts didn't move). Identical parts at identical positions pair at
      distance 0 → UNCHANGED. Removed parts have no signature match → REMOVED.
    - The greedy scheme (sort all candidate pairs by distance, accept smallest first
      that doesn't conflict with an earlier accept) is O(k²) per signature bucket but
      buckets are small in practice — for assemblies with up to a few thousand parts
      this is dominated by I/O rather than matching.
    """
    buckets_v1: dict = defaultdict(list)
    buckets_v2: dict = defaultdict(list)
    for p in v1:
        buckets_v1[p.signature].append(p)
    for p in v2:
        buckets_v2[p.signature].append(p)

    pairs: list[tuple[Part, Part]] = []
    leftover_v1: list[Part] = []
    leftover_v2: list[Part] = []

    for sig in set(buckets_v1) | set(buckets_v2):
        a = buckets_v1.get(sig, [])
        b = buckets_v2.get(sig, [])
        if not b:
            leftover_v1.extend(a)
            continue
        if not a:
            leftover_v2.extend(b)
            continue

        # Build all candidate pairs (i, j) with their centroid distance, then greedily
        # accept smallest-first while skipping any that conflict with an earlier accept.
        candidates: list[tuple[float, int, int]] = []
        for i, ai in enumerate(a):
            for j, bj in enumerate(b):
                d = float(np.linalg.norm(ai.centroid - bj.centroid))
                candidates.append((d, i, j))
        candidates.sort(key=lambda t: t[0])

        used_a: set[int] = set()
        used_b: set[int] = set()
        for _d, i, j in candidates:
            if i in used_a or j in used_b:
                continue
            pairs.append((a[i], b[j]))
            used_a.add(i)
            used_b.add(j)
            if len(used_a) == len(a) or len(used_b) == len(b):
                break

        leftover_v1.extend(a[i] for i in range(len(a)) if i not in used_a)
        leftover_v2.extend(b[j] for j in range(len(b)) if j not in used_b)

    return pairs, leftover_v1, leftover_v2


def diff_parts(
    v1: list[Part],
    v2: list[Part],
    tol_mm: float = DEFAULT_TOL_MM,
    tol_deg: float = DEFAULT_TOL_DEG,
) -> DiffResult:
    """Diff two flat part lists.

    Pairing strategy: for each unique geometry signature, pair v1 and v2 parts of
    that signature by one-to-one greedy nearest-centroid assignment. Names (which
    are commonly auto-generated and renumber when parts are removed) are NOT used
    for matching — they're kept on the resulting entries for display only.
    """
    entries: list[DiffEntry] = []

    pairs, leftover_v1, leftover_v2 = _spatial_signature_pairing(v1, v2)
    for p1, p2 in pairs:
        entries.append(_classify_pair(p1, p2, tol_mm, tol_deg))

    for p in leftover_v1:
        entries.append(DiffEntry(status=Status.REMOVED, part_v1=p, part_v2=None))
    for p in leftover_v2:
        entries.append(DiffEntry(status=Status.ADDED, part_v1=None, part_v2=p))

    return DiffResult(entries=entries)
