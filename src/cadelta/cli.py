from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .matcher import DEFAULT_TOL_DEG, DEFAULT_TOL_MM, Status, diff_parts
from .reader import load_parts
from .writer import write_diff


@click.command()
@click.argument("v1", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("v2", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="Output STEP file path.")
@click.option("--gltf", "gltf", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Optional glTF/GLB output path for easy 3D viewing in a browser.")
@click.option("--tol-mm", type=float, default=DEFAULT_TOL_MM, show_default=True,
              help="Translation tolerance in mm. Below this, a part is considered unchanged.")
@click.option("--tol-deg", type=float, default=DEFAULT_TOL_DEG, show_default=True,
              help="Rotation tolerance in degrees. Below this, a part is considered unchanged.")
@click.option("--report", "report", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Optional JSON report listing every part with its status and deltas.")
def main(v1: Path, v2: Path, output: Path, gltf: Path | None,
         tol_mm: float, tol_deg: float, report: Path | None) -> None:
    """Compare two STEP files V1 and V2, write a colored diff to OUTPUT.

    Coloring of the resulting model:

      green  = added   (in V2 only)
      yellow = moved   (in both, pose differs beyond tolerance)
      red    = removed (in V1 only; rendered at its original V1 position)
      gray   = unchanged
    """
    click.echo(f"Reading {v1} ...", err=True)
    parts_v1 = load_parts(v1)
    click.echo(f"  {len(parts_v1)} part(s)", err=True)

    click.echo(f"Reading {v2} ...", err=True)
    parts_v2 = load_parts(v2)
    click.echo(f"  {len(parts_v2)} part(s)", err=True)

    click.echo(f"Diffing (tol_mm={tol_mm}, tol_deg={tol_deg}) ...", err=True)
    result = diff_parts(parts_v1, parts_v2, tol_mm=tol_mm, tol_deg=tol_deg)

    counts = {s.value: len(result.by_status(s)) for s in Status}
    click.echo(
        f"  added={counts['added']}  removed={counts['removed']}  "
        f"moved={counts['moved']}  unchanged={counts['unchanged']}",
        err=True,
    )

    click.echo(f"Writing {output} ...", err=True)
    write_diff(result, output, out_gltf=gltf)
    if gltf is not None:
        click.echo(f"  also wrote {gltf}", err=True)

    if report is not None:
        report_data = {
            "v1": str(v1),
            "v2": str(v2),
            "tol_mm": tol_mm,
            "tol_deg": tol_deg,
            "counts": counts,
            "entries": [
                {
                    "status": e.status.value,
                    "name": (e.part_v2.name if e.part_v2 else (e.part_v1.name if e.part_v1 else "")),
                    "delta_mm": e.delta_mm,
                    "delta_deg": e.delta_deg,
                }
                for e in result.entries
            ],
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(report_data, indent=2))
        click.echo(f"  wrote report {report}", err=True)


if __name__ == "__main__":
    main()
