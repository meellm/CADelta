"""Stress benchmark: how does CADelta scale with part count?

Run directly: `.venv/bin/python tests/bench.py [N]`
Not part of the pytest suite — too slow.
"""
from __future__ import annotations

import gc
import resource
import sys
import time
from pathlib import Path

# Make sure we use the package from src/ and the tests/ helpers
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from conftest import box_at, make_step  # noqa: E402
from cadelta.reader import load_parts  # noqa: E402
from cadelta.matcher import diff_parts  # noqa: E402
from cadelta.writer import write_diff  # noqa: E402


def rss_mb() -> float:
    # macOS reports ru_maxrss in bytes; Linux in kilobytes.
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return raw / (1024 * 1024)
    return raw / 1024


def build_step(path: Path, n_parts: int, jitter: dict[int, float] | None = None,
               drop: set[int] | None = None, extra_offset_x: float = 0.0) -> None:
    """Place `n_parts` boxes in a 20-wide grid. `jitter[i]` adds Δ to box i's X.
    `drop` skips those indices (simulates removed parts)."""
    drop = drop or set()
    jitter = jitter or {}
    rows = int(n_parts ** 0.5) + 1
    parts = []
    for i in range(n_parts):
        if i in drop:
            continue
        gx = (i % rows) * 20 + jitter.get(i, 0.0) + extra_offset_x
        gy = (i // rows) * 20
        parts.append((f"Part{i:04d}", box_at(8, 8, 8, gx, gy, 0)))
    make_step(path, parts)


def bench(n: int, with_gltf: bool, tmp: Path) -> dict:
    v1 = tmp / f"v1_{n}.step"
    v2 = tmp / f"v2_{n}.step"
    out_step = tmp / f"diff_{n}.step"
    out_glb = tmp / f"diff_{n}.glb"

    # Build v1 and v2 with: 1% moved, 1% removed, 1% added.
    n_changes = max(1, n // 100)
    moved = {i: 0.5 for i in range(0, n_changes)}
    removed = set(range(n_changes, 2 * n_changes))

    print(f"\n=== n={n}{' (+ glTF)' if with_gltf else ''} ===")
    t0 = time.perf_counter()
    build_step(v1, n)
    t_v1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    # v2: same n parts, jitter moved indices, drop removed indices, add new at end
    extra = [(f"NewPart{i:04d}", box_at(6, 6, 6, 1000 + i * 15, 0, 0)) for i in range(n_changes)]
    rows = int(n ** 0.5) + 1
    parts = []
    for i in range(n):
        if i in removed:
            continue
        gx = (i % rows) * 20 + moved.get(i, 0.0)
        gy = (i // rows) * 20
        parts.append((f"Part{i:04d}", box_at(8, 8, 8, gx, gy, 0)))
    make_step(v2, parts + extra)
    t_v2 = time.perf_counter() - t0

    print(f"  build v1     {t_v1:6.2f}s  ({v1.stat().st_size / 1024:.0f} KB)")
    print(f"  build v2     {t_v2:6.2f}s  ({v2.stat().st_size / 1024:.0f} KB)")

    t0 = time.perf_counter()
    parts_v1 = load_parts(v1)
    t_read1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    parts_v2 = load_parts(v2)
    t_read2 = time.perf_counter() - t0

    t0 = time.perf_counter()
    result = diff_parts(parts_v1, parts_v2)
    t_diff = time.perf_counter() - t0

    counts = {s.value: len(result.by_status(s)) for s in __import__("cadelta.matcher", fromlist=["Status"]).Status}

    t0 = time.perf_counter()
    write_diff(result, out_step, out_gltf=out_glb if with_gltf else None)
    t_write = time.perf_counter() - t0

    mem = rss_mb()
    print(f"  read v1      {t_read1:6.2f}s  ({len(parts_v1)} parts)")
    print(f"  read v2      {t_read2:6.2f}s  ({len(parts_v2)} parts)")
    print(f"  diff         {t_diff:6.2f}s  {counts}")
    print(f"  write        {t_write:6.2f}s  step={out_step.stat().st_size//1024} KB"
          + (f"  glb={out_glb.stat().st_size//1024} KB" if with_gltf else ""))
    print(f"  total        {t_v1 + t_v2 + t_read1 + t_read2 + t_diff + t_write:6.2f}s   peak RSS {mem:.0f} MB")
    return {
        "n": n, "with_gltf": with_gltf,
        "read_v1": t_read1, "read_v2": t_read2, "diff": t_diff, "write": t_write,
        "rss_mb": mem,
    }


if __name__ == "__main__":
    import tempfile
    sizes = [int(x) for x in sys.argv[1:]] or [50, 200, 500, 1000]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        results = []
        for n in sizes:
            for with_gltf in (False, True):
                gc.collect()
                results.append(bench(n, with_gltf, tmp))
        print("\n=== summary ===")
        print(f"{'n':>6} {'glTF':>6} {'read':>8} {'diff':>8} {'write':>8} {'rss_MB':>8}")
        for r in results:
            print(f"{r['n']:>6} {('yes' if r['with_gltf'] else 'no'):>6}"
                  f" {r['read_v1'] + r['read_v2']:>8.2f} {r['diff']:>8.2f}"
                  f" {r['write']:>8.2f} {r['rss_mb']:>8.0f}")
