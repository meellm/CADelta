from __future__ import annotations

from dataclasses import dataclass

SIG_DECIMALS = 4


@dataclass(frozen=True)
class Signature:
    volume: float
    area: float
    bbox_dx: float
    bbox_dy: float
    bbox_dz: float
    num_faces: int

    @classmethod
    def from_values(
        cls,
        volume: float,
        area: float,
        bbox_dx: float,
        bbox_dy: float,
        bbox_dz: float,
        num_faces: int,
    ) -> "Signature":
        r = lambda v: round(float(v), SIG_DECIMALS)
        return cls(r(volume), r(area), r(bbox_dx), r(bbox_dy), r(bbox_dz), int(num_faces))


def compute_signature(shape) -> Signature:
    """Compute a transform-stable geometry signature for an OCCT TopoDS_Shape.

    The bbox dims are sorted so the signature is invariant under axis
    permutations (e.g. a re-export that swaps X and Y axes).
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE

    vprops = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vprops)
    volume = abs(vprops.Mass())

    sprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sprops)
    area = sprops.Mass()

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    if bb.IsVoid():
        dx = dy = dz = 0.0
    else:
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        dims = sorted([xmax - xmin, ymax - ymin, zmax - zmin])
        dx, dy, dz = dims[0], dims[1], dims[2]

    n_faces = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        n_faces += 1
        exp.Next()

    return Signature.from_values(volume, area, dx, dy, dz, n_faces)
