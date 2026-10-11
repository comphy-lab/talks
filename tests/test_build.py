"""Build invariants the browser relies on, checked on the files (no browser).

- Spans: one source at (almost) one place on consecutive states must share a
  placement key, or the player would restart it.
- Videos: SPEC section 4 encodes, content-addressed names, one file per source.
- Overlays: transparent outside the movies they cover, except for a bleed
  band that repeats the still.
- Slide 4 corrections from talk.yaml are in the slide text and, if Tesseract
  is installed, legible in the image.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from talk_helpers import Talk, erode, placement_key, visible_rect

REPO = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")
SPAN_GROUPS = [(21, 24), (34, 36), (37, 38), (106, 110)]
OVERLAY_BLEED = 16  # canvas px; tools/talks_build/build.py


def referenced_files(talk: Talk) -> set[str]:
    m = talk.manifest
    names = {v["src"] for v in m["videos"].values()} | {v["poster"] for v in m["videos"].values()}
    for s in m["slides"]:
        for st in s["states"]:
            names.add(st["image"])
            if st["overlay"]:
                names.add(st["overlay"])
    return names


def test_media_files_are_content_addressed(talk: Talk):
    names = referenced_files(talk)
    for name in sorted(names):
        assert NAME_RE.match(name), name
        path = talk.media_path(name)
        assert path.is_file(), f"missing {name}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        assert name.startswith(digest), f"{name}: content hash is {digest}"


def test_spans_share_keys_on_consecutive_states(talk: Talk):
    near_misses = []
    for a, b in zip(talk.steps, talk.steps[1:], strict=False):
        for ma in a.state["media"]:
            for mb in b.state["media"]:
                if ma["kind"] != mb["kind"] or ma["ref"] != mb["ref"]:
                    continue
                close = all(abs(p - q) <= 3 for p, q in zip(ma["rect"], mb["rect"], strict=True))
                if close and placement_key(ma) != placement_key(mb):
                    near_misses.append(f"{a.label}->{b.label}: {placement_key(ma)} vs {placement_key(mb)}")
    assert not near_misses, "same source, almost the same place, different keys (would restart):\n" + "\n".join(
        near_misses
    )


def test_cross_slide_span_groups(talk: Talk):
    """The groups of consecutive slides that keep one video playing."""
    groups = []
    for key in sorted({k for s in talk.steps for k in s.video_keys}):
        run: list = []
        for step in talk.steps + [None]:
            if step is not None and key in step.keys:
                run.append(step)
                continue
            if run and run[0].n != run[-1].n:
                groups.append((run[0].n, run[-1].n))
            run = []
    assert sorted(groups) == SPAN_GROUPS, groups


def test_one_entry_per_source_and_fifteen_movies(talk: Talk):
    m = talk.manifest
    srcs = [v["src"] for v in m["videos"].values()]
    assert len(srcs) == len(set(srcs)), "two video entries share a file"
    assert len(m["videos"]) + len(m.get("youtube", {})) == 15, "the UIUC deck has 15 distinct movies"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffprobe")
def test_video_encodes_follow_spec(talk: Talk):
    max_scale = 2.0
    largest: dict[str, tuple[int, int]] = {}
    for s in talk.steps:
        for mm in s.state["media"]:
            if mm["kind"] == "video":
                w, h = largest.get(mm["ref"], (0, 0))
                largest[mm["ref"]] = (max(w, round(mm["rect"][2])), max(h, round(mm["rect"][3])))
    for ref, info in talk.manifest["videos"].items():
        path = talk.media_path(info["src"])
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        probe = json.loads(out.stdout)
        streams = probe["streams"]
        assert [s["codec_type"] for s in streams] == ["video"], f"{ref}: streams {[s['codec_type'] for s in streams]}"
        v = streams[0]
        assert (v["codec_name"], v["profile"], v["pix_fmt"]) == ("h264", "High", "yuv420p"), (ref, v)
        assert v["width"] % 2 == 0 and v["height"] % 2 == 0, (ref, v["width"], v["height"])
        assert (v["width"], v["height"]) == (info["width"], info["height"])
        bw, bh = largest[ref]
        assert v["width"] <= max_scale * bw + 2 and v["height"] <= max_scale * bh + 2, (ref, v["width"], bw)
        assert abs(float(probe["format"]["duration"]) - info["duration"]) < 0.1, (ref, probe["format"]["duration"])
        head = path.read_bytes()[: 1 << 16]
        assert head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat")), (
            f"{ref}: not faststart"
        )


def _poster_fit(talk: Talk, still: np.ndarray, uncovered: np.ndarray, poster, rect, region) -> float:
    """Mean colour difference between the still and the poster scaled into `rect`, inside `region`."""
    from PIL import Image

    x, y, w, h = rect
    x0, y0, x1, y1 = region
    scaled = np.asarray(poster.resize((w, h), Image.Resampling.LANCZOS), dtype=np.int16)
    diff = np.abs(still[y0:y1, x0:x1] - scaled[y0 - y : y1 - y, x0 - x : x1 - x]).max(axis=2)
    return float(diff[uncovered[y0:y1, x0:x1]].mean())


def test_video_boxes_line_up_with_keynote_posters(talk: Talk):
    """A video covers the poster frame Keynote baked into the still: same place, same size.

    Moving any one edge of the manifest rectangle by a pixel must not fit the
    baked-in poster clearly better; otherwise the video would sit a pixel off,
    stretched against the still, or leave a static row of poster beside it.
    """
    from PIL import Image

    checked, report = set(), []
    for step in talk.steps:
        for m in step.state["media"]:
            if m["kind"] != "video":
                continue
            rect = tuple(round(v) for v in m["rect"])
            tag = (m["ref"], rect, step.state["image"])
            if tag in checked:
                continue
            checked.add(tag)
            still = talk.rgba(step.state["image"])[..., :3].astype(np.int16)
            alpha = talk.rgba(step.state["overlay"])[..., 3] if step.state["overlay"] else np.zeros(still.shape[:2])
            uncovered = alpha == 0
            with Image.open(talk.media_path(talk.manifest["videos"][m["ref"]]["poster"])) as im:
                poster = im.convert("RGB")
            x, y, w, h = rect
            # Compare inside the box, away from its edges, on the visible canvas.
            region = (max(0, x) + 4, max(0, y) + 4, min(1920, x + w) - 4, min(1080, y + h) - 4)
            here = _poster_fit(talk, still, uncovered, poster, rect, region)
            moves = [
                (x - 1, y, w + 1, h), (x + 1, y, w - 1, h), (x, y, w - 1, h), (x, y, w + 1, h),
                (x, y - 1, w, h + 1), (x, y + 1, w, h - 1), (x, y, w, h - 1), (x, y, w, h + 1),
            ]  # fmt: skip
            best = min(_poster_fit(talk, still, uncovered, poster, r, region) for r in moves)
            report.append(f"{step.label} {m['ref']} {list(rect)}: {here:.2f} (best moved edge {best:.2f})")
            assert here <= 1.1 * best + 0.3, (
                f"{step.label} {m['ref']} {list(rect)}: an edge is a pixel off ({here:.2f} vs {best:.2f})"
            )
    print("\n" + "\n".join(report))


def test_overlays_only_above_movies(talk: Talk):
    """An overlay draws over its movies, plus a bleed band that repeats the still.

    Outside every media box the overlay may only continue the still for
    OVERLAY_BLEED canvas pixels (so a scaled stage shows no seam), opaque and
    equal to the still up to WebP noise.
    """
    for step in talk.steps:
        if not step.state["overlay"]:
            continue
        overlay = talk.rgba(step.state["overlay"])
        alpha = overlay[..., 3]
        inside = np.zeros_like(alpha, dtype=bool)
        for mm in step.state["media"]:
            x0, y0, x1, y1 = visible_rect(mm["rect"])
            inside[y0:y1, x0:x1] = True
        near = ~erode(~inside, OVERLAY_BLEED)
        stray = int(((alpha > 0) & ~near).sum())
        assert stray == 0, f"{step.label}: {stray} overlay pixels further than {OVERLAY_BLEED} px from the movies"
        assert int(((alpha > 0) & inside).sum()) > 0, f"{step.label}: empty overlay"
        band = (alpha > 0) & ~inside
        if band.any():
            assert int(alpha[band].min()) == 255, f"{step.label}: translucent bleed band"
            still = talk.rgba(step.state["image"])[..., :3].astype(int)
            diff = np.abs(overlay[..., :3].astype(int) - still).max(axis=2)[band]
            assert diff.mean() <= 4 and np.percentile(diff, 99) <= 30, (
                f"{step.label}: bleed band differs from the still (mean {diff.mean():.1f}, "
                f"99th percentile {np.percentile(diff, 99):.0f})"
            )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def patches(talk: Talk) -> list[dict]:
    path = REPO / "talks-src" / talk.slug / "talk.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return data.get("patches") or []


NATIVE_CORRECTIONS = [
    {
        "slide": "6BDE34AF-C6F3-4830-829F-9C89C9310573",
        "find": "Gabache et al. (2016), Sc. Rep.",
        "replace": "Ghabache et al. (2016), Sci. Rep.",
        "ocr_rect": [650, 990, 1270, 1070],
    },
    {
        "slide": "6BDE34AF-C6F3-4830-829F-9C89C9310573",
        "find": "Walls et al. (2015), PRE",
        "replace": "Walls, Henaux & Bird (2015), Phys. Rev. E 92, 021002",
        "ocr_rect": [1270, 950, 1920, 1080],
    },
    {
        "slide": "B47A6023-3D84-4F1B-9E00-F5153B91555B",
        "find": "Yellow Stone National Park:",
        "replace": "Yellowstone National Park:",
        "ocr_rect": [210, 1020, 1660, 1080],
    },
]


@pytest.fixture(scope="module")
def corrections(talk: Talk, patches: list[dict]) -> list[dict]:
    if talk.slug != "singular-bursting-bubbles":
        return patches
    obsolete = {p["find"] for p in NATIVE_CORRECTIONS}
    assert not any(p["find"] in obsolete for p in patches), "remove obsolete native-correction patches"
    return NATIVE_CORRECTIONS + patches


def test_corrections_in_slide_text(talk: Talk, corrections: list[dict]):
    if not corrections:
        pytest.skip("no corrections")
    for p in corrections:
        slide = next(s for s in talk.manifest["slides"] if s["id"] == p["slide"])
        text = _norm(slide["text"])
        assert _norm(p["replace"]) in text, f"slide {slide['n']}: {p['replace']!r} not in {text!r}"
        assert _norm(p["find"]) not in text, f"slide {slide['n']}: {p['find']!r} still in the text"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
@pytest.mark.parametrize("scale", [1, 2])
def test_corrections_legible_in_image(talk: Talk, corrections: list[dict], tmp_path: Path, scale: int):
    """OCR the final state at both densities, including corrections now made in Keynote.

    Each citation block is read separately so OCR cannot interleave columns.
    Text over the faded glass is read raw and at two thresholds. Accessibility
    text checks preserve exact punctuation; pixel OCR checks letters/numbers.
    """
    if not corrections:
        pytest.skip("no corrections")
    from PIL import Image

    def squash(text: str) -> str:
        return re.sub(r"[^a-z0-9&]", "", text.casefold())

    for i, p in enumerate(corrections):
        slide = next(s for s in talk.manifest["slides"] if s["id"] == p["slide"])
        readings = []
        field = "image2x" if scale == 2 else "image"
        if field not in slide["states"][-1]:
            pytest.skip("no 2x images in this talk")
        with Image.open(talk.media_path(slide["states"][-1][field])) as im:
            if p.get("ocr_rect"):
                im = im.crop(tuple(v * scale for v in p["ocr_rect"]))
            grey = im.convert("L")
            for threshold in (None, 90, 110):
                png = tmp_path / f"slide{slide['n']}-correction{i}-{threshold}.png"
                (grey if threshold is None else grey.point(lambda v, t=threshold: 0 if v < t else 255)).save(png)
                out = subprocess.run(
                    ["tesseract", str(png), "-", "--psm", "6" if p.get("ocr_rect") else "11"],
                    capture_output=True, text=True, check=True,
                )
                readings.append(out.stdout)
        for part in re.split(r"(?<=,)\s+", p["replace"]):
            assert any(squash(part) in squash(r) for r in readings), f"slide {slide['n']}: {part!r} not legible: {readings}"
        if squash(p["find"]) == squash(p["replace"]):
            # Whitespace is the correction for Yellowstone; removing all
            # whitespace would make the old and new spelling indistinguishable.
            old_words = r"\s+".join(re.escape(w) for w in p["find"].split())
            assert not any(re.search(old_words, r, re.I) for r in readings), readings
        else:
            assert not any(squash(p["find"]) in squash(r) for r in readings), f"slide {slide['n']}: old text still shown"


# What the export's wrong ToUnicode maps turned TeX maths into (beta -> omega,
# alpha -> epsilon, >> -> right arrow, theta -> check mark, ...), plus unknown glyphs.
MISREAD = set("ωεϖϑϱς↗↘↔↑↓↵✁✂⌧�\x00")
# Read off the slides themselves (rendered stills viewed during review).
KNOWN_TITLES = {
    87: "β < 49.3° ⟺ We j → ∞",
    91: "Geometry selects β; β selects α",
    72: "Laplace in a cone selects ν(β)",
    79: "Everything follows from ν(β)",
    46: "An alternate theory: inertia dominates (We j ≫ 1)",
}


@pytest.fixture(scope="module")
def decisions(talk: Talk) -> dict:
    path = REPO / "talks-src" / talk.slug / "talk.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_titles_and_text_carry_the_slide_maths(talk: Talk, decisions: dict):
    """Titles (shown beside the counter, read as the stage label) and screen-reader text match the slides.

    A title with a symbol from the mis-decoded set needs a human-written
    override in talk.yaml ``titles``; the text may not contain the symbols
    that only ever came from mis-decoding.
    """
    overrides = decisions.get("titles") or {}
    for s in talk.manifest["slides"]:
        bad = sorted(set(s["title"]) & MISREAD)
        assert not bad or s["id"] in overrides, f"slide {s['n']}: title {s['title']!r} has mis-decoded maths {bad}"
        bad = sorted(set(s["text"]) & (MISREAD - {"ω", "ε"}))
        assert not bad, f"slide {s['n']}: text has mis-decoded maths {bad}: {s['text'][:200]!r}"
    if talk.slug != "singular-bursting-bubbles":
        return
    for n, title in KNOWN_TITLES.items():
        assert talk.slide(n)["title"] == title, (n, talk.slide(n)["title"])
    assert "Zeff" in talk.slide(57)["text"] and "Ze!" not in talk.slide(57)["text"]
    # Beamer keeps later overlays in the page at zero opacity: slide 87 must not read slide 88's line.
    assert "Oh = 0.03" not in talk.slide(87)["text"] and "Oh = 0.03" in talk.slide(88)["text"]
    assert "✓ shapes collapse" in talk.slide(43)["text"]
