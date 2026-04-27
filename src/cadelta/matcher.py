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


def _rotation_delta_deg(t1: np.ndarray, t2: np.ndarray) -> float:
    r1 = t1[:3, :3]
    r2 = t2[:3, :3]
    rel = r1.T @ r2
    cos_theta = (np.trace(rel) - 1.0) / 2.0
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_theta)))


def _classify_pair(p1: Part, p2: Part, tol_mm: float, tol_deg: float) -> DiffEntry:
    dmm = _translation_delta(p1, p2)
    ddeg = _rotation_delta_deg(p1.transform, p2.transform)
    status = Status.UNCHANGED if (dmm <= tol_mm and ddeg <= tol_deg) else Status.MOVED
    return DiffEntry(status=status, part_v1=p1, part_v2=p2, delta_mm=dmm, delta_deg=ddeg)


def _pair_by_key(
    v1: list[Part],
    v2: list[Part],
    key,
) -> tuple[list[tuple[Part, Part]], list[Part], list[Part]]:
    """Pair parts whose `key(part)` matches across the two lists. Greedy: first-seen wins."""
    buckets_v1: dict = defaultdict(list)
    buckets_v2: dict = defaultdict(list)
    for p in v1:
        buckets_v1[key(p)].append(p)
    for p in v2:
        buckets_v2[key(p)].append(p)

    pairs: list[tuple[Part, Part]] = []
    leftover_v1: list[Part] = []
    leftover_v2: list[Part] = []

    all_keys = set(buckets_v1) | set(buckets_v2)
    for k in all_keys:
        a = buckets_v1.get(k, [])
        b = buckets_v2.get(k, [])
        n = min(len(a), len(b))
        for i in range(n):
            pairs.append((a[i], b[i]))
        leftover_v1.extend(a[n:])
        leftover_v2.extend(b[n:])
    return pairs, leftover_v1, leftover_v2


def diff_parts(
    v1: list[Part],
    v2: list[Part],
    tol_mm: float = DEFAULT_TOL_MM,
    tol_deg: float = DEFAULT_TOL_DEG,
) -> DiffResult:
    """Diff two flat part lists. Match by name first, then by geometry signature."""
    entries: list[DiffEntry] = []

    # Pass A: name-based pairing (skip empty/anonymous names so they fall to signature pass).
    named_v1 = [p for p in v1 if p.name and p.name != "<unnamed>"]
    named_v2 = [p for p in v2 if p.name and p.name != "<unnamed>"]
    anon_v1 = [p for p in v1 if not p.name or p.name == "<unnamed>"]
    anon_v2 = [p for p in v2 if not p.name or p.name == "<unnamed>"]

    name_pairs, name_leftover_v1, name_leftover_v2 = _pair_by_key(
        named_v1, named_v2, key=lambda p: p.name
    )
    for p1, p2 in name_pairs:
        entries.append(_classify_pair(p1, p2, tol_mm, tol_deg))

    # Pass B: signature-based pairing on whatever's left.
    remaining_v1 = name_leftover_v1 + anon_v1
    remaining_v2 = name_leftover_v2 + anon_v2

    sig_pairs, leftover_v1, leftover_v2 = _pair_by_key(
        remaining_v1, remaining_v2, key=lambda p: p.signature
    )
    for p1, p2 in sig_pairs:
        entries.append(_classify_pair(p1, p2, tol_mm, tol_deg))

    for p in leftover_v1:
        entries.append(DiffEntry(status=Status.REMOVED, part_v1=p, part_v2=None))
    for p in leftover_v2:
        entries.append(DiffEntry(status=Status.ADDED, part_v1=None, part_v2=p))

    return DiffResult(entries=entries)
