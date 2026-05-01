from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .signature import Signature, compute_signature


@dataclass
class Part:
    name: str
    shape: object  # OCCT TopoDS_Shape (with world transform applied via Located)
    transform: np.ndarray  # 4x4 world-space assembly transform (may be identity when geometry is baked)
    signature: Signature
    centroid: Optional[np.ndarray] = None  # world-space center of mass (3-vector)
    # World-space orientation derived from the principal axes of inertia of the located
    # shape. Representation-invariant (unlike `transform`, which only reflects the XCAF
    # transform slot). `None` means the orientation is undefined — typical for synthetic
    # test parts that bypass the reader.
    orientation: Optional[np.ndarray] = None  # 3x3 right-handed rotation matrix
    # True when at least two principal moments of inertia are nearly equal: the inertia
    # frame is then rotationally ambiguous (cylinders, cubes, spheres, regular prisms).
    # Rotation cannot be reliably detected for such parts, and visually it usually
    # cannot be perceived either.
    axisymmetric: bool = False

    def __post_init__(self):
        # If no explicit centroid is provided (e.g. synthetic test parts), derive it
        # from the transform's translation column. The reader always supplies a real one.
        if self.centroid is None:
            self.centroid = np.asarray(self.transform[:3, 3], dtype=float).copy()


def _label_name(label) -> Optional[str]:
    from OCP.TDataStd import TDataStd_Name
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        try:
            return attr.Get().ToExtString()
        except Exception:
            return None
    return None


def _location_to_matrix(loc) -> np.ndarray:
    """Convert TopLoc_Location to a numpy 4x4 matrix."""
    trsf = loc.Transformation()
    mat = np.eye(4)
    for i in range(3):
        for j in range(4):
            mat[i, j] = trsf.Value(i + 1, j + 1)
    return mat


# Two principal moments are considered equal (axisymmetric) when their relative
# difference is below this threshold. 1% catches cylinders, cubes, regular prisms,
# and most fasteners while staying loose enough to avoid mis-flagging genuinely
# asymmetric parts whose moments happen to be numerically close.
_AXISYM_REL_TOL = 0.01


def _orientation_from_inertia(vprops) -> tuple[Optional[np.ndarray], bool]:
    """Build a 3x3 world-space rotation matrix from the principal axes of inertia.

    Returns (orientation, axisymmetric):
      orientation: right-handed 3x3 rotation matrix whose columns are the principal
                   inertia axes of the located shape, or None if the shape is degenerate
                   (zero volume, point, etc).
      axisymmetric: True when at least two principal moments coincide, in which case
                    the corresponding axes can rotate freely within their plane and
                    rotation cannot be reliably detected.
    """
    pp = vprops.PrincipalProperties()
    i1, i2, i3 = pp.Moments()
    moments_sorted = sorted([float(i1), float(i2), float(i3)])
    m_max = max(abs(moments_sorted[2]), 1e-12)
    axisymmetric = (
        abs(moments_sorted[1] - moments_sorted[0]) / m_max < _AXISYM_REL_TOL
        or abs(moments_sorted[2] - moments_sorted[1]) / m_max < _AXISYM_REL_TOL
    )

    ax1 = pp.FirstAxisOfInertia()
    ax2 = pp.SecondAxisOfInertia()
    ax3 = pp.ThirdAxisOfInertia()
    R = np.array([
        [ax1.X(), ax2.X(), ax3.X()],
        [ax1.Y(), ax2.Y(), ax3.Y()],
        [ax1.Z(), ax2.Z(), ax3.Z()],
    ], dtype=float)

    # Force right-handedness so subsequent angle math is well-defined.
    if np.linalg.det(R) < 0:
        R[:, 2] *= -1.0
    return R, axisymmetric


def _read_doc(step_path: Path):
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.IFSelect import IFSelect_RetDone

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    status = reader.ReadFile(str(step_path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"Failed to read STEP file (status={status}): {step_path}")
    if not reader.Transfer(doc):
        raise RuntimeError(f"Failed to transfer STEP into XCAFDoc: {step_path}")
    return doc


def load_parts(step_path: str | Path) -> list[Part]:
    """Load a STEP file and return a flat list of leaf parts with world-space transforms."""
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_LabelSequence, TDF_Label
    from OCP.TopLoc import TopLoc_Location

    path = Path(step_path)
    doc = _read_doc(path)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    parts: list[Part] = []

    def walk(label, parent_loc: "TopLoc_Location", name_prefix: str):
        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            comps = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, comps)
            for i in range(1, comps.Length() + 1):
                comp = comps.Value(i)
                comp_loc = XCAFDoc_ShapeTool.GetLocation_s(comp)
                ref = TDF_Label()
                if XCAFDoc_ShapeTool.GetReferredShape_s(comp, ref):
                    world_loc = parent_loc.Multiplied(comp_loc)
                    comp_name = _label_name(comp) or _label_name(ref) or ""
                    child_prefix = f"{name_prefix}/{comp_name}" if name_prefix else comp_name
                    walk(ref, world_loc, child_prefix)
        else:
            shape = XCAFDoc_ShapeTool.GetShape_s(label)
            if shape.IsNull():
                return
            located = shape.Located(parent_loc)
            # The path-style name_prefix already identifies this leaf via the chain
            # of component names that led here. Don't re-append the master shape's
            # name (would produce "A/A" for free-leaf STEP exports).
            full_name = name_prefix or _label_name(label) or "<unnamed>"
            # Compute the signature on the master shape (no assembly transform applied)
            # so AABB dims stay stable when the same part appears in different poses.
            sig = compute_signature(shape)
            # Compute world-space centroid from the located shape so movement is detected
            # regardless of whether the position is encoded as an XCAF transform or baked
            # into the geometry coordinates.
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            vprops = GProp_GProps()
            BRepGProp.VolumeProperties_s(located, vprops)
            com = vprops.CentreOfMass()
            centroid = np.array([com.X(), com.Y(), com.Z()], dtype=float)

            # Derive a world-space orientation from the principal axes of inertia.
            # Unlike the XCAF transform, this is intrinsic to the located geometry,
            # so it gives the same answer whether v1 stores rotation as a transform
            # and v2 bakes it into geometry (or vice versa).
            orientation, axisymmetric = _orientation_from_inertia(vprops)

            parts.append(
                Part(
                    name=full_name,
                    shape=located,
                    transform=_location_to_matrix(parent_loc),
                    signature=sig,
                    centroid=centroid,
                    orientation=orientation,
                    axisymmetric=axisymmetric,
                )
            )

    free_labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_labels)
    identity = TopLoc_Location()
    for i in range(1, free_labels.Length() + 1):
        root = free_labels.Value(i)
        root_name = _label_name(root) or ""
        walk(root, identity, root_name)

    return parts
