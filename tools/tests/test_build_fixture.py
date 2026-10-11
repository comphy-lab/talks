"""End-to-end build of the synthetic export: outputs, spans, overlays, determinism."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import synthetic_export as sx
from PIL import Image

from talks_build.build import OVERLAY_BLEED, build
from talks_build.geometry import Rect
from talks_build.manifest import placement_key, validate
from talks_build.text import font_file

pytestmark = pytest.mark.slow

TEMPLATE = '<title>{{TITLE}}</title><meta name=description content="{{DESCRIPTION}}"><main data-slug="{{SLUG}}"></main>'


def _build(export: Path, root: Path, *, patches: bool = False, **talk_options) -> tuple[dict, dict]:
    talk = sx.write_talk(root, with_patches=patches, **talk_options)
    (root / "site" / "player").mkdir(parents=True, exist_ok=True)
    (root / "site" / "player" / "talk.template.html").write_text(TEMPLATE)
    report = build(export, talk, None, pdf=None, jobs=2, log=lambda *_: None)
    manifest = json.loads((root / "site" / "fixture-talk" / "manifest.json").read_text())
    return report, manifest


@pytest.fixture(scope="module")
def built(synthetic_export, tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    return root, *_build(synthetic_export, root)


def test_outputs_and_counts(built):
    root, report, manifest = built
    assert report["states"] == 7 and report["slides"] == 4
    assert validate(manifest, root / "build" / "fixture-talk" / "media") == []
    assert list(manifest["videos"]) == ["v01"]  # two copies, one stream: one encode
    assert list(manifest["youtube"]) == ["y01"]
    page = (root / "site" / "fixture-talk" / "index.html").read_text()
    assert "Fixture talk" in page and "fixture-talk" in page and "{{" not in page
    index = json.loads((root / "site" / "talks.json").read_text())
    assert index[0]["slug"] == "fixture-talk" and index[0]["url"] == "fixture-talk/"
    names = set((root / "build" / "fixture-talk" / "media").iterdir())
    assert all(len(p.stem) == 16 for p in names)


def test_span_overlay_and_transitions(built):
    _root, _report, m = built
    s1, s2, s3, s4 = m["slides"]
    assert [len(s["states"]) for s in m["slides"]] == [1, 3, 1, 2]
    # click 1 shows the poster; the movie plays from click 2 and continues on slide 3
    assert s2["states"][0]["media"] == []
    k2 = [placement_key(x) for x in s2["states"][2]["media"]]
    k3 = [placement_key(x) for x in s3["states"][0]["media"]]
    assert k2 == k3 == ["video:v01:200,300,640,360"]
    # the label above the movie is in an overlay; no overlay before the movie plays
    assert s2["states"][0]["overlay"] is None
    assert s2["states"][1]["overlay"] and s2["states"][2]["overlay"]
    assert s3["states"][0]["overlay"] is None
    assert s2["states"][2]["transition"] == "dissolve"


@pytest.mark.parametrize("scale", [1, 2])
def test_overlay_is_clipped_to_the_movie_plus_bleed(built, scale):
    root, _report, m = built
    media = root / "build" / "fixture-talk" / "media"
    state = m["slides"][1]["states"][1]
    suffix = "2x" if scale == 2 else ""
    ov = np.asarray(Image.open(media / state[f"overlay{suffix}"]).convert("RGBA")).astype(int)
    still = np.asarray(Image.open(media / state[f"image{suffix}"]).convert("RGB")).astype(int)
    assert ov.shape[:2] == (1080 * scale, 1920 * scale)
    alpha = ov[..., 3]
    # label at x 700-1000, y 600-660; movie at x 200-840, y 300-660
    inside = np.zeros(alpha.shape, dtype=bool)
    inside[300 * scale : 660 * scale, 200 * scale : 840 * scale] = True
    ys, xs = np.nonzero((alpha > 8) & inside)
    assert xs.min() >= 700 * scale and xs.max() < 840 * scale
    assert ys.min() >= 600 * scale and ys.max() < 660 * scale
    # Outside the movie: only the bleed band next to the label, opaque, repeating the still.
    ys, xs = np.nonzero((alpha > 0) & ~inside)
    assert len(xs) > 0, "no bleed band"
    assert xs.min() >= (700 - OVERLAY_BLEED) * scale and xs.max() < (840 + OVERLAY_BLEED) * scale
    assert ys.min() >= (600 - OVERLAY_BLEED) * scale and ys.max() < (660 + OVERLAY_BLEED) * scale
    assert (alpha[ys, xs] == 255).all()
    assert np.abs(ov[ys, xs, :3] - still[ys, xs]).max(axis=1).mean() < 4


def _embed_area(root: Path, image: str, rect, scale=1) -> np.ndarray:
    x, y, w, h = (v * scale for v in rect)
    with Image.open(root / "build" / "fixture-talk" / "media" / image) as im:
        return np.asarray(im.convert("RGB"))[y : y + h, x : x + w].astype(int)


@pytest.mark.parametrize("scale", [1, 2])
def test_youtube_poster_before_the_click(built, scale):
    """Keynote shows the movie's poster before the click that starts it; so does the build (default)."""
    root, report, m = built
    st = m["slides"][3]["states"]
    rect = st[1]["media"][0]["rect"]
    assert st[0]["media"] == []
    field = "image2x" if scale == 2 else "image"
    before = _embed_area(root, st[0][field], rect, scale)
    playing = _embed_area(root, st[1][field], rect, scale)
    # the fixture poster is a flat (127, 115, 102) fill; the embed sits on plain white
    assert np.abs(before.mean(axis=(0, 1)) - (127, 115, 102)).max() < 6
    assert playing.min() > 245
    assert report["youtube_before_start"] == [{"slide": 4, "state": 1, "shown": "poster"}]


def test_youtube_before_start_embed_and_titles_override(synthetic_export, tmp_path):
    report, m = _build(
        synthetic_export, tmp_path, before_start="embed", titles={sx.SLIDES["S2"]: "A title set by hand"}
    )
    st = m["slides"][3]["states"]
    keys = [[placement_key(x) for x in s["media"]] for s in st]
    assert keys[0] == keys[1] and keys[0][0].startswith("youtube:y01:"), keys  # one embed, kept across the click
    assert _embed_area(tmp_path, st[0]["image"], st[0]["media"][0]["rect"]).min() > 245
    assert "youtube_before_start" not in report
    assert m["slides"][1]["title"] == "A title set by hand"


def test_youtube_rect_clear_of_title_and_credit(built):
    _root, report, m = built
    st = m["slides"][3]["states"]
    assert st[0]["media"] == []
    (item,) = st[1]["media"]
    assert item["kind"] == "youtube" and item["ref"] == "y01"
    x, y, w, h = item["rect"]
    assert w * 9 == h * 16
    r = Rect(x, y, w, h)
    assert not r.intersects(Rect(300, 40, 900, 90))  # title object
    assert not r.intersects(Rect(300, 1030, 600, 40))  # credit object
    assert report["contrast_fixes"][0]["recoloured_to"] == "#222222"
    assert st[1]["overlay"] is None


def test_build_is_deterministic(synthetic_export, built, tmp_path):
    root1, _r, m1 = built
    _r2, m2 = _build(synthetic_export, tmp_path)
    assert json.dumps(m1, sort_keys=True) == json.dumps(m2, sort_keys=True)
    a = {p.name for p in (root1 / "build" / "fixture-talk" / "media").iterdir()}
    b = {p.name for p in (tmp_path / "build" / "fixture-talk" / "media").iterdir()}
    assert a == b
    assert (root1 / "site" / "fixture-talk" / "manifest.json").read_bytes() == (
        tmp_path / "site" / "fixture-talk" / "manifest.json"
    ).read_bytes()


@pytest.mark.skipif(font_file("Helvetica") is None, reason="no local Helvetica font for the patch renderer")
def test_patch_replaces_text_in_image_and_text(synthetic_export, tmp_path):
    report, m = _build(synthetic_export, tmp_path, patches=True)
    flat, p = report["patches"]
    assert p["lines"] == ["Smith, Jones & Brown (2020), Phys. Rev. Fluids 5, 012345"]
    assert p["depth"] == "texture"
    # a single-event slide: the patch works on the flattened texture
    assert flat["lines"] == ["Fixture title 1"]
    assert flat["colour"] == "#000000"  # ink, not the opaque white background
    assert flat["fit_iou"] > 0.6 and abs(flat["size_px"] - 72) < 1.5
    assert m["slides"][0]["title"] == "Fixture title 1"
    text = m["slides"][1]["text"]
    assert "Smith, Jones & Brown (2020), Phys. Rev. Fluids 5, 012345" in text
    assert "Smith et al." not in text
    # the citation is built in on click 2: patched images differ only there
    _r0, m0 = _build(synthetic_export, tmp_path / "plain")
    s, s0 = m["slides"][1]["states"], m0["slides"][1]["states"]
    assert s[0]["image"] == s0[0]["image"]
    assert s[2]["image"] != s0[2]["image"]
    assert m["slides"][0]["states"][0]["image"] != m0["slides"][0]["states"][0]["image"]
    shutil.rmtree(tmp_path / "plain")


def test_bleed_band_repeats_the_still_next_to_overlay_ink():
    from talks_build.build import bleed_overlay

    still = Image.new("RGBA", (60, 40), (10, 200, 30, 255))
    overlay = Image.new("RGBA", (60, 40), (0, 0, 0, 0))
    ink = Image.new("RGBA", (6, 6), (255, 255, 255, 255))
    overlay.paste(ink, (20, 10))  # touches the movie's left edge at x = 20
    movie = Rect(20, 5, 30, 30)  # x 20-50, y 5-35
    other = Rect(0, 0, 14, 40)  # another media box, never covered
    out = np.asarray(bleed_overlay(overlay, still, [(20, 5, 50, 35)], [movie, other], 4))
    a = out[..., 3]
    assert (a[10:16, 16:20] == 255).all() and (out[10:16, 16:20, :3] == (10, 200, 30)).all()
    assert (a[:, :14] == 0).all()  # the other box stays clear
    assert (a[10:16, 20:26] == 255).all() and (out[12, 22, :3] == 255).all()  # the ink itself unchanged
    assert (a[30:40, :] == 0).all() and (a[:, 54:] == 0).all()  # no ink near those edges: no band
    assert a[:, :20].sum() // 255 == 4 * (6 + 2 * 4)  # band only beside the ink, 4 px deep
    unchanged = bleed_overlay(overlay, still, [(20, 5, 50, 35)], [movie], 0)
    assert unchanged is overlay
