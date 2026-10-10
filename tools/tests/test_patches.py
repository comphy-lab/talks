"""Patch layout and colour rules (regressions found while building the UIUC talk)."""

from __future__ import annotations

import json
import shutil
import subprocess
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
def test_real_build_patches_use_the_visible_colour(real_export, tmp_path):
    """Slide 6's attribution is recoloured for legibility before it is patched.

    The patch must draw in the recoloured ink (#222222), not the original
    white that sat over the movie, and keep the gap before 'Video from'.
    """
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [
            "python",
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
    by_find = {p["find"]: p for p in report["patches"]}
    yellow = by_find["Yellow Stone National Park:"]
    assert yellow["colour"] == "#222222"
    assert yellow["layout"].startswith("one line, end-aligned")
    walls = by_find["Walls et al. (2015), PRE"]
    assert walls["lines"] == ["Walls, Henaux & Bird (2015),", "Phys. Rev. E 92, 021002"]
