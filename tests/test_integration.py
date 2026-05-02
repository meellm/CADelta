"""End-to-end test: build two synthetic STEP files, diff, write output, read it back."""
from __future__ import annotations

from pathlib import Path

import pytest

from cadelta.matcher import Status, diff_parts
from cadelta.reader import load_parts
from cadelta.writer import COLOR_BY_STATUS, write_diff

from .conftest import box_at, loc_rotate_z, loc_translate, make_assembly_step, make_step


@pytest.fixture
def step_pair(tmp_path: Path):
    """Build v1.step and v2.step with a known set of changes."""
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"

    # v1: A (unchanged), B (will be moved), C (will be removed)
    make_step(v1, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40, 0, 0)),
        ("C", box_at(20, 20, 20, 80, 0, 0)),
    ])
    # v2: A (unchanged), B moved by 0.5mm in +X, D added (new), C gone
    make_step(v2, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40.5, 0, 0)),
        ("D", box_at(12, 12, 12, 120, 0, 0)),
    ])
    return v1, v2


def test_load_parts_round_trip(step_pair):
    v1, v2 = step_pair
    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)

    assert len(parts_v1) == 3
    assert len(parts_v2) == 3
    names_v1 = {p.name for p in parts_v1}
    assert {"A", "B", "C"} <= names_v1, f"got names: {names_v1}"


def test_diff_classifies_each_part_correctly(step_pair):
    v1, v2 = step_pair
    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    result = diff_parts(parts_v1, parts_v2)

    by_name = {(e.part_v2 or e.part_v1).name: e for e in result.entries}
    assert by_name["A"].status == Status.UNCHANGED
    assert by_name["B"].status == Status.MOVED
    assert by_name["B"].delta_mm == pytest.approx(0.5, abs=1e-6)
    assert by_name["C"].status == Status.REMOVED
    assert by_name["D"].status == Status.ADDED


def test_write_diff_produces_step_and_glb(step_pair, tmp_path: Path):
    v1, v2 = step_pair
    result = diff_parts(load_parts(v1), load_parts(v2))
    out_step = tmp_path / "diff.step"
    out_glb = tmp_path / "diff.glb"

    write_diff(result, out_step, out_gltf=out_glb)

    assert out_step.exists() and out_step.stat().st_size > 0
    assert out_glb.exists() and out_glb.stat().st_size > 0


def test_diff_step_has_correct_colors_and_positions(step_pair, tmp_path: Path):
    """Read the produced diff.step back and verify each part has the expected color
    and that the removed part sits at its original v1 world-space position."""
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorTool, XCAFDoc_ColorSurf
    from OCP.TDF import TDF_LabelSequence
    from OCP.Quantity import Quantity_Color
    from OCP.TDataStd import TDataStd_Name

    v1, v2 = step_pair
    result = diff_parts(load_parts(v1), load_parts(v2))
    out_step = tmp_path / "diff.step"
    write_diff(result, out_step)

    # Re-open the produced STEP via the reader.
    from cadelta.reader import _read_doc
    doc = _read_doc(out_step)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    assert labels.Length() == 4  # A unchanged + B moved + D added + C removed = 4

    found_status: dict[str, tuple[float, float, float]] = {}
    for i in range(1, labels.Length() + 1):
        lab = labels.Value(i)
        name_attr = TDataStd_Name()
        name = ""
        if lab.FindAttribute(TDataStd_Name.GetID_s(), name_attr):
            name = name_attr.Get().ToExtString()
        col = Quantity_Color()
        ok = XCAFDoc_ColorTool.GetColor_s(lab, XCAFDoc_ColorSurf, col)
        assert ok, f"label {name!r} has no surface color"
        rgb = (round(col.Red(), 2), round(col.Green(), 2), round(col.Blue(), 2))
        found_status[name] = rgb

    # Resolve expected colors (rounded to 2 decimals to be tolerant)
    def rgb(s: Status) -> tuple[float, float, float]:
        r, g, b = COLOR_BY_STATUS[s]
        return round(r, 2), round(g, 2), round(b, 2)

    # Find each by status tag in the name.
    a = next(v for k, v in found_status.items() if "A" in k and "UNCHANGED" in k)
    b = next(v for k, v in found_status.items() if "B" in k and "MOVED" in k)
    d = next(v for k, v in found_status.items() if "D" in k and "ADDED" in k)
    c = next(v for k, v in found_status.items() if "C" in k and "REMOVED" in k)

    assert a == rgb(Status.UNCHANGED)
    assert b == rgb(Status.MOVED)
    assert d == rgb(Status.ADDED)
    assert c == rgb(Status.REMOVED)

    # Verify the removed part sits at its original v1 world-space position.
    # In the fixture, C is a 20x20x20 box centered at (80, 0, 0) — its centroid
    # in diff.step should match.
    parts_v1 = load_parts(v1)
    c_v1 = next(p for p in parts_v1 if p.name == "C")
    parts_diff = load_parts(out_step)
    removed_part = next(p for p in parts_diff if "REMOVED" in p.name)
    assert removed_part.centroid == pytest.approx(c_v1.centroid, abs=1e-6), (
        f"removed part centroid {removed_part.centroid} should match v1 position {c_v1.centroid}"
    )


def test_removed_part_in_assembly_step_preserves_v1_position(tmp_path: Path):
    """Regression: when a removed part comes from an assembly whose pose lives in a
    non-identity TopLoc_Location, diff.step must place the part at its original v1
    world-space position. The earlier _bake_location implementation applied the
    location twice (yielding L²(geometry)) on assembly-style inputs, which the
    baked-geometry test fixtures didn't catch.

    The fixture intentionally avoids any chance of name-based pairing (the
    assembly-style STEP files don't preserve component names through the round-
    trip, and XCAF's auto-generated label names can spuriously collide between
    versions). v2 contains a single, signature-distinct filler so v1's target
    falls all the way through to the REMOVED bucket."""
    target_box = box_at(20, 20, 20)  # 20×20×20 at origin (master)
    filler_box = box_at(3, 3, 3)     # tiny cube — completely different signature

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, [("Target", target_box, loc_translate(50, 0, 0))])
    make_step(v2, [("Filler", filler_box)])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    assert len(parts_v1) == 1 and len(parts_v2) == 1

    target_v1 = parts_v1[0]
    # Sanity: the assembly-style v1 reports the target's world-space centroid as
    # the center of a 20³ box at origin (50,0,0) → (60, 10, 10).
    assert target_v1.centroid == pytest.approx([60.0, 10.0, 10.0], abs=1e-6)

    result = diff_parts(parts_v1, parts_v2)
    # Must have produced a REMOVED entry for the target.
    removed = [e for e in result.entries if e.status == Status.REMOVED]
    assert len(removed) == 1, f"expected 1 REMOVED entry, got {len(removed)}"

    out_step = tmp_path / "diff.step"
    write_diff(result, out_step)

    parts_diff = load_parts(out_step)
    removed_in_diff = next(p for p in parts_diff if "REMOVED" in p.name)

    assert removed_in_diff.centroid == pytest.approx(target_v1.centroid, abs=1e-6), (
        f"Removed-part centroid {removed_in_diff.centroid} should match v1 centroid "
        f"{target_v1.centroid}. The L² bake bug would yield ~(110, 10, 10)."
    )


def test_rotation_invariant_to_representation(tmp_path: Path):
    """Regression: a part rotated 90° around Z is reported as UNCHANGED whether the
    rotation lives in v1's TopLoc_Location or v2's baked geometry. The previous
    matcher compared XCAF transform rotation blocks and would flag this case as
    MOVED, since v1's transform = R90 but v2's transform = identity.

    90° was chosen because it permutes the box's AABB dimensions (40×10×5 →
    10×40×5) without changing the *sorted* dimensions, so the signature-based
    matcher still pairs the two parts. A 30° rotation would grow the AABB and
    the parts would fail to pair via signature."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    import math
    from OCP.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir

    # 40x10x5 cuboid is asymmetric (3 distinct principal moments) so its inertia
    # frame is well-defined — the test wouldn't be meaningful for a cube.
    box = box_at(40, 10, 5)

    # v1: rotation stored as the component's TopLoc_Location.
    v1 = tmp_path / "v1.step"
    make_assembly_step(v1, [("Bar", box, loc_rotate_z(90.0))])

    # v2: same physical pose, but rotation baked into geometry as a flat free shape.
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(90.0))
    box_rotated = BRepBuilderAPI_Transform(box, rot, True).Shape()
    v2 = tmp_path / "v2.step"
    make_step(v2, [("Bar", box_rotated)])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    assert len(parts_v1) == 1 and len(parts_v2) == 1, (
        f"expected 1 part each; got v1={len(parts_v1)}, v2={len(parts_v2)}"
    )
    # Sanity: same world-space pose → same centroid.
    assert parts_v1[0].centroid == pytest.approx(parts_v2[0].centroid, abs=1e-6)

    result = diff_parts(parts_v1, parts_v2)
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.status == Status.UNCHANGED, (
        f"Expected UNCHANGED but got {entry.status} "
        f"(delta_mm={entry.delta_mm}, delta_deg={entry.delta_deg}). "
        "If delta_deg is ~90, the matcher is comparing XCAF transforms again "
        "instead of world-space inertia orientations."
    )


def test_unchanged_part_preserves_v2_color(tmp_path: Path):
    """A part that's identical between v1 and v2 should appear in diff.step with
    its ORIGINAL v2 color, not the gray UNCHANGED fallback. ADDED/MOVED parts
    still get their status color (cyan/pink)."""
    from cadelta.writer import COLOR_BY_STATUS

    # Both v1 and v2 carry an unchanged 20³ box; v2 colors it teal. v2 also
    # introduces an added 8³ box that should come out cyan.
    teal = (0.10, 0.55, 0.65)
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [("Box", box_at(20, 20, 20))])
    make_step(v2, [
        ("Box", box_at(20, 20, 20), teal),
        ("Newcomer", box_at(8, 8, 8, 100, 0, 0), (0.5, 0.5, 0.5)),  # any color; ADDED overrides
    ])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    result = diff_parts(parts_v1, parts_v2)
    out = tmp_path / "diff.step"
    write_diff(result, out)

    parts_diff = load_parts(out)
    assert len(parts_diff) == 2

    unchanged = next(p for p in parts_diff if "UNCHANGED" in p.name)
    added = next(p for p in parts_diff if "ADDED" in p.name)

    assert unchanged.color is not None, "unchanged part lost its color attribute"
    for got, want in zip(unchanged.color, teal):
        assert abs(got - want) < 0.01, (
            f"unchanged part should keep v2's teal {teal}; got {unchanged.color}"
        )

    expected_added = COLOR_BY_STATUS[Status.ADDED]
    assert added.color is not None
    for got, want in zip(added.color, expected_added):
        assert abs(got - want) < 0.01, (
            f"added part should be cyan {expected_added}; got {added.color}"
        )


def test_cli_help_runs():
    """Smoke-test the CLI entrypoint."""
    from click.testing import CliRunner
    from cadelta.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "cadelta" in result.output.lower() or "compare two step" in result.output.lower()


def test_cli_full_run_produces_outputs(step_pair, tmp_path: Path):
    from click.testing import CliRunner
    from cadelta.cli import main

    v1, v2 = step_pair
    out_step = tmp_path / "diff.step"
    out_glb = tmp_path / "diff.glb"
    out_report = tmp_path / "report.json"

    runner = CliRunner()
    result = runner.invoke(main, [
        str(v1), str(v2),
        "-o", str(out_step),
        "--gltf", str(out_glb),
        "--report", str(out_report),
    ])
    assert result.exit_code == 0, f"CLI failed:\n{result.output}\n{result.exception}"
    assert out_step.exists() and out_step.stat().st_size > 0
    assert out_glb.exists() and out_glb.stat().st_size > 0
    assert out_report.exists()

    import json
    report = json.loads(out_report.read_text())
    assert report["counts"]["added"] == 1
    assert report["counts"]["removed"] == 1
    assert report["counts"]["moved"] == 1
    assert report["counts"]["unchanged"] == 1
