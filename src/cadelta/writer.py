from __future__ import annotations

from pathlib import Path
from typing import Optional

from .matcher import DiffEntry, DiffResult, Status
from .reader import Part, _label_entry

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


def _find_root_assembly(shape_tool):
    """Return the first free-shape label that's an assembly, or ``None``.

    Diff bodies (REMOVED, MOVED_FROM ghosts, baked MOVED/ADDED replacements)
    are attached as *components* of this assembly so third-party CAD viewers
    that only render the main assembly tree still see them. Without this,
    parallel free shapes go invisible in many viewers.
    """
    from OCP.TDF import TDF_LabelSequence
    free = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free)
    for i in range(1, free.Length() + 1):
        lab = free.Value(i)
        if shape_tool.IsAssembly_s(lab):
            return lab
    return None


def _attach_baked_as_component(
    shape_tool, color_tool, asm_label, baked, display_name: str,
    rgb: tuple[float, float, float],
):
    """Add ``baked`` as a new component of ``asm_label`` and color the master.

    Coloring the master (rather than the component) is critical for viewer
    compatibility: many third-party CAD viewers honor only master-level
    color attributes, ignoring per-instance overrides. Each baked body has
    its own fresh master (no sharing with other parts), so master-level
    color produces the right per-body diff coloring everywhere.

    Returns the component label (or None on failure).
    """
    from OCP.TDF import TDF_Label
    comp = shape_tool.AddComponent(asm_label, baked, False)
    if comp.IsNull():
        return None
    # AddComponent created a fresh master internally; resolve it so we can
    # attach the diff color and name there.
    master = TDF_Label()
    if shape_tool.GetReferredShape_s(comp, master) and not master.IsNull():
        _set_name(master, display_name)
        _set_label_color(color_tool, master, rgb)
    else:
        # Fallback if the master isn't reachable — set on the component itself.
        _set_name(comp, display_name)
        _set_label_color(color_tool, comp, rgb)
    return comp


def _recolor_v2_doc_in_place(diff: DiffResult, doc_v2):
    """Walk v2 entries against ``doc_v2``, leaving UNCHANGED leaves in place
    and excising every other leaf so the bake path can re-emit it with a
    universally-honored master-level color.

    Why we no longer try to recolor MOVED/ADDED in place: third-party CAD
    viewers don't reliably honor color attributes set on *component* labels
    of a shared master. They render every instance in the master's color,
    which makes our diff highlights invisible in the viewer. Component
    excision + master-level baked replacement is the only universally
    portable approach.

    Returns the list of entries that need fresh baked replacements. The
    UNCHANGED majority stays in place, preserving v2's master/instance
    sharing — that's where the file-size win still comes from.
    """
    from collections import defaultdict
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_Label

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc_v2.Main())

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

    # Collect master labels of any component we excise so we can prune them
    # later if RemoveComponent leaves them orphaned (no remaining references).
    # Without this cleanup the orphan masters get promoted to free shapes by
    # XCAF and the reader sees them as ghost bodies at the origin.
    orphan_master_entries: set[str] = set()

    for entry_str, entries in v2_by_entry.items():
        label = _lookup_label(doc_v2, entry_str)
        if label is None:
            # Label disappeared somehow — fall back to baking.
            leftover.extend(entries)
            continue

        statuses = {e.status for e in entries}

        if statuses == {Status.UNCHANGED}:
            # Leave the v2 leaf untouched: keeps original v2 color and the
            # master/instance sharing that makes diff.step compact. We only
            # tag the component's display name so the diff "[UNCHANGED]"
            # marker is visible in viewers that show label names.
            _prefix_label_name(label, "UNCHANGED")
            continue

        # Any non-UNCHANGED entry on this leaf — single status or mixed —
        # gets routed to the bake path. We excise the v2 component first
        # so the same body doesn't end up rendered twice (once at v2 in
        # the original color, once at v2 baked in the new color).
        # Capture the referenced master BEFORE excising so we can prune it
        # afterwards if it becomes orphaned.
        master = TDF_Label()
        had_master = shape_tool.GetReferredShape_s(label, master) and not master.IsNull()
        master_entry = _label_entry(master) if had_master else None

        # RemoveComponent works for assembly components; RemoveShape works
        # for free-shape leaves. Try RemoveComponent first since reader's
        # `addressable_label` is the deepest component for assembly inputs.
        removed_ok = False
        try:
            shape_tool.RemoveComponent(label)
            removed_ok = True
        except Exception:
            removed_ok = False
        if not removed_ok:
            removed_ok = bool(shape_tool.RemoveShape(label))

        if removed_ok:
            leftover.extend(entries)
            if master_entry is not None:
                orphan_master_entries.add(master_entry)
        else:
            # Couldn't excise the original — emit the deltas on top anyway
            # so the user at least sees them in the viewer. UNCHANGED
            # siblings of a mixed leaf keep their original v2 color via
            # the surviving leaf.
            leftover.extend(
                e for e in entries if e.status != Status.UNCHANGED
            )

    # Prune orphan masters: any tracked master that's now `IsFree_s` (no
    # component references remaining) gets removed, otherwise the reader
    # would treat it as a ghost free shape at the origin.
    for master_entry in orphan_master_entries:
        ml = _lookup_label(doc_v2, master_entry)
        if ml is None or ml.IsNull():
            continue
        if XCAFDoc_ShapeTool.IsFree_s(ml):
            shape_tool.RemoveShape(ml)

    return leftover


def _emit_baked_into_doc(
    doc,
    v2_entries: list[DiffEntry],
    removed_entries: list[DiffEntry],
    moved_ghost_entries: list[DiffEntry],
) -> None:
    """Add fresh baked labels to ``doc`` for entries that need their own
    geometry (and master-level color) in the output.

    This is the only path that calls :func:`_bake_location`. Bodies handled
    here:

    - leftover v2 entries (MOVED, ADDED, mixed-leaf sub-parts) excised from
      the v2 assembly by :func:`_recolor_v2_doc_in_place`,
    - REMOVED parts (only present in v1, baked at v1 world position),
    - MOVED-from ghosts (soft-pink overlay at v1 world position).

    When ``doc`` contains a root assembly, baked bodies are attached as
    *components* of that assembly with their color set on the master label.
    This is the universally-portable path: third-party CAD viewers honor
    master-level color and render the assembly's children.

    When no root assembly exists (synthetic test docs built via
    :func:`make_step` from flat free shapes), baked bodies are added as
    parallel free shapes — the legacy behavior that keeps the existing
    test fixtures' assertions about free-shape topology valid.

    Sub-parts of the same source-compound that came through as leftovers
    and share status+color are re-grouped into a single TopoDS_Compound so
    the output stays compact.
    """
    from collections import defaultdict
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    root_asm = _find_root_assembly(shape_tool)

    def _emit_label(shape, display_name: str, rgb: tuple[float, float, float]) -> None:
        if root_asm is not None:
            # Assembly present: attach as component, set color on master so
            # all viewers (including ones that ignore component-level color
            # overrides on shared masters) render the diff highlight.
            _attach_baked_as_component(
                shape_tool, color_tool, root_asm, shape, display_name, rgb,
            )
        else:
            # Doc has no assembly — fall back to free-shape addition, same
            # as the legacy from-scratch writer path.
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

    from OCP.XCAFDoc import XCAFDoc_DocumentTool

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

    # Rebuild assembly bounds/structure after RemoveComponent and AddComponent
    # mutations so STEP serialization sees a consistent tree. Without this,
    # newly-attached components can be missing from the assembly's effective
    # shape, which surfaces as them being absent in third-party CAD viewers.
    XCAFDoc_DocumentTool.ShapeTool_s(doc.Main()).UpdateAssemblies()

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
