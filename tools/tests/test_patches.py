"""Patch layout and colour rules (regressions found while building the UIUC talk)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from talks_build.patches import candidate_layouts


def _font() -> Path:
    exe = shutil.which("fc-match")
    if not exe:
        pytest.skip("fc-match not available")
    out = subprocess.run([exe, "-f", "%{file}", "serif"], capture_output=True, text=True, check=False).stdout
    path = Path(out.strip())
    if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf"}:
        pytest.skip("no TrueType/OpenType serif font found")
    return path


def test_end_alignment_keeps_the_original_right_edge():
    """A shorter replacement followed by more text on its line keeps that text's spacing."""
    font = _font()
    end_x = 900.0
    layouts = candidate_layouts(font, 30.0, "Yellowstone National Park:", 400.0, 500.0, 1920, end_x=end_x)
    first = layouts[0]
    assert first.name.startswith("one line, end-aligned")
    assert abs(first.box.x2 - end_x) <= 1.0


def test_without_following_text_the_first_choice_is_left_aligned():
    font = _font()
    layouts = candidate_layouts(font, 30.0, "Ghabache et al. (2016), Sci. Rep.", 400.0, 500.0, 1920)
    assert layouts[0].name.startswith("one line, left-aligned")


@pytest.mark.export
@pytest.mark.slow
def test_real_build_uses_native_corrections_and_visible_colour(real_export, tmp_path):
    """The native citations are correct; removing YouTube's poster keeps the credit legible."""
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "talks_build",
            "build",
            "--export",
            str(real_export),
            "--talk",
            str(repo / "talks-src" / "singular-bursting-bubbles" / "talk.yaml"),
            "--out-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    report = json.loads((tmp_path / "build" / "singular-bursting-bubbles" / "report.json").read_text())
    assert not report.get("patches"), "native corrections must not depend on raster text patches"
    manifest = json.loads((tmp_path / "site" / "singular-bursting-bubbles" / "manifest.json").read_text())
    newtonian = " ".join(manifest["slides"][3]["text"].split())
    park = " ".join(manifest["slides"][5]["text"].split())
    assert "Ghabache et al. (2016), Sci. Rep." in newtonian
    assert "Walls, Henaux & Bird (2015), Phys. Rev. E 92, 021002" in newtonian
    assert "Gabache" not in newtonian and "Walls et al. (2015), PRE" not in newtonian
    assert "Yellowstone National Park:" in park and "Yellow Stone" not in park
    fixes = [f for f in report["contrast_fixes"] if f["slide"] == 6]
    assert fixes and all(f["recoloured_to"] == "#222222" for f in fixes)
