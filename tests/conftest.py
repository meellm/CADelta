"""Shared test fixtures: synthetic STEP file builders backed by OCP."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple


def make_step(path: Path, parts: Iterable[Tuple[str, object]]) -> None:
    """Write a STEP file containing the given (name, shape) pairs as free shapes."""
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.TDataStd import TDataStd_Name
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    for name, shape in parts:
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))

    writer = STEPCAFControl_Writer()
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
