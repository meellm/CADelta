# 🎨 CADelta

**Visual diff for STEP CAD files.**

Compare two versions of a mechanical assembly and see — in a single colored output — exactly what changed: parts added, removed, or moved.

## Features

- 🩵 **Cyan** — parts added in v2
- 💖 **Hot pink** — parts moved (rendered at their NEW v2 position, in high-contrast pink) — paired with a softer pink ghost rendered at the OLD v1 position so you can trace the movement
- 🔴 **Bright red** — parts removed (rendered in place at their original v1 position so you can see exactly what was lost where)
- 🎨 **Original** — unchanged parts keep whatever color v2 had assigned to them, so the output reads like v2 with the change overlays popping out (gray fallback when v2 has no color set)
- Smart matching — pairs parts by geometry signature (volume, surface area, sorted bounding-box, face count) and world-space centroid, so renames, re-exports, and STEP entity renumbering don't trigger spurious diffs.
- Centroid-based movement detection — works whether position is encoded as an XCAF transform or baked into geometry coordinates.
- Compact output — diff.step is produced by mutating v2's XCAF document in place (recoloring leaves), so the original master/instance sharing is preserved and file size stays roughly proportional to v2.step rather than ballooning with instance count.
- Multiple outputs — colored STEP for your CAD app, GLB for any browser glTF viewer, JSON report for scripting.
- Configurable tolerance, defaulting to 0.01 mm / 0.01°.
- Cross-platform (macOS, Linux, Windows). Pure pip, no conda.

## 🚀 Quick Start

```bash
git clone https://github.com/meellm/CADelta.git
cd CADelta
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Then:
```bash
cadelta v1.step v2.step -o diff.step                     # Colored STEP for your CAD app
cadelta v1.step v2.step -o diff.step --gltf diff.glb     # + GLB for browser viewers
cadelta v1.step v2.step -o diff.step --report diff.json  # + JSON status report
```

Open `diff.step` in your CAD application, or drag `diff.glb` into a glTF viewer such as <https://gltf-viewer.donmccurdy.com> (local file; nothing is uploaded).

> **Windows:** activate the venv with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

## 🎯 What it detects

| Color     | Status    | Triggers when…                                                                                              |
|-----------|-----------|-------------------------------------------------------------------------------------------------------------|
| 🩵 Cyan   | added     | A part exists in v2 but not in v1.                                                                          |
| 💖 Hot pink + soft pink ghost | moved | A part exists in both, but its world-space center moved > 0.01 mm or rotated > 0.01°. Hot pink at the new v2 position, soft pink at the old v1 position. |
| 🔴 Bright red | removed | A part exists in v1 but not in v2. Rendered at its original v1 world-space position so the loss is visible in context. |
| 🎨 Original | unchanged | Matching geometry and same pose. Keeps the original v2 color (gray fallback if none was set).             |

## 📋 Commands

| Command | Description |
|---------|-------------|
| `cadelta V1 V2 -o OUT.step` | Compare V1 against V2 and write a colored STEP file. |
| `cadelta V1 V2 -o OUT.step --gltf OUT.glb` | Also export a binary glTF for browser viewing. |
| `cadelta V1 V2 -o OUT.step --report OUT.json` | Also emit a JSON report (per-part status and deltas). |
| `cadelta V1 V2 -o OUT.step --tol-mm 0.05 --tol-deg 0.1` | Loosen movement tolerance. |
| `cadelta --help` | Show all options. |

## 🔍 How matching works

For each leaf part in v1 and v2, CADelta builds:

- a **geometry signature** — `(volume, surface area, sorted bbox dims, face count)`, computed on the master shape so it is pose-invariant,
- a **world-space centroid** computed from the located volume, so movement is detected whether position lives in an XCAF transform or in the geometry itself,
- a **world-space orientation** derived from the principal axes of inertia of the located shape, so rotation detection is invariant to how the rotation is encoded (transform vs baked geometry).

The matcher then:

1. **Spatial signature pairing.** Parts with identical signatures are grouped, then within each group v1 and v2 parts are matched one-to-one by closest centroid (greedy: smallest distance first). Names are intentionally ignored — STEP files routinely use auto-generated identifiers (e.g. `NAUO123`) that renumber when parts are removed, which would mis-pair physically different parts if used naively.
2. For each pair, if Δcentroid ≤ 0.01 mm and Δrotation ≤ 0.01° the part is **unchanged**; otherwise it is **moved**. Rotation is computed from inertia-frame orientation, so axisymmetric parts (cylinders, fasteners) skip the rotation check.
3. Anything still unmatched in v1 is **removed**; anything still unmatched in v2 is **added**.

## 📊 Performance

Measured locally on synthetic flat assemblies (Apple Silicon Mac):

| Parts | Read v1+v2 | Diff | Write STEP | Total | Peak RSS |
|-------|-----------|------|-----------|-------|----------|
| 200   | 0.6 s     | 0.03 s  | 0.1 s   | 1.5 s  | 355 MB   |
| 1,000 | 2.9 s     | 0.9 s   | 0.6 s   | 5.6 s  | 712 MB   |
| 3,000 | 9.6 s     | 8.8 s   | 1.9 s   | 25 s   | 2.4 GB   |

The bench inputs are pathological for the matcher: every part shares the same geometry signature, so the inner candidate-pair sort runs at O(n²). Real-world assemblies have varied geometry — distinct signatures bucket parts into small groups, and the matcher is essentially free. File sizes also stay close to v2.step on real assemblies because the writer preserves the original master/instance graph instead of baking copies.

## 🧪 Tests

```bash
pytest                          # 30 tests — pure logic + end-to-end round trip
python tests/bench.py 1000      # Stress benchmark
```

## 🛠️ Architecture

| Layer | Module | Purpose |
|-------|--------|---------|
| CLI       | [`src/cadelta/cli.py`](src/cadelta/cli.py)             | Click-based entrypoint. |
| Reader    | [`src/cadelta/reader.py`](src/cadelta/reader.py)       | STEP → list of `Part(name, shape, transform, signature, centroid)`. |
| Signature | [`src/cadelta/signature.py`](src/cadelta/signature.py) | Pose-invariant geometry fingerprint. |
| Matcher   | [`src/cadelta/matcher.py`](src/cadelta/matcher.py)     | Signature + nearest-centroid pairing; classifies each pair. |
| Writer    | [`src/cadelta/writer.py`](src/cadelta/writer.py)       | Builds the colored output STEP and optional GLB. |

OCCT (OpenCascade) bindings come from [`cadquery-ocp`](https://pypi.org/project/cadquery-ocp/) — pure pip, no conda required.

## ⚠️ Known limitations

- Multiple identically-named parts pair greedily by list order. If your CAD swaps two `Bolt` instances, both may be reported as moved even though the assembly is unchanged.
- Pure rotations of a part whose pose is *baked into geometry* (no separate transform) are not detected. Translations are.
- Names containing `/` collide with the path separator used for the assembly hierarchy.
- Sheet bodies and other zero-volume shapes have an undefined centroid; results may be unreliable for those parts.

Contributions are welcome.

## 📄 License

MIT
