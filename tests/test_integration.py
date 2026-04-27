"""End-to-end test: build two synthetic STEP files, diff, write output, read it back."""
from __future__ import annotations

from pathlib import Path

import pytest

from cadelta.matcher import Status, diff_parts
from cadelta.reader import load_parts
from cadelta.writer import COLOR_BY_STATUS, write_diff

from .conftest import box_at, make_step


@pytest.fixture
def step_pair(tmp_path: Path):
    """Build v1.step and v2.step with a known set of changes."""
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"

    # v1: A (unchanged), B (will be moved), C (will be removed)
    make_step(v1, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40, 0, 0)),
        ("C", box_at(20, 20, 20, 80, 0, 0)),
    ])
    # v2: A (unchanged), B moved by 0.5mm in +X, D added (new), C gone
    make_step(v2, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40.5, 0, 0)),
        ("D", box_at(12, 12, 12, 120, 0, 0)),
    ])
    return v1, v2


def test_load_parts_round_trip(step_pair):
    v1, v2 = step_pair
    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)

    assert len(parts_v1) == 3
    assert len(parts_v2) == 3
    names_v1 = {p.name for p in parts_v1}
    assert {"A", "B", "C"} <= names_v1, f"got names: {names_v1}"


def test_diff_classifies_each_part_correctly(step_pair):
    v1, v2 = step_pair
    parts_v1 = load_parts(v1)
    parts_v2 = load_parts(v2)
    result = diff_parts(parts_v1, parts_v2)

    by_name = {(e.part_v2 or e.part_v1).name: e for e in result.entries}
    assert by_name["A"].status == Status.UNCHANGED
    assert by_name["B"].status == Status.MOVED
    assert by_name["B"].delta_mm == pytest.approx(0.5, abs=1e-6)
    assert by_name["C"].status == Status.REMOVED
    assert by_name["D"].status == Status.ADDED


def test_write_diff_produces_step_and_glb(step_pair, tmp_path: Path):
    v1, v2 = step_pair
    result = diff_parts(load_parts(v1), load_parts(v2))
    out_step = tmp_path / "diff.step"
    out_glb = tmp_path / "diff.glb"

    write_diff(result, out_step, out_gltf=out_glb)

    assert out_step.exists() and out_step.stat().st_size > 0
    assert out_glb.exists() and out_glb.stat().st_size > 0


def test_diff_step_has_correct_colors_and_offset(step_pair, tmp_path: Path):
    """Read the produced diff.step back and verify each part has the expected color
    and that the removed part sits offset outside the v2 bounding box."""
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorTool, XCAFDoc_ColorSurf
    from OCP.TDF import TDF_LabelSequence
    from OCP.Quantity import Quantity_Color
    from OCP.TDataStd import TDataStd_Name

    v1, v2 = step_pair
    result = diff_parts(load_parts(v1), load_parts(v2))
    out_step = tmp_path / "diff.step"
    write_diff(result, out_step)

    # Re-open the produced STEP via the reader.
    from cadelta.reader import _read_doc
    doc = _read_doc(out_step)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    labels = TDF_LabelSequence()
    shape_tool.GetFreeShapes(labels)
    assert labels.Length() == 4  # A unchanged + B moved + D added + C removed = 4

    found_status: dict[str, tuple[float, float, float]] = {}
    for i in range(1, labels.Length() + 1):
        lab = labels.Value(i)
        name_attr = TDataStd_Name()
        name = ""
        if lab.FindAttribute(TDataStd_Name.GetID_s(), name_attr):
            name = name_attr.Get().ToExtString()
        col = Quantity_Color()
        ok = XCAFDoc_ColorTool.GetColor_s(lab, XCAFDoc_ColorSurf, col)
        assert ok, f"label {name!r} has no surface color"
        rgb = (round(col.Red(), 2), round(col.Green(), 2), round(col.Blue(), 2))
        found_status[name] = rgb

    # Resolve expected colors (rounded to 2 decimals to be tolerant)
    def rgb(s: Status) -> tuple[float, float, float]:
        r, g, b = COLOR_BY_STATUS[s]
        return round(r, 2), round(g, 2), round(b, 2)

    # Find each by status tag in the name.
    a = next(v for k, v in found_status.items() if "A" in k and "UNCHANGED" in k)
    b = next(v for k, v in found_status.items() if "B" in k and "MOVED" in k)
    d = next(v for k, v in found_status.items() if "D" in k and "ADDED" in k)
    c = next(v for k, v in found_status.items() if "C" in k and "REMOVED" in k)

    assert a == rgb(Status.UNCHANGED)
    assert b == rgb(Status.MOVED)
    assert d == rgb(Status.ADDED)
    assert c == rgb(Status.REMOVED)

    # Verify the removed part sits to the right of all the v2 parts
    from cadelta.writer import _shape_xmin_xmax
    parts = load_parts(out_step)
    by_tag = {p.name: p for p in parts}
    removed_part = next(p for k, p in by_tag.items() if "REMOVED" in k)
    v2_parts = [p for k, p in by_tag.items() if "REMOVED" not in k]
    v2_xmax = max(_shape_xmin_xmax(p.shape)[1] for p in v2_parts)
    removed_xmin, _ = _shape_xmin_xmax(removed_part.shape)
    assert removed_xmin > v2_xmax, (
        f"removed part xmin={removed_xmin} should sit past v2_xmax={v2_xmax}"
    )


def test_cli_help_runs():
    """Smoke-test the CLI entrypoint."""
    from click.testing import CliRunner
    from cadelta.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "cadelta" in result.output.lower() or "compare two step" in result.output.lower()


def test_cli_full_run_produces_outputs(step_pair, tmp_path: Path):
    from click.testing import CliRunner
    from cadelta.cli import main

    v1, v2 = step_pair
    out_step = tmp_path / "diff.step"
    out_glb = tmp_path / "diff.glb"
    out_report = tmp_path / "report.json"

    runner = CliRunner()
    result = runner.invoke(main, [
        str(v1), str(v2),
        "-o", str(out_step),
        "--gltf", str(out_glb),
        "--report", str(out_report),
    ])
    assert result.exit_code == 0, f"CLI failed:\n{result.output}\n{result.exception}"
    assert out_step.exists() and out_step.stat().st_size > 0
    assert out_glb.exists() and out_glb.stat().st_size > 0
    assert out_report.exists()

    import json
    report = json.loads(out_report.read_text())
    assert report["counts"]["added"] == 1
    assert report["counts"]["removed"] == 1
    assert report["counts"]["moved"] == 1
    assert report["counts"]["unchanged"] == 1
