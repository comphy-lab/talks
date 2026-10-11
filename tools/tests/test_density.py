"""True vector density, shared logical geometry and scaled patch regressions."""

from __future__ import annotations

import io
import json
import shutil
import subprocess

import numpy as np
import pytest
import synthetic_export as sx
from PIL import Image

from talks_build.build import build
from talks_build.raster import PageCache
from talks_build.text import font_file

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def density_build(synthetic_export, tmp_path_factory):
    root = tmp_path_factory.mktemp("density-build")
    talk = sx.write_talk(root, with_patches=font_file("Helvetica") is not None)
    report = build(synthetic_export, talk, root, jobs=2, log=lambda *_: None)
    manifest = json.loads((root / "site" / "fixture-talk" / "manifest.json").read_text())
    return root / "build" / "fixture-talk" / "media", report, manifest


def test_page_cache_uses_the_vector_pdf_at_both_densities(synthetic_export, tmp_path):
    pdf = synthetic_export / "assets" / sx.SLIDES["S1"] / "assets" / f"{sx.SLIDES['S1']}.pdf"
    cache = PageCache(tmp_path / "cache")
    one = cache.page(pdf, 0)
    two = cache.page(pdf, 0, 2)
    assert one.size == (1920, 1080) and two.size == (3840, 2160)
    assert cache.page(pdf, 0) is one and cache.page(pdf, 0, 2) is two
    assert cache._key(pdf, 0).endswith("-72") and cache._key(pdf, 0, 2).endswith("-144")

    # An independent 144 dpi render proves the extra pixels come from PDF
    # vectors. Enlarging the 72 dpi raster has visibly different glyph edges.
    stem = tmp_path / "reference"
    subprocess.run(
        ["pdftocairo", "-png", "-transp", "-r", "144", "-f", "1", "-l", "1", "-singlefile", str(pdf), str(stem)],
        check=True,
        capture_output=True,
    )
    with Image.open(stem.with_suffix(".png")) as im:
        assert np.array_equal(np.asarray(two), np.asarray(im.convert("RGBA")))
    enlarged = one.resize(two.size, Image.Resampling.LANCZOS)
    a = np.asarray(two.crop((590, 260, 1700, 440)), dtype=np.int16)
    b = np.asarray(enlarged.crop((590, 260, 1700, 440)), dtype=np.int16)
    assert np.abs(a - b).mean() > 0.2


def _composite(media, state, scale):
    suffix = "2x" if scale == 2 else ""
    with Image.open(media / state[f"image{suffix}"]) as im:
        canvas = im.convert("RGBA")
    overlay = state[f"overlay{suffix}"]
    if overlay:
        with Image.open(media / overlay) as im:
            canvas.alpha_composite(im.convert("RGBA"))
    return canvas


def test_every_state_has_consistent_logical_geometry(density_build):
    media, report, manifest = density_build
    assert manifest["schema"] == 2 and report["render_scales"] == [1, 2]
    for slide in manifest["slides"]:
        for index, state in enumerate(slide["states"], 1):
            one = _composite(media, state, 1)
            two = _composite(media, state, 2)
            assert one.size == (1920, 1080) and two.size == (3840, 2160)
            a = np.asarray(one.convert("RGB"), dtype=np.int16)
            b = np.asarray(two.resize(one.size, Image.Resampling.LANCZOS).convert("RGB"), dtype=np.int16)
            diff = np.abs(a - b).max(axis=2)
            assert diff.mean() < 1.5, (slide["n"], index, float(diff.mean()))
            assert (diff > 32).mean() < 0.01, (slide["n"], index, float((diff > 32).mean()))


def test_patch_ocr_at_two_x(density_build):
    media, report, manifest = density_build
    if not report.get("patches"):
        pytest.skip("no local Helvetica font for patch rendering")
    if not shutil.which("tesseract"):
        pytest.skip("no Tesseract for OCR")
    state = manifest["slides"][1]["states"][-1]
    with Image.open(media / state["image2x"]) as im:
        crop = im.crop((360, 1360, 3840, 1600)).convert("RGB")
    data = io.BytesIO()
    crop.save(data, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "6"],
        input=data.getvalue(),
        capture_output=True,
        check=True,
    )
    text = result.stdout.decode("utf-8")
    for word in ("Smith", "Jones", "Brown", "2020", "012345"):
        assert word in text, text
