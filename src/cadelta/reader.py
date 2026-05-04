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
    # Original RGB surface color stored on the part's XCAF label (or inherited from a
    # parent component / master shape). `None` means the source STEP did not assign a
    # color, in which case the writer falls back to the gray UNCHANGED default.
    color: Optional[tuple[float, float, float]] = None
    # All split sub-parts that came from the same TopoDS_Compound leaf share the same
    # `source_group` integer. The writer uses this so it can re-merge unchanged
    # siblings back into a single compound on output (avoiding 100x label/color
    # bloat for batched components like "147 screws as one entity"). `None` means the
    # part stands alone — either a single-solid leaf or a synthetic test part.
    source_group: Optional[int] = None

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


def _label_color(label) -> Optional[tuple[float, float, float]]:
    """Return the RGB color attached to an XCAF label, trying surface and generic
    color attributes in turn. Returns None when the label has no color attribute set
    (the caller should then inherit from the parent or fall back to a default).

    Uses the static `GetColor_s(label, type, color) -> bool` overload — the instance
    method `XCAFDoc_ColorTool.GetColor` only accepts a `TopoDS_Shape`, not a label.
    """
    from OCP.Quantity import Quantity_Color
    from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorSurf, XCAFDoc_ColorGen
    col = Quantity_Color()
    for ctype in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
        if XCAFDoc_ColorTool.GetColor_s(label, ctype, col):
            return (float(col.Red()), float(col.Green()), float(col.Blue()))
    return None


def _shape_color(color_tool, shape) -> Optional[tuple[float, float, float]]:
    """Return the RGB color attached to a `TopoDS_Shape`, or None if no color is set.

    Needed in addition to `_label_color` because the STEPCAFControl_Reader sometimes
    distributes a colored compound's color across its sub-shapes rather than keeping
    it on the leaf label. After such a round-trip the leaf label has no color but
    each constituent solid does — accessible only through this shape-based overload.
    """
    from OCP.Quantity import Quantity_Color
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_ColorGen
    col = Quantity_Color()
    for ctype in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
        if color_tool.GetColor(shape, ctype, col):
            return (float(col.Red()), float(col.Green()), float(col.Blue()))
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


def _iter_solid_subshapes(shape, world_loc):
    """Walk a leaf shape's geometry tree and yield `(bare_master, world_location)`
    for each individuatable physical sub-part.

    Why this exists: many CAD apps export "batched" components — for example, an
    electronics tool may pack 147 screws into a single XCAF leaf whose shape is a
    `TopoDS_Compound`. Without splitting, the matcher sees ONE Part whose centroid
    is the average of all 147 screws; moving any single screw shifts that average
    and the entire batch lights up as MOVED. Splitting the compound into its
    constituent solids lets the matcher reason at the screw level.

    Strategy:
      - If `shape` is a non-compound (typically a SOLID), yield it as one part.
      - If `shape` is a compound, recurse via `TopoDS_Iterator`, accumulating
        location through each level.
      - From the recursive yield, return only solids (preferred). If no solids
        exist, fall back to shells (sheet bodies). If neither, return the whole
        input as a single part (handles wireframe-only or degenerate cases).

    Locations are composed manually rather than using `TopExp_Explorer`'s
    `.Current()` (whose location semantics across OCCT versions are subtle):
    every level multiplies the running `world_loc` by that subshape's intrinsic
    location. The yielded master has its location stripped to identity so
    callers can compute pose-invariant signatures on it.
    """
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_SOLID, TopAbs_SHELL
    from OCP.TopoDS import TopoDS_Iterator
    from OCP.TopLoc import TopLoc_Location

    def _walk(s, here_loc):
        intrinsic = s.Location()
        composed = here_loc.Multiplied(intrinsic)
        if s.ShapeType() == TopAbs_COMPOUND:
            it = TopoDS_Iterator(s)
            while it.More():
                yield from _walk(it.Value(), composed)
                it.Next()
        else:
            master = s.Located(TopLoc_Location())
            yield master, composed

    candidates = list(_walk(shape, world_loc))
    if not candidates:
        return []

    solids = [(m, l) for m, l in candidates if m.ShapeType() == TopAbs_SOLID]
    if solids:
        return solids
    shells = [(m, l) for m, l in candidates if m.ShapeType() == TopAbs_SHELL]
    if shells:
        return shells
    # Fallback: treat the whole input shape as one part (preserves prior behavior
    # for unusual leaves like wires or compounds that contain only helper geometry).
    intrinsic = shape.Location()
    return [(shape.Located(TopLoc_Location()), world_loc.Multiplied(intrinsic))]


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
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    parts: list[Part] = []
    # Mutable counter so nested walk() can assign a fresh group id whenever a
    # multi-solid compound leaf is encountered.
    group_counter = [0]

    def walk(label, parent_loc: "TopLoc_Location", name_prefix: str,
             parent_color: Optional[tuple[float, float, float]]):
        # XCAF color inheritance: a color set directly on the label wins; otherwise
        # the part inherits whatever color came from the assembly chain above it.
        label_color = _label_color(label) or parent_color

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
                    # Component-level color overrides assembly color for that instance.
                    comp_color = _label_color(comp) or label_color
                    walk(ref, world_loc, child_prefix, comp_color)
        else:
            shape = XCAFDoc_ShapeTool.GetShape_s(label)
            if shape.IsNull():
                return
            # Split compound leaves into individual solids. Some CAD tools (notably
            # ECAD packages) export many discrete components as a single batched
            # shape (e.g. all 147 screws under one "component_screw_147" leaf).
            # Without this split each batch becomes one Part whose centroid is the
            # batch average — moving one screw would re-color the entire batch.
            sub_iter = _iter_solid_subshapes(shape, parent_loc)
            base_name = name_prefix or _label_name(label) or "<unnamed>"
            n_subs = len(sub_iter)

            # Only multi-solid leaves get a group id — single-solid leaves stay
            # `None` so they're treated as standalone parts (no re-grouping needed).
            group_id = None
            if n_subs > 1:
                group_id = group_counter[0]
                group_counter[0] += 1

            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps

            for idx, (sub_master, sub_world_loc) in enumerate(sub_iter):
                if sub_master.IsNull():
                    continue
                # Color resolution for split sub-parts: STEPCAFControl_Reader often
                # distributes a colored compound's color onto its sub-shapes rather
                # than the leaf label. Try the sub-shape directly first, then fall
                # back to whatever color the label / parent supplied.
                sub_color = _shape_color(color_tool, sub_master) or label_color
                # Signature on the bare master so it is pose-invariant.
                sig = compute_signature(sub_master)
                located = sub_master.Located(sub_world_loc)
                # Centroid + inertia in world space so movement and orientation are
                # detected regardless of how the pose is encoded (transform vs baked).
                vprops = GProp_GProps()
                BRepGProp.VolumeProperties_s(located, vprops)
                com = vprops.CentreOfMass()
                centroid = np.array([com.X(), com.Y(), com.Z()], dtype=float)
                orientation, axisymmetric = _orientation_from_inertia(vprops)

                # Append [idx] only when we actually split — single-solid leaves keep
                # their original name unchanged, matching the previous behavior.
                full_name = f"{base_name}[{idx}]" if n_subs > 1 else base_name

                parts.append(
                    Part(
                        name=full_name,
                        shape=located,
                        transform=_location_to_matrix(sub_world_loc),
                        signature=sig,
                        centroid=centroid,
                        orientation=orientation,
                        axisymmetric=axisymmetric,
                        color=sub_color,
                        source_group=group_id,
                    )
                )

    free_labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_labels)
    identity = TopLoc_Location()
    for i in range(1, free_labels.Length() + 1):
        root = free_labels.Value(i)
        root_name = _label_name(root) or ""
        walk(root, identity, root_name, parent_color=None)

    return parts
