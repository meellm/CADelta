from __future__ import annotations

from pathlib import Path
from typing import Optional

from .matcher import DiffEntry, DiffResult, Status
from .reader import Part

# RGB triplets in 0..1.
# UNCHANGED parts use whatever color the source v2 STEP assigned to them; this entry
# is the gray fallback applied only when the v2 part has no color attribute set.
COLOR_BY_STATUS = {
    Status.ADDED: (0.00, 0.80, 0.95),      # cyan
    Status.MOVED: (1.00, 0.05, 0.50),      # hot pink (high-contrast: the NEW position)
    Status.REMOVED: (1.00, 0.00, 0.00),    # pure red (high-contrast)
    Status.UNCHANGED: (0.60, 0.60, 0.60),  # gray (fallback only)
}

# Ghost color rendered at the OLD (v1) position of a moved part: a softer,
# lower-contrast pink that visually pairs with COLOR_BY_STATUS[Status.MOVED]
# while staying clearly subordinate to it. The high-contrast pink at v2's new
# position is what the user's eye should be drawn to; the ghost answers
# "where did it come from?".
COLOR_MOVED_FROM = (1.00, 0.65, 0.80)

def _new_doc():
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    return doc


def _quantity_color(rgb: tuple[float, float, float]):
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    return Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_RGB)


def _set_name(label, name: str) -> None:
    from OCP.TDataStd import TDataStd_Name
    from OCP.TCollection import TCollection_ExtendedString
    if not name:
        return
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))


def _shape_xmin_xmax(shape) -> tuple[float, float]:
    """Return (xmin, xmax) of a shape's axis-aligned bounding box; (0, 0) if void."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return 0.0, 0.0
    xmin, _, _, xmax, _, _ = bb.Get()
    return xmin, xmax


def _bake_location(shape):
    """Bake any TopLoc_Location into the geometry so AddShape stores a flat free shape.

    The reader returns shapes carrying a world-space TopLoc_Location. If we hand those
    to XCAFDoc_ShapeTool.AddShape with makeAssembly=False, XCAF still auto-splits them
    into master+instance pairs, which produces a master/instance graph that OCCT itself
    can re-read but that third-party CAD viewers may reject. Baking the location into a
    fresh copy of the geometry sidesteps that path entirely.

    Note: BRepBuilderAPI_Transform composes the given trsf ON TOP of the shape's
    existing location. Passing a located shape together with its own location's trsf
    would apply the transform twice (L · L = L²). We strip the location to identity
    first, then apply the trsf — yielding a fresh shape at L(geometry) with identity
    location, which is what we actually want.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.TopLoc import TopLoc_Location
    loc = shape.Location()
    if loc.IsIdentity():
        return shape
    bare = shape.Located(TopLoc_Location())
    return BRepBuilderAPI_Transform(bare, loc.Transformation(), True).Shape()


_STATUS_TAG = {
    Status.ADDED: "ADDED",
    Status.MOVED: "MOVED",
    Status.UNCHANGED: "UNCHANGED",
    Status.REMOVED: "REMOVED",
}


def _prefix_label_name(label, status_tag: str) -> None:
    """Prefix the label's existing name with ``[STATUS_TAG]``.

    Idempotent: if the name already has any ``[...]`` prefix we replace it
    rather than stacking, so re-running the writer (or operating on labels
    whose names were set by us earlier) doesn't keep growing tags.
    """
    from OCP.TDataStd import TDataStd_Name
    from OCP.TCollection import TCollection_ExtendedString
    attr = TDataStd_Name()
    current = ""
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        try:
            current = attr.Get().ToExtString()
        except Exception:
            current = ""
    base = current
    if base.startswith("["):
        end = base.find("]")
        if end != -1:
            base = base[end + 1:].lstrip()
    new_name = f"[{status_tag}] {base}" if base else f"[{status_tag}]"
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(new_name))


def _lookup_label(doc, entry_str: str):
    """Resolve a TDF entry string back to a TDF_Label in ``doc``.

    Returns the label, or ``None`` if the entry no longer exists in the doc.
    """
    from OCP.TDF import TDF_Tool, TDF_Label
    from OCP.TCollection import TCollection_AsciiString
    label = TDF_Label()
    TDF_Tool.Label_s(doc.Main().Data(), TCollection_AsciiString(entry_str), label, False)
    if label.IsNull():
        return None
    return label


def _set_label_color(color_tool, label, rgb: tuple[float, float, float]) -> None:
    """Force-overwrite a label's surface and generic color attributes."""
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_ColorGen
    qcolor = _quantity_color(rgb)
    color_tool.SetColor(label, qcolor, XCAFDoc_ColorSurf)
    color_tool.SetColor(label, qcolor, XCAFDoc_ColorGen)


def _recolor_v2_doc_in_place(diff: DiffResult, doc_v2):
    """Walk the diff's v2 entries against ``doc_v2``, recoloring leaves in place.

    Returns the list of entries whose v2 leaf could NOT be handled in place — for
    example, a leaf whose split sub-parts carry mixed status (some MOVED, some
    UNCHANGED), or a leaf that lacks a ``label_entry``. These leftover entries
    are emitted afterwards by the legacy bake path so their per-body diff
    coloring remains visible.

    The big win: leaves where every sub-part is UNCHANGED, or every sub-part
    shares one non-unchanged status, are handled by overwriting a single XCAF
    color attribute. The geometry stays as-is, master/instance refs stay
    shared, and the output stays as compact as v2.step.
    """
    from collections import defaultdict
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc_v2.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc_v2.Main())

    # Index v2-side entries by their addressing label; sub-parts of a split
    # compound share one entry, so the value is a list.
    v2_by_entry: dict[str, list[DiffEntry]] = defaultdict(list)
    leftover: list[DiffEntry] = []

    for entry in diff.entries:
        if entry.status == Status.REMOVED:
            continue  # handled separately by the bake path; never in v2's doc
        part = entry.part_v2
        if part is None or part.shape is None:
            continue
        if part.label_entry is None:
            # Synthetic test parts and other doc-less inputs go through the bake
            # path. Real reader-loaded Parts always carry a label_entry.
            leftover.append(entry)
            continue
        v2_by_entry[part.label_entry].append(entry)

    for entry_str, entries in v2_by_entry.items():
        label = _lookup_label(doc_v2, entry_str)
        if label is None:
            # Label disappeared somehow — fall back to baking.
            leftover.extend(entries)
            continue

        statuses = {e.status for e in entries}

        if statuses == {Status.UNCHANGED}:
            # Tag the name only — original v2 color stays untouched, which is
            # exactly the "preserves v2's color" behavior we want, achieved
            # here for free.
            _prefix_label_name(label, "UNCHANGED")
            continue

        if len(statuses) == 1:
            # All sub-parts share one non-unchanged status: recolor the whole
            # leaf with that status's color.
            status = next(iter(statuses))
            _set_label_color(color_tool, label, COLOR_BY_STATUS[status])
            _prefix_label_name(label, _STATUS_TAG[status])
            continue

        # Mixed status across the same XCAF leaf: we can't recolor one body
        # inside a compound through a single label attribute. Try to remove
        # the leaf cleanly and let the legacy bake path re-emit each sub-part
        # as an individual coloured body. RemoveShape only succeeds for
        # free-shape labels — for component-referenced masters it returns
        # False, in which case we leave the leaf in place (with its original
        # v2 colors) and overlay baked deltas on top: not ideal visually but
        # keeps the file readable.
        if shape_tool.RemoveShape(label):
            leftover.extend(entries)
        else:
            # Couldn't excise the leaf — bake the moved/added sub-parts
            # anyway so their per-body color shows; the unchanged siblings
            # keep their original v2 color through the surviving leaf.
            leftover.extend(
                e for e in entries
                if e.status != Status.UNCHANGED
            )

    return leftover


def _emit_baked_into_doc(
    doc,
    v2_entries: list[DiffEntry],
    removed_entries: list[DiffEntry],
    moved_ghost_entries: list[DiffEntry],
) -> None:
    """Add fresh baked labels to ``doc`` for entries that aren't (or can no
    longer be) represented by an existing v2-doc leaf.

    This is the only path that calls :func:`_bake_location` — flattening
    world-space transforms into geometry. Restricted to:

    - REMOVED parts (only present in v1, must be added at v1 world position),
    - MOVED-from ghosts (the soft-pink overlay at v1 world position),
    - leftover v2 entries the in-place pass couldn't handle (mixed-status
      leaves; or doc-less synthetic inputs).

    Sub-parts of the same source-compound that came through as leftovers and
    share status+color are re-grouped into a single TopoDS_Compound so the
    output stays compact.
    """
    from collections import defaultdict
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def _emit_label(shape, display_name: str, rgb: tuple[float, float, float]) -> None:
        label = shape_tool.AddShape(shape, False)
        _set_name(label, display_name)
        _set_label_color(color_tool, label, rgb)

    # Bucket leftover v2 entries: sub-parts of the same source_group sharing one
    # status + color regroup into a single compound on output (avoids 100×
    # label/color bloat for batched components).
    unchanged_grouped: dict = defaultdict(list)
    moved_grouped: dict = defaultdict(list)
    added_grouped: dict = defaultdict(list)
    individuals: list[DiffEntry] = []

    for entry in v2_entries:
        part = entry.part_v2
        if part is None or part.shape is None:
            continue
        if part.source_group is None:
            individuals.append(entry)
            continue
        if entry.status == Status.UNCHANGED:
            unchanged_grouped[(part.source_group, part.color)].append(entry)
        elif entry.status == Status.MOVED:
            moved_grouped[part.source_group].append(entry)
        elif entry.status == Status.ADDED:
            added_grouped[part.source_group].append(entry)
        else:
            individuals.append(entry)

    def _demote_solo(grouped: dict) -> None:
        for key in list(grouped.keys()):
            if len(grouped[key]) == 1:
                individuals.append(grouped.pop(key)[0])

    _demote_solo(unchanged_grouped)
    _demote_solo(moved_grouped)
    _demote_solo(added_grouped)

    def _emit_compound(entries_in_group, status_tag, rgb_resolver):
        builder = BRep_Builder()
        merged = TopoDS_Compound()
        builder.MakeCompound(merged)
        for e in entries_in_group:
            builder.Add(merged, _bake_location(e.part_v2.shape))
        rep_name = entries_in_group[0].part_v2.name.rsplit("[", 1)[0]
        display_name = f"[{status_tag}] {rep_name}" if rep_name else f"[{status_tag}]"
        _emit_label(merged, display_name, rgb_resolver(entries_in_group[0]))

    for (_gid, color), grp in unchanged_grouped.items():
        rgb = color if color is not None else COLOR_BY_STATUS[Status.UNCHANGED]
        _emit_compound(grp, "UNCHANGED", lambda _e, _rgb=rgb: _rgb)
    for _gid, grp in moved_grouped.items():
        _emit_compound(grp, "MOVED", lambda _e: COLOR_BY_STATUS[Status.MOVED])
    for _gid, grp in added_grouped.items():
        _emit_compound(grp, "ADDED", lambda _e: COLOR_BY_STATUS[Status.ADDED])

    for entry in individuals:
        part = entry.part_v2
        baked = _bake_location(part.shape)
        tag = _STATUS_TAG[entry.status]
        display_name = f"[{tag}] {part.name}" if part.name else f"[{tag}]"
        if entry.status == Status.UNCHANGED and part.color is not None:
            rgb = part.color
        else:
            rgb = COLOR_BY_STATUS[entry.status]
        _emit_label(baked, display_name, rgb)

    # Removed parts: rendered at their original v1 world-space position.
    for entry in removed_entries:
        part = entry.part_v1
        if part is None or part.shape is None:
            continue
        baked = _bake_location(part.shape)
        display_name = f"[REMOVED] {part.name}" if part.name else "[REMOVED]"
        _emit_label(baked, display_name, COLOR_BY_STATUS[Status.REMOVED])

    # Moved-from ghosts: soft-pink overlay at the v1 world position so the
    # user can trace where each moved part came from.
    for entry in moved_ghost_entries:
        part = entry.part_v1
        if part is None or part.shape is None:
            continue
        baked = _bake_location(part.shape)
        display_name = f"[MOVED_FROM] {part.name}" if part.name else "[MOVED_FROM]"
        _emit_label(baked, display_name, COLOR_MOVED_FROM)


def write_diff(
    diff: DiffResult,
    out_step: Path,
    doc_v2=None,
    out_gltf: Optional[Path] = None,
) -> None:
    """Serialize a diff to a colored STEP file (and optionally a glTF/GLB).

    When ``doc_v2`` is provided (the XCAFDoc the reader populated from v2.step,
    obtained via :func:`cadelta.reader.load_parts_with_doc`), the writer mutates
    that doc in place — recoloring its existing leaves to indicate diff status,
    and adding fresh baked labels only for v1-derived deltas (REMOVED parts and
    MOVED-from ghosts). The output preserves v2.step's master/instance graph,
    keeping diff.step roughly the same size as v2.step.

    When ``doc_v2`` is None (the legacy code path used by tests that build
    Parts directly without a doc), every output label is built from baked
    geometry. This is correct but produces ~4× larger files for assemblies with
    repeated parts, because master/instance sharing is lost during baking.
    """
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static

    if doc_v2 is not None:
        doc = doc_v2
        leftover_v2 = _recolor_v2_doc_in_place(diff, doc)
    else:
        doc = _new_doc()
        # Everything from v2 goes through the bake path.
        leftover_v2 = [
            e for e in diff.entries
            if e.status != Status.REMOVED
            and e.part_v2 is not None
            and e.part_v2.shape is not None
        ]

    removed = [
        e for e in diff.entries
        if e.status == Status.REMOVED
        and e.part_v1 is not None
        and e.part_v1.shape is not None
    ]
    moved_ghosts = [
        e for e in diff.entries
        if e.status == Status.MOVED
        and e.part_v1 is not None
        and e.part_v1.shape is not None
    ]
    _emit_baked_into_doc(doc, leftover_v2, removed, moved_ghosts)

    out_step = Path(out_step)
    out_step.parent.mkdir(parents=True, exist_ok=True)

    # AP214IS is the canonical schema for color-bearing STEP files; default may emit
    # AP203 (no color support), which strict viewers reject when STYLED_ITEM entities
    # appear without matching schema declarations.
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")

    step_writer = STEPCAFControl_Writer()
    # Mirror the reader's symmetric setup so XCAF metadata (colors, names, layers) is
    # serialized as well-formed STEP entities. Without these the writer can emit color
    # references that OCCT tolerates on re-read but third-party CAD viewers reject.
    step_writer.SetColorMode(True)
    step_writer.SetNameMode(True)
    step_writer.SetLayerMode(True)
    if not step_writer.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError(f"Failed to transfer XCAFDoc into STEP writer: {out_step}")
    write_status = step_writer.Write(str(out_step))
    if write_status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to write STEP file (status={write_status}): {out_step}")

    if out_gltf is not None:
        _write_gltf(doc, Path(out_gltf))


def _write_gltf(doc, out_gltf: Path) -> None:
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TColStd import TColStd_IndexedDataMapOfStringString
    from OCP.Message import Message_ProgressRange
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence

    # Mesh every shape attached to free labels so glTF has triangles.
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    for i in range(1, labels.Length() + 1):
        s = XCAFDoc_ShapeTool.GetShape_s(labels.Value(i))
        if not s.IsNull():
            BRepMesh_IncrementalMesh(s, 0.5, False, 0.5, True)

    out_gltf.parent.mkdir(parents=True, exist_ok=True)
    is_binary = out_gltf.suffix.lower() == ".glb"
    writer = RWGltf_CafWriter(TCollection_AsciiString(str(out_gltf)), is_binary)
    metadata = TColStd_IndexedDataMapOfStringString()
    if not writer.Perform(doc, metadata, Message_ProgressRange()):
        raise RuntimeError(f"Failed to write glTF file: {out_gltf}")
