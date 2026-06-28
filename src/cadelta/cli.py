from __future__ import annotations

import json
from pathlib import Path

import click

from .matcher import DEFAULT_TOL_DEG, DEFAULT_TOL_MM, Status, diff_parts
from .reader import load_parts, load_parts_with_doc
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

      cyan        = added (in V2 only)
      hot pink    = moved — rendered at the NEW (V2) position
      soft pink   = moved — ghost rendered at the OLD (V1) position
      bright red  = removed (in V1 only; rendered at its V1 position)
      original    = unchanged (keeps the V2 color; gray fallback)
    """
    click.echo(f"Reading {v1} ...", err=True)
    try:
        parts_v1 = load_parts(v1)
    except RuntimeError as exc:
        # OCCT's STEPCAFControl_Reader raises RuntimeError for malformed or
        # unsupported STEP files; surface a clean message instead of a Python
        # traceback.
        raise click.ClickException(f"Could not read {v1}: {exc}") from exc
    click.echo(f"  {len(parts_v1)} part(s)", err=True)

    click.echo(f"Reading {v2} ...", err=True)
    # Load v2 WITH its XCAFDoc — the writer will mutate the doc in place
    # (recoloring leaves to indicate diff status) so the output preserves v2's
    # native master/instance graph. Rebuilding from scratch via baked geometry
    # bloats the file ~4× on real assemblies that share masters across instances.
    try:
        parts_v2, doc_v2 = load_parts_with_doc(v2)
    except RuntimeError as exc:
        raise click.ClickException(f"Could not read {v2}: {exc}") from exc
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
    write_diff(result, output, doc_v2=doc_v2, out_gltf=gltf)
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
