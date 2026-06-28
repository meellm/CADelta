"""End-to-end test: build two synthetic STEP files, diff, write output, read it back."""
from __future__ import annotations

from pathlib import Path

import pytest

from cadelta.matcher import Status, diff_parts
from cadelta.reader import load_parts
from cadelta.writer import COLOR_BY_STATUS, write_diff

from .conftest import box_at, boxes_compound, loc_rotate_z, loc_translate, make_assembly_step, make_step


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
    # A unchanged + B moved (new pos) + B moved_from ghost (old pos) + D added + C removed = 5
    assert labels.Length() == 5

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
    from cadelta.writer import COLOR_MOVED_FROM
    def rgb(triple: tuple[float, float, float]) -> tuple[float, float, float]:
        r, g, b = triple
        return round(r, 2), round(g, 2), round(b, 2)

    # Find each by status tag in the name. MOVED_FROM must be checked BEFORE MOVED
    # because "MOVED" is a substring of "MOVED_FROM".
    a = next(v for k, v in found_status.items() if "A" in k and "UNCHANGED" in k)
    b_to = next(v for k, v in found_status.items() if "[MOVED]" in k and "B" in k)
    b_from = next(v for k, v in found_status.items() if "MOVED_FROM" in k and "B" in k)
    d = next(v for k, v in found_status.items() if "D" in k and "ADDED" in k)
    c = next(v for k, v in found_status.items() if "C" in k and "REMOVED" in k)

    assert a == rgb(COLOR_BY_STATUS[Status.UNCHANGED])
    assert b_to == rgb(COLOR_BY_STATUS[Status.MOVED])
    assert b_from == rgb(COLOR_MOVED_FROM)
    assert d == rgb(COLOR_BY_STATUS[Status.ADDED])
    assert c == rgb(COLOR_BY_STATUS[Status.REMOVED])

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


def test_compound_leaf_split_into_individual_parts(tmp_path: Path):
    """A leaf shape that's a TopoDS_Compound packing N solids must split into N
    individual Parts so that moving one of them lights up only that one — not the
    whole batch. Mirrors the "ECAD exports 147 screws as one component" pattern.

    Without splitting, the matcher would see ONE Part per side with a centroid
    that's the average of all 5 boxes; moving the middle box would shift that
    average and report the entire compound as MOVED, defeating the visual diff."""
    positions_v1 = [(0, 0, 0), (20, 0, 0), (40, 0, 0), (60, 0, 0), (80, 0, 0)]
    # The middle box moves +50 mm in Y; the other four stay put.
    positions_v2 = [(0, 0, 0), (20, 0, 0), (40, 50, 0), (60, 0, 0), (80, 0, 0)]

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [("Batch", boxes_compound(positions_v1))])
    make_step(v2, [("Batch", boxes_compound(positions_v2))])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    assert len(parts_v1) == 5, f"compound leaf should split into 5 parts, got {len(parts_v1)}"
    assert len(parts_v2) == 5

    result = diff_parts(parts_v1, parts_v2)
    counts = {s: len(result.by_status(s)) for s in Status}
    assert counts == {
        Status.UNCHANGED: 4,
        Status.MOVED: 1,
        Status.ADDED: 0,
        Status.REMOVED: 0,
    }, f"expected 4 unchanged + 1 moved, got {counts}"

    moved = result.by_status(Status.MOVED)[0]
    assert moved.delta_mm == pytest.approx(50.0, abs=1e-3), (
        f"the one moved box should report ~50mm delta, got {moved.delta_mm}"
    )


def test_compound_leaf_handles_added_and_removed_subshapes(tmp_path: Path):
    """A compound leaf where one sub-solid is removed and another is added
    between v1 and v2 should report exactly one REMOVED and one ADDED entry —
    not the whole batch as MOVED. Sub-solids must have signature-distinct
    sizes so the matcher can tell which one is which."""
    # Cubes of distinct sizes so each sub-solid has a unique signature.
    # v1 has cubes A(5), B(7), C(9); v2 keeps A and C, drops B, adds D(11).
    from .conftest import box_at
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    def cmp_of(parts):
        builder = BRep_Builder()
        c = TopoDS_Compound()
        builder.MakeCompound(c)
        for s, x in parts:
            builder.Add(c, box_at(s, s, s, x, 0, 0))
        return c

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [("Bag", cmp_of([(5, 0), (7, 30), (9, 60)]))])
    make_step(v2, [("Bag", cmp_of([(5, 0), (9, 60), (11, 90)]))])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    assert len(parts_v1) == 3
    assert len(parts_v2) == 3

    result = diff_parts(parts_v1, parts_v2)
    counts = {s: len(result.by_status(s)) for s in Status}
    assert counts == {
        Status.UNCHANGED: 2,
        Status.MOVED: 0,
        Status.ADDED: 1,
        Status.REMOVED: 1,
    }, f"expected 2 unchanged + 1 added + 1 removed, got {counts}"


def test_compound_leaf_split_with_color_preservation(tmp_path: Path):
    """When a compound leaf carries a single XCAF color, every split sub-part
    must inherit that color so the diff.step doesn't lose visual identity for
    batched components."""
    from .conftest import box_at
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    builder = BRep_Builder()
    cmp = TopoDS_Compound()
    builder.MakeCompound(cmp)
    for x in (0.0, 30.0, 60.0):
        builder.Add(cmp, box_at(10, 10, 10, x, 0, 0))

    teal = (0.10, 0.55, 0.65)
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [("Triplet", cmp, teal)])
    make_step(v2, [("Triplet", cmp, teal)])

    parts_v1 = load_parts(v1)
    assert len(parts_v1) == 3
    for p in parts_v1:
        assert p.color is not None, "split sub-part lost its inherited color"
        for got, want in zip(p.color, teal):
            assert abs(got - want) < 0.01

    # End-to-end: nothing changed → all UNCHANGED → all keep teal in diff.step
    result = diff_parts(parts_v1, load_parts(v2))
    assert all(e.status == Status.UNCHANGED for e in result.entries)
    out = tmp_path / "diff.step"
    write_diff(result, out)
    diff_parts_loaded = load_parts(out)
    assert len(diff_parts_loaded) == 3
    for p in diff_parts_loaded:
        for got, want in zip(p.color, teal):
            assert abs(got - want) < 0.02


def test_nested_compound_flattens_to_solids(tmp_path: Path):
    """A compound containing other compounds (containing solids) should still
    flatten to one Part per solid — the splitter must recurse, not stop at the
    first compound boundary."""
    from .conftest import box_at
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    builder = BRep_Builder()
    inner_a = TopoDS_Compound()
    builder.MakeCompound(inner_a)
    builder.Add(inner_a, box_at(10, 10, 10, 0, 0, 0))
    builder.Add(inner_a, box_at(10, 10, 10, 20, 0, 0))

    inner_b = TopoDS_Compound()
    builder.MakeCompound(inner_b)
    builder.Add(inner_b, box_at(10, 10, 10, 40, 0, 0))

    outer = TopoDS_Compound()
    builder.MakeCompound(outer)
    builder.Add(outer, inner_a)
    builder.Add(outer, inner_b)

    step = tmp_path / "nested.step"
    make_step(step, [("Nested", outer)])

    parts = load_parts(step)
    assert len(parts) == 3, f"nested compound should flatten to 3 solids, got {len(parts)}"
    centroids = sorted(tuple(round(c, 3) for c in p.centroid) for p in parts)
    assert centroids == [(5.0, 5.0, 5.0), (25.0, 5.0, 5.0), (45.0, 5.0, 5.0)]


def test_diff_step_regroups_unchanged_compound_subshapes(tmp_path: Path):
    """When a compound leaf was split for matching, UNCHANGED siblings sharing the
    same color must be re-merged into a single compound on output. Without this,
    a 100-body batch where one body moves would balloon diff.step to 100 separate
    XCAF labels (one STYLED_ITEM each), making the file slow to open in CAD apps.

    Setup: a 5-box compound where the middle box moves +50mm.
    Expected output topology:
      - 1 free COMPOUND containing the 4 unchanged boxes (1 STYLED_ITEM)
      - 1 free SOLID for the moved box at its v2 position (yellow)
      - 1 free SOLID for the moved-from ghost at its v1 position (hot pink)
      = 3 free shapes total, NOT 5.
    """
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID

    positions_v1 = [(0, 0, 0), (20, 0, 0), (40, 0, 0), (60, 0, 0), (80, 0, 0)]
    positions_v2 = [(0, 0, 0), (20, 0, 0), (40, 50, 0), (60, 0, 0), (80, 0, 0)]

    teal = (0.10, 0.55, 0.65)
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [("Batch", boxes_compound(positions_v1), teal)])
    make_step(v2, [("Batch", boxes_compound(positions_v2), teal)])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    # Sanity: every sub-part of the same compound got the same source_group.
    groups_v2 = {p.source_group for p in parts_v2}
    assert len(groups_v2) == 1 and None not in groups_v2, (
        f"all 5 sub-parts should share one source_group, got {groups_v2}"
    )

    result = diff_parts(parts_v1, parts_v2)
    out_step = tmp_path / "diff.step"
    write_diff(result, out_step)

    # Inspect XCAF topology of the output: free shapes by type.
    from cadelta.reader import _read_doc
    doc = _read_doc(out_step)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)

    from collections import Counter
    types = Counter()
    for i in range(1, free.Length() + 1):
        s = XCAFDoc_ShapeTool.GetShape_s(free.Value(i))
        types[s.ShapeType()] += 1
    # 1 COMPOUND (4 unchanged regrouped) + 2 SOLIDs (moved at v2, moved-from at v1).
    assert types == Counter({TopAbs_COMPOUND: 1, TopAbs_SOLID: 2}), (
        f"expected 1 COMPOUND + 2 SOLIDs; got {dict(types)}. "
        "If 5 SOLIDs, the writer is not regrouping the UNCHANGED siblings."
    )

    # End-to-end colors after re-load:
    #   4 unchanged sub-parts (teal) + 1 moved at v2 (yellow) + 1 moved-from ghost (hot pink)
    from cadelta.writer import COLOR_MOVED_FROM
    parts_diff = load_parts(out_step)
    assert len(parts_diff) == 6, "expected 4 unchanged + moved + moved-from ghost"
    moved_to = next(p for p in parts_diff if "[MOVED]" in p.name)
    moved_from = next(p for p in parts_diff if "MOVED_FROM" in p.name)
    moved_rgb = COLOR_BY_STATUS[Status.MOVED]
    ghost_rgb = COLOR_MOVED_FROM
    for got, want in zip(moved_to.color, moved_rgb):
        assert abs(got - want) < 0.02, f"moved should be {moved_rgb}, got {moved_to.color}"
    for got, want in zip(moved_from.color, ghost_rgb):
        assert abs(got - want) < 0.02, f"moved-from ghost should be {ghost_rgb}, got {moved_from.color}"
    unchanged = [p for p in parts_diff if "UNCHANGED" in p.name]
    assert len(unchanged) == 4
    for p in unchanged:
        assert p.color is not None, "unchanged sub-part lost color through re-merge"
        for got, want in zip(p.color, teal):
            assert abs(got - want) < 0.02, (
                f"unchanged should keep teal {teal}; got {p.color}"
            )


def test_moved_part_emits_v1_ghost_and_v2_new_position(tmp_path: Path):
    """A MOVED part should appear TWICE in diff.step:
      - yellow at the new v2 world-space position, and
      - hot pink (COLOR_MOVED_FROM) at the old v1 world-space position.
    The two entries let users visually trace where each moved part came from."""
    from cadelta.writer import COLOR_MOVED_FROM

    box = box_at(15, 15, 15)
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    # v1 carries a single named part at origin; v2 has the same part shifted +30mm in X.
    make_step(v1, [("Brick", box)])
    make_step(v2, [("Brick", box_at(15, 15, 15, 30, 0, 0))])

    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    result = diff_parts(parts_v1, parts_v2)
    assert len(result.entries) == 1 and result.entries[0].status == Status.MOVED

    out = tmp_path / "diff.step"
    write_diff(result, out)

    parts_diff = load_parts(out)
    moved_to = [p for p in parts_diff if "[MOVED]" in p.name]
    moved_from = [p for p in parts_diff if "MOVED_FROM" in p.name]
    assert len(moved_to) == 1, f"expected one [MOVED] entry, got {len(moved_to)}"
    assert len(moved_from) == 1, f"expected one [MOVED_FROM] ghost, got {len(moved_from)}"

    # Geometry: the v2 part's centroid is at the new position (37.5, 7.5, 7.5);
    # the ghost's centroid is at v1's old position (7.5, 7.5, 7.5).
    assert moved_to[0].centroid == pytest.approx([37.5, 7.5, 7.5], abs=1e-6)
    assert moved_from[0].centroid == pytest.approx([7.5, 7.5, 7.5], abs=1e-6)

    # Colors: yellow at v2 new position, hot pink at v1 ghost.
    moved_rgb = COLOR_BY_STATUS[Status.MOVED]
    for got, want in zip(moved_to[0].color, moved_rgb):
        assert abs(got - want) < 0.02
    for got, want in zip(moved_from[0].color, COLOR_MOVED_FROM):
        assert abs(got - want) < 0.02


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


def test_diff_step_preserves_master_instance_sharing(tmp_path: Path):
    """When v2 is an assembly that references one master shape from N components,
    diff.step must preserve that master/instance graph instead of baking N
    independent geometry copies.

    Without this, the writer's `_bake_location` flattens every Part's world
    transform into a fresh duplicated geometry, producing files that grow
    linearly with instance count. On real assemblies (e.g. an electronics
    board with 100 identical screws referenced from one master) that means
    diff.step balloons to 4×+ the size of v2.step, making the output slow or
    impossible to open in third-party CAD viewers.

    This test compares the in-place mutation path (passing `doc_v2`) against
    the legacy bake path (`doc_v2=None`) for the same diff. The in-place
    output should be markedly smaller, proving sharing is preserved."""
    from cadelta.reader import load_parts_with_doc

    # Five instances of one master box, identical between v1 and v2 → all
    # UNCHANGED, no deltas, no bakes from v1. The only knob exercised is
    # whether v2's master/instance graph is preserved.
    box = box_at(20, 20, 20)
    instances = [
        ("inst1", box, loc_translate(0, 0, 0)),
        ("inst2", box, loc_translate(50, 0, 0)),
        ("inst3", box, loc_translate(100, 0, 0)),
        ("inst4", box, loc_translate(150, 0, 0)),
        ("inst5", box, loc_translate(200, 0, 0)),
    ]
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, instances)
    make_assembly_step(v2, instances)

    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    assert len(parts_v1) == 5 and len(parts_v2) == 5

    result = diff_parts(parts_v1, parts_v2)
    assert all(e.status == Status.UNCHANGED for e in result.entries), (
        f"identical assemblies should all pair as UNCHANGED, got "
        f"{[e.status for e in result.entries]}"
    )

    # In-place path: pass doc_v2 to preserve v2's master/instance graph.
    out_inplace = tmp_path / "diff_inplace.step"
    write_diff(result, out_inplace, doc_v2=doc_v2)

    # Legacy bake path: same diff, no doc_v2 → every instance is baked.
    # Re-run diff because the in-place writer mutated doc_v2 in place; the
    # legacy run only looks at the entries, which are independent of doc_v2.
    parts_v1b = load_parts(v1)
    parts_v2b = load_parts(v2)
    result_b = diff_parts(parts_v1b, parts_v2b)
    out_legacy = tmp_path / "diff_legacy.step"
    write_diff(result_b, out_legacy)

    inplace_size = out_inplace.stat().st_size
    legacy_size = out_legacy.stat().st_size
    # The legacy path bakes 5 independent geometry copies; the in-place path
    # reuses v2's single master with 5 component refs. The ratio should be
    # comfortably below 1× — pin it at 0.7× to allow some metadata jitter
    # while still catching the regression that motivated this test.
    assert inplace_size < legacy_size * 0.7, (
        f"In-place output ({inplace_size} bytes) should be markedly smaller "
        f"than the bake path ({legacy_size} bytes); ratio "
        f"{inplace_size / legacy_size:.2f}. Master/instance sharing was lost."
    )


def test_diff_bodies_attached_to_v2_assembly_for_viewer_compat(tmp_path: Path):
    """When v2 is an assembly, every diff body (MOVED, ADDED, REMOVED, and the
    MOVED-from ghost) must end up as a *component* of that assembly — not as
    parallel free shapes — and each must carry its diff color on its master
    label.

    Regression for the visual bug where diff.step looked identical to v2 in
    third-party CAD viewers: viewers that only render the main assembly tree
    would skip parallel free shapes entirely (so REMOVED + MOVED_FROM ghosts
    were invisible), and viewers that ignore component-level color overrides
    on shared masters would show MOVED/ADDED in v2's original color
    (so the diff highlights were invisible).

    This test pins both invariants down by inspecting the output's XCAF tree
    AND the raw STEP entities."""
    import re
    from cadelta.reader import load_parts_with_doc
    from cadelta.writer import COLOR_BY_STATUS, COLOR_MOVED_FROM
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDF import TDF_LabelSequence

    # Distinct sizes so the matcher can pair them by signature unambiguously.
    box_a = box_at(20, 20, 20)
    box_b = box_at(15, 15, 15)
    box_c = box_at(10, 10, 10)
    box_d = box_at(8, 8, 8)
    v1_parts = [
        ("a", box_a, loc_translate(0, 0, 0)),
        ("b", box_b, loc_translate(50, 0, 0)),
        ("c", box_c, loc_translate(100, 0, 0)),
    ]
    v2_parts = [
        ("a", box_a, loc_translate(0, 0, 0)),
        ("b", box_b, loc_translate(50, 30, 0)),  # moved
        ("d", box_d, loc_translate(150, 0, 0)),  # added
    ]
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, v1_parts)
    make_assembly_step(v2, v2_parts)

    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)

    out = tmp_path / "diff.step"
    write_diff(result, out, doc_v2=doc_v2)

    # Topology: exactly ONE free shape (the v2 assembly). No parallel
    # free shapes carrying diff bodies — viewers that ignore parallel
    # free shapes would lose them.
    parts_diff, doc = load_parts_with_doc(out)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)
    assert free.Length() == 1, (
        f"diff.step should have exactly one free shape (the v2 assembly); "
        f"got {free.Length()}. Extra free shapes mean diff bodies leaked "
        "outside the assembly tree and would be invisible in some viewers."
    )

    # Every diff body must carry its specific color and be reachable via
    # load_parts (which walks the assembly).
    by_color: dict[tuple, list] = {}
    for p in parts_diff:
        if p.color is None:
            continue
        key = tuple(round(c, 2) for c in p.color)
        by_color.setdefault(key, []).append(p)

    expected_colors = {
        tuple(round(c, 2) for c in COLOR_BY_STATUS[Status.MOVED]),
        tuple(round(c, 2) for c in COLOR_BY_STATUS[Status.ADDED]),
        tuple(round(c, 2) for c in COLOR_BY_STATUS[Status.REMOVED]),
        tuple(round(c, 2) for c in COLOR_MOVED_FROM),
    }
    found_colors = set(by_color.keys())
    missing = expected_colors - found_colors
    assert not missing, (
        f"Missing diff colors in output: {missing}. "
        f"Found: {found_colors}. The viewer would not see the diff highlights."
    )

    # Raw STEP entity check: at least one explicit color entity for each of
    # the four diff colors. This is what third-party viewers parse — verifying
    # in the load_parts API alone would mask serialization problems where
    # cadelta's reader recovers colors from somewhere viewers don't honor.
    text = out.read_text()
    color_entities = re.findall(
        r"COLOUR_RGB\([^)]+\)|DRAUGHTING_PRE_DEFINED_COLOUR\([^)]+\)", text,
    )
    assert len(set(color_entities)) >= 4, (
        f"Expected at least 4 distinct color entities in STEP output "
        f"(MOVED, ADDED, REMOVED, MOVED_FROM); found {len(set(color_entities))}: "
        f"{set(color_entities)}"
    )


def test_unchanged_assembly_instances_collapse_under_parent(tmp_path: Path):
    """Regression: when v2 is structured as an assembly of N components that all
    share ONE master shape (the "screw_18 component with N screw instances"
    pattern from real CAD exports), the writer must merge the UNCHANGED
    instances back under a single component child of their parent assembly —
    NOT leave them as N separate component slots that clutter the user's CAD
    viewer tree.

    This is the assembly-of-instances counterpart to the
    `test_diff_step_regroups_unchanged_compound_subshapes` test, which covers
    the true-`TopoDS_Compound`-leaf path. Both must produce a single merged
    output node for the user to perceive the diff as "still one component
    with the screws inside it"."""
    from cadelta.reader import load_parts_with_doc
    from cadelta.writer import COLOR_MOVED_FROM
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence

    N = 12
    master_box = box_at(5.0, 5.0, 5.0)
    instances_v1 = [(f"s{i}", master_box, loc_translate(i * 8.0, 0, 0)) for i in range(N)]
    # v2: same structure except the middle instance moves +30mm in Y.
    instances_v2 = list(instances_v1)
    instances_v2[N // 2] = (f"s{N // 2}", master_box, loc_translate((N // 2) * 8.0, 30.0, 0))

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, instances_v1)
    make_assembly_step(v2, instances_v2)

    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    # Sanity: every part has source_group=None (assembly of singletons, not a
    # split compound) — this is exactly the structural pattern that, before
    # this fix, would leave UNCHANGED siblings as N individual component slots.
    assert all(p.source_group is None for p in parts_v2)

    result = diff_parts(parts_v1, parts_v2)
    counts = {s: len(result.by_status(s)) for s in Status}
    # The matcher pairs by signature+centroid; the moved one moves far enough
    # to register as MOVED, the rest as UNCHANGED.
    assert counts[Status.UNCHANGED] == N - 1
    assert counts[Status.MOVED] == 1

    out = tmp_path / "diff.step"
    write_diff(result, out, doc_v2=doc_v2)

    # Inspect the output's XCAF tree at the parent level. Before the fix the
    # parent had N components (the N-1 surviving UNCHANGED instances + the
    # baked MOVED + the MOVED_FROM ghost = N+1). After the fix it has a
    # single collapsed sub-assembly + the deltas.
    _parts_diff, doc_out = load_parts_with_doc(out)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc_out.Main())
    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)
    assert free.Length() == 1
    root = free.Value(1)
    assert XCAFDoc_ShapeTool.IsAssembly_s(root)
    root_comps = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetComponents_s(root, root_comps)
    # Expected children of root: 1 collapsed UNCHANGED group + 1 MOVED + 1 MOVED_FROM = 3.
    # Without the collapse this was N + 1 = 13.
    assert root_comps.Length() == 3, (
        f"expected 3 root components after collapse (1 UNCHANGED group + 1 MOVED + "
        f"1 MOVED_FROM); got {root_comps.Length()}. The N-1 unchanged instances "
        "are leaking out as individual components instead of being merged."
    )

    # The collapsed UNCHANGED master should still be a "real" assembly with N-1
    # component children, each pointing back to the original master shape so
    # STEP serialization preserves master/instance sharing (file size win).
    parts_diff = load_parts(out)
    unchanged = [p for p in parts_diff if "UNCHANGED" in p.name]
    assert len(unchanged) == N - 1, (
        f"every UNCHANGED instance should still be reachable inside the "
        f"collapsed group; got {len(unchanged)} / expected {N - 1}"
    )

    # Master/instance sharing check: file should not balloon. Compare against
    # the v2 input — diff.step adds a few baked delta bodies but the bulk of
    # v2's geometry should still be referenced once, not duplicated N times.
    v2_size = v2.stat().st_size
    diff_size = out.stat().st_size
    assert diff_size < v2_size * 4.0, (
        f"diff.step ({diff_size} B) is more than 4× v2.step ({v2_size} B). "
        "The collapse pass is likely baking each instance as independent "
        "geometry instead of preserving master/instance sharing."
    )

    # MOVED + ghost data is still present and untouched by the collapse.
    moved = [p for p in parts_diff if "[MOVED]" in p.name]
    moved_from = [p for p in parts_diff if "MOVED_FROM" in p.name]
    assert len(moved) == 1
    assert len(moved_from) == 1


def test_collapse_pass_does_not_merge_distinct_masters(tmp_path: Path):
    """Critical guardrail: the collapse pass must NOT merge UNCHANGED siblings
    that reference *different* master shapes, even when they share a parent
    and a color. If it did, every flat assembly with N unique parts would get
    collapsed into one compound — exactly the "whole project under one
    component" regression we explicitly want to prevent.

    Setup: an assembly of 5 differently-sized boxes (5 distinct masters) under
    one parent, all unchanged between v1 and v2. The output must keep all 5
    as separate component slots."""
    from cadelta.reader import load_parts_with_doc
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence

    # 5 distinct masters → 5 distinct signatures → distinct XCAF labels.
    boxes = [box_at(s, s, s) for s in (5, 7, 9, 11, 13)]
    instances = [(f"p{i}", b, loc_translate(i * 30.0, 0, 0)) for i, b in enumerate(boxes)]

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, instances)
    make_assembly_step(v2, instances)

    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)
    assert all(e.status == Status.UNCHANGED for e in result.entries)

    out = tmp_path / "diff.step"
    write_diff(result, out, doc_v2=doc_v2)

    _parts, doc_out = load_parts_with_doc(out)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc_out.Main())
    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)
    root = free.Value(1)
    root_comps = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetComponents_s(root, root_comps)
    # All 5 should remain individual under the parent — no collapse.
    assert root_comps.Length() == 5, (
        f"expected 5 individual components (different masters → no collapse); "
        f"got {root_comps.Length()}. This is the 'whole project under one "
        "component' regression — collapse is firing on distinct masters."
    )


def test_collapse_pass_does_not_lose_added_or_removed(tmp_path: Path):
    """Belt-and-braces: with N instances of one master that include MOVED,
    ADDED, and REMOVED siblings alongside the UNCHANGED ones, the collapse
    pass must preserve every status. Lost MOVED/ADDED/REMOVED entries was a
    prior regression the user explicitly warned about."""
    from cadelta.reader import load_parts_with_doc
    from cadelta.writer import COLOR_MOVED_FROM

    master = box_at(5.0, 5.0, 5.0)
    instances_v1 = [
        ("a", master, loc_translate(0, 0, 0)),       # unchanged
        ("b", master, loc_translate(20, 0, 0)),      # unchanged
        ("c", master, loc_translate(40, 0, 0)),      # unchanged
        ("d", master, loc_translate(60, 0, 0)),      # will be MOVED in v2
        ("e", master, loc_translate(80, 0, 0)),      # will be REMOVED in v2
    ]
    instances_v2 = [
        ("a", master, loc_translate(0, 0, 0)),
        ("b", master, loc_translate(20, 0, 0)),
        ("c", master, loc_translate(40, 0, 0)),
        ("d", master, loc_translate(60, 50, 0)),     # MOVED
        ("f", master, loc_translate(100, 0, 0)),     # ADDED (replaces e)
    ]

    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_assembly_step(v1, instances_v1)
    make_assembly_step(v2, instances_v2)

    parts_v1 = load_parts(v1)
    parts_v2, doc_v2 = load_parts_with_doc(v2)
    result = diff_parts(parts_v1, parts_v2)
    counts = {s: len(result.by_status(s)) for s in Status}
    # Sanity: matcher pairs by signature+centroid. With all instances sharing
    # one master (and therefore one signature), the greedy nearest-centroid
    # assignment determines what's MOVED vs ADDED/REMOVED. Validate the count
    # *categories* rather than which specific letters wound up in each bucket.
    assert counts[Status.UNCHANGED] >= 3
    assert counts[Status.MOVED] + counts[Status.REMOVED] >= 1
    assert counts[Status.MOVED] + counts[Status.ADDED] >= 1

    out = tmp_path / "diff.step"
    write_diff(result, out, doc_v2=doc_v2)

    parts_diff = load_parts(out)
    # Every non-UNCHANGED status that the diff reported must still be visible
    # by tag in the output's part names — proof that the collapse didn't
    # absorb or erase those bodies.
    if counts[Status.MOVED] > 0:
        assert any("[MOVED]" in p.name for p in parts_diff), "MOVED body lost"
        assert any("MOVED_FROM" in p.name for p in parts_diff), "MOVED_FROM ghost lost"
    if counts[Status.REMOVED] > 0:
        assert any("REMOVED" in p.name for p in parts_diff), "REMOVED body lost"
    if counts[Status.ADDED] > 0:
        assert any("ADDED" in p.name for p in parts_diff), "ADDED body lost"


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
