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


def write_diff(
    diff: DiffResult,
    out_step: Path,
    out_gltf: Optional[Path] = None,
) -> None:
    from collections import defaultdict
    from OCP.XCAFDoc import (
        XCAFDoc_DocumentTool,
        XCAFDoc_ColorSurf,
        XCAFDoc_ColorGen,
    )
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    doc = _new_doc()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    # --- Bucket entries by output strategy ---
    # The reader splits batched compounds (e.g. "147 screws as one component") into
    # one Part per sub-solid for matching. Re-emitting all of them as separate XCAF
    # labels would balloon the diff.step (one STYLED_ITEM per body), so we re-merge
    # UNCHANGED siblings that came from the same compound and share the same color
    # back into a single compound on output. MOVED, ADDED, and REMOVED bodies stay
    # as individuals so their per-body coloring remains visible.
    unchanged_grouped: dict = defaultdict(list)
    v2_individuals: list = []
    v1_removed: list = []
    # MOVED entries get rendered TWICE: once at the v2 (new) position with the
    # high-contrast pink, and again as a softer pink ghost at the v1 (old)
    # position so it's clear where the part came from.
    moved_ghosts: list = []

    for entry in diff.entries:
        if entry.status == Status.REMOVED:
            if entry.part_v1 is not None and entry.part_v1.shape is not None:
                v1_removed.append(entry)
            continue
        part = entry.part_v2
        if part is None or part.shape is None:
            continue
        if entry.status == Status.UNCHANGED and part.source_group is not None:
            # Key on color too: the rare case where sub-parts of the same compound
            # carry different colors splits into distinct merged compounds, one
            # per color, so no per-body color is silently dropped.
            unchanged_grouped[(part.source_group, part.color)].append(entry)
        else:
            v2_individuals.append(entry)
        if (entry.status == Status.MOVED
                and entry.part_v1 is not None
                and entry.part_v1.shape is not None):
            moved_ghosts.append(entry)

    # A "group" with only one entry isn't worth wrapping in a compound — demote
    # those back to the individuals bucket so they render exactly as before.
    for key in list(unchanged_grouped.keys()):
        if len(unchanged_grouped[key]) == 1:
            v2_individuals.append(unchanged_grouped.pop(key)[0])

    def _emit_label(shape, display_name: str, rgb: tuple[float, float, float]) -> None:
        label = shape_tool.AddShape(shape, False)
        _set_name(label, display_name)
        qcolor = _quantity_color(rgb)
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorSurf)
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorGen)

    # --- Re-merged UNCHANGED groups: one compound, one color, one label ---
    for (_group_id, color), entries_in_group in unchanged_grouped.items():
        builder = BRep_Builder()
        merged = TopoDS_Compound()
        builder.MakeCompound(merged)
        for e in entries_in_group:
            builder.Add(merged, _bake_location(e.part_v2.shape))
        # Display name: drop the "[idx]" suffix the reader added so the merged
        # compound shows as "[UNCHANGED] component_screw_147" rather than "...[0]".
        rep_name = entries_in_group[0].part_v2.name.rsplit("[", 1)[0]
        display_name = f"[UNCHANGED] {rep_name}" if rep_name else "[UNCHANGED]"
        rgb = color if color is not None else COLOR_BY_STATUS[Status.UNCHANGED]
        _emit_label(merged, display_name, rgb)

    # --- Individual v2 parts: ADDED, MOVED, and solo UNCHANGED ---
    for entry in v2_individuals:
        part = entry.part_v2
        baked = _bake_location(part.shape)
        status_tag = {
            Status.ADDED: "ADDED",
            Status.MOVED: "MOVED",
            Status.UNCHANGED: "UNCHANGED",
        }[entry.status]
        display_name = f"[{status_tag}] {part.name}" if part.name else f"[{status_tag}]"
        if entry.status == Status.UNCHANGED and part.color is not None:
            rgb = part.color
        else:
            rgb = COLOR_BY_STATUS[entry.status]
        _emit_label(baked, display_name, rgb)

    # --- Removed parts: rendered in place at their original v1 world-space position ---
    # The reader stores each part with a TopLoc_Location carrying its world transform;
    # _bake_location() flattens that into geometry so the part sits where it used to be
    # in v1. Red color distinguishes removed parts from v2 ghosts that may overlap.
    for entry in v1_removed:
        part = entry.part_v1
        baked = _bake_location(part.shape)
        display_name = f"[REMOVED] {part.name}" if part.name else "[REMOVED]"
        _emit_label(baked, display_name, COLOR_BY_STATUS[Status.REMOVED])

    # --- Moved-from ghosts: render the v1 position of each moved part in soft pink ---
    # Pairs with the high-contrast pink at the v2 position so the user can visually
    # trace where each moved part came from.
    for entry in moved_ghosts:
        part = entry.part_v1
        baked = _bake_location(part.shape)
        display_name = f"[MOVED_FROM] {part.name}" if part.name else "[MOVED_FROM]"
        _emit_label(baked, display_name, COLOR_MOVED_FROM)

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
