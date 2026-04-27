# 🎨 CADelta

**Visual diff for STEP CAD files.**

Compare two versions of a mechanical assembly and see — in a single colored output — exactly what changed: parts added, removed, or moved.

## Features

- 🟢 **Green** — parts added in v2
- 🟡 **Yellow** — parts moved (translation > 0.01 mm or rotation > 0.01°)
- 🔴 **Red** — parts removed (placed offset outside the v2 bounding box, so they don't pollute the live design)
- ⚪ **Gray** — unchanged
- Smart matching — pairs parts by name first, falls back to a geometry signature (volume, surface area, sorted bounding-box, face count) so a rename doesn't masquerade as remove + add.
- Centroid-based movement detection — works whether position is encoded as an XCAF transform or baked into geometry coordinates.
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
| 🟢 Green  | added     | A part exists in v2 but not in v1.                                                                          |
| 🟡 Yellow | moved     | A part exists in both, but its world-space center moved > 0.01 mm or rotated > 0.01°.                       |
| 🔴 Red    | removed   | A part exists in v1 but not in v2. The original geometry is placed +X outside v2's bbox to keep it visible. |
| ⚪ Gray   | unchanged | Same name (or matching geometry) and same pose.                                                             |

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

- a **path-style name** drawn from the assembly hierarchy (e.g. `Asm/SubA/Bolt`),
- a **geometry signature** — `(volume, surface area, sorted bbox dims, face count)`, computed on the master shape so it is pose-invariant,
- a **world-space centroid** computed from the located volume, so movement is detected whether position lives in an XCAF transform or in the geometry itself.

The matcher then:

1. **Name pairing.** A part with the same name in both versions becomes a candidate pair.
2. **Signature pairing** runs over what is left, so a rename does not show up as remove + add.
3. For each pair, if Δcentroid ≤ 0.01 mm and Δrotation ≤ 0.01° the part is **unchanged**; otherwise it is **moved**.
4. Anything still unmatched in v1 is **removed**; anything still unmatched in v2 is **added**.

## 📊 Performance

Measured locally on synthetic flat assemblies (Apple Silicon Mac):

| Parts | Read v1+v2 | Diff | Write STEP | Total | Peak RSS |
|-------|-----------|------|-----------|-------|----------|
| 200   | 0.5 s     | <0.01 s | 0.1 s   | 0.9 s  | 355 MB   |
| 1,000 | 2.9 s     | 0.01 s  | 1.0 s   | 5.1 s  | 636 MB   |
| 3,000 | 9.2 s     | 0.02 s  | 2.0 s   | 16 s   | 1.2 GB   |

Scaling is linear end-to-end; the matcher itself is essentially free. For very large assemblies (10,000+ parts) expect roughly one minute and ~4 GB of RAM.

## 🧪 Tests

```bash
pytest                          # 19 tests — pure logic + end-to-end round trip
python tests/bench.py 1000      # Stress benchmark
```

## 🛠️ Architecture

| Layer | Module | Purpose |
|-------|--------|---------|
| CLI       | [`src/cadelta/cli.py`](src/cadelta/cli.py)             | Click-based entrypoint. |
| Reader    | [`src/cadelta/reader.py`](src/cadelta/reader.py)       | STEP → list of `Part(name, shape, transform, signature, centroid)`. |
| Signature | [`src/cadelta/signature.py`](src/cadelta/signature.py) | Pose-invariant geometry fingerprint. |
| Matcher   | [`src/cadelta/matcher.py`](src/cadelta/matcher.py)     | Name → signature pairing; classifies each pair. |
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
