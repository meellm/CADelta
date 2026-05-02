"""Shared test fixtures: synthetic STEP file builders backed by OCP."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple


def make_step(path: Path, parts: Iterable[Tuple]) -> None:
    """Write a STEP file containing the given parts as free shapes.

    Each entry in `parts` is either:
      (name, shape)              — uncolored
      (name, shape, (r, g, b))   — surface-colored via XCAFDoc_ColorSurf
    """
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorSurf
    from OCP.TDataStd import TDataStd_Name
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    for entry in parts:
        if len(entry) == 2:
            name, shape = entry
            rgb = None
        else:
            name, shape, rgb = entry
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        if rgb is not None:
            qcolor = Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TOC_RGB)
            color_tool.SetColor(label, qcolor, XCAFDoc_ColorSurf)

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    assert writer.Transfer(doc, STEPControl_AsIs)
    status = writer.Write(str(path))
    assert status == IFSelect_RetDone, f"STEP write failed: {status}"


def box_at(size_x: float, size_y: float, size_z: float, x: float = 0.0, y: float = 0.0, z: float = 0.0):
    """Make an axis-aligned box of the given size, translated to (x, y, z)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    box = BRepPrimAPI_MakeBox(size_x, size_y, size_z).Shape()
    if (x, y, z) == (0.0, 0.0, 0.0):
        return box
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(x, y, z))
    return BRepBuilderAPI_Transform(box, trsf, True).Shape()


def boxes_compound(positions, size: float = 10.0):
    """Return a TopoDS_Compound packing one axis-aligned box at each `(x, y, z)`
    position. Models the "ECAD exports 147 screws as one batched component"
    pattern: a single XCAF leaf whose shape contains many discrete solids."""
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for x, y, z in positions:
        builder.Add(compound, box_at(size, size, size, x, y, z))
    return compound


def loc_translate(x: float, y: float, z: float):
    """Build a TopLoc_Location for a pure translation."""
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Trsf, gp_Vec
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(trsf)


def loc_rotate_z(angle_deg: float):
    """Build a TopLoc_Location for a rotation around the Z axis through the origin."""
    import math
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Trsf, gp_Ax1, gp_Pnt, gp_Dir
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(angle_deg))
    return TopLoc_Location(trsf)


def make_assembly_step(path: Path, parts) -> None:
    """Write a STEP file as an XCAF assembly: each part is a *component* whose pose
    lives in a TopLoc_Location, not in baked geometry. This is what real CAD-exported
    STEP files look like, and it exercises the reader's parent_loc → located-shape
    path that flat free-shape STEP files (the `make_step` fixture) don't.

    `parts` is an iterable of (name, master_shape, location) tuples. Names are not
    preserved through the STEP round-trip in this construction (XCAF auto-generates
    component labels of the form "=>[0:1:1:N]"), so tests should match parts by
    signature or centroid rather than by name.
    """
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDataStd import TDataStd_Name
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    # Build a compound containing each master shape positioned via .Located(loc).
    # XCAF will recognise the subshapes as components when AddShape(..., True) is
    # called, producing a real assembly hierarchy in the STEP file.
    builder = BRep_Builder()
    asm = TopoDS_Compound()
    builder.MakeCompound(asm)
    for _name, master_shape, loc in parts:
        builder.Add(asm, master_shape.Located(loc))

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    asm_label = shape_tool.AddShape(asm, True)  # makeAssembly=True
    TDataStd_Name.Set_s(asm_label, TCollection_ExtendedString("RootAssembly"))
    shape_tool.UpdateAssemblies()

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    assert writer.Transfer(doc, STEPControl_AsIs)
    status = writer.Write(str(path))
    assert status == IFSelect_RetDone, f"STEP write failed: {status}"
