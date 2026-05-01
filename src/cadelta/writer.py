from __future__ import annotations

from pathlib import Path
from typing import Optional

from .matcher import DiffEntry, DiffResult, Status
from .reader import Part

# RGB triplets in 0..1
COLOR_BY_STATUS = {
    Status.ADDED: (0.20, 0.80, 0.20),      # green
    Status.MOVED: (1.00, 0.85, 0.00),      # yellow
    Status.REMOVED: (0.90, 0.15, 0.15),    # red
    Status.UNCHANGED: (0.60, 0.60, 0.60),  # gray
}

REMOVED_OFFSET_GAP_MM = 50.0


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


def _bbox_xmax_xmin(shapes) -> tuple[float, float]:
    """Return (xmin, xmax) over the union of bounding boxes; (0, 0) if empty."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    found = False
    for s in shapes:
        if s is None or s.IsNull():
            continue
        BRepBndLib.Add_s(s, bb)
        found = True
    if not found or bb.IsVoid():
        return 0.0, 0.0
    xmin, _, _, xmax, _, _ = bb.Get()
    return xmin, xmax


def _shape_xmin_xmax(shape) -> tuple[float, float]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        return 0.0, 0.0
    xmin, _, _, xmax, _, _ = bb.Get()
    return xmin, xmax


def _translate_shape(shape, dx: float):
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(dx, 0.0, 0.0))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _bake_location(shape):
    """Bake any TopLoc_Location into the geometry so AddShape stores a flat free shape.

    The reader returns shapes carrying a world-space TopLoc_Location. If we hand those
    to XCAFDoc_ShapeTool.AddShape with makeAssembly=False, XCAF still auto-splits them
    into master+instance pairs, which produces a master/instance graph that OCCT itself
    can re-read but that third-party CAD viewers may reject. Baking the location into a
    fresh copy of the geometry sidesteps that path entirely.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    loc = shape.Location()
    if loc.IsIdentity():
        return shape
    return BRepBuilderAPI_Transform(shape, loc.Transformation(), True).Shape()


def write_diff(
    diff: DiffResult,
    out_step: Path,
    out_gltf: Optional[Path] = None,
) -> None:
    from OCP.XCAFDoc import (
        XCAFDoc_DocumentTool,
        XCAFDoc_ColorSurf,
        XCAFDoc_ColorGen,
    )
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static

    doc = _new_doc()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    # --- v2 parts: added / moved / unchanged ---
    v2_shapes = []
    for entry in diff.entries:
        if entry.status == Status.REMOVED:
            continue
        part = entry.part_v2
        if part is None or part.shape is None:
            continue
        baked = _bake_location(part.shape)
        v2_shapes.append(baked)
        label = shape_tool.AddShape(baked, False)
        status_tag = {
            Status.ADDED: "ADDED",
            Status.MOVED: "MOVED",
            Status.UNCHANGED: "UNCHANGED",
        }[entry.status]
        display_name = f"[{status_tag}] {part.name}" if part.name else f"[{status_tag}]"
        _set_name(label, display_name)
        qcolor = _quantity_color(COLOR_BY_STATUS[entry.status])
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorSurf)
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorGen)

    # --- removed parts: translated outside the v2 bbox in +X ---
    _, v2_xmax = _bbox_xmax_xmin(v2_shapes)
    drop_x = v2_xmax + REMOVED_OFFSET_GAP_MM
    for entry in diff.entries:
        if entry.status != Status.REMOVED:
            continue
        part = entry.part_v1
        if part is None or part.shape is None:
            continue
        baked = _bake_location(part.shape)
        rxmin, rxmax = _shape_xmin_xmax(baked)
        dx = drop_x - rxmin
        moved = _translate_shape(baked, dx)
        # advance drop_x for the next removed part
        drop_x = drop_x + (rxmax - rxmin) + REMOVED_OFFSET_GAP_MM

        label = shape_tool.AddShape(moved, False)
        display_name = f"[REMOVED] {part.name}" if part.name else "[REMOVED]"
        _set_name(label, display_name)
        qcolor = _quantity_color(COLOR_BY_STATUS[Status.REMOVED])
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorSurf)
        color_tool.SetColor(label, qcolor, XCAFDoc_ColorGen)

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
