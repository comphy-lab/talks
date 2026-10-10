"""Write a tiny synthetic Keynote HTML export for tests.

Everything is generated: a hand-written vector PDF texture atlas per slide
(base-14 Helvetica text and filled rectangles), JSON layer trees with the
same nesting Keynote uses, and short H.264 movies from ffmpeg's ``testsrc2``
pattern. No third-party media is involved.

Deck (5 click states):

1. ``S1``: one event (flattened final state), title only.        1 state
2. ``S2``: click starts movie ``clip`` under a label that overlaps it,
   click builds in a citation, click leaves.                     3 states
3. ``S3``: the same movie (a re-muxed copy with shifted container
   timestamps) starts automatically at the same rectangle.       1 state
4. ``S4``: a full-canvas movie ``Clipyt`` under a title and a white
   credit, replaced by a YouTube embed in the fixture talk.yaml. 2 states

Total: 7 click states.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

W, H = 1920, 1080
SLIDES = {
    "S1": "11111111-1111-4111-8111-111111111111",
    "S2": "22222222-2222-4222-8222-222222222222",
    "S3": "33333333-3333-4333-8333-333333333333",
    "S4": "44444444-4444-4444-8444-444444444444",
}
MOVIE_OBJ = "AAAAAAAA-0000-4000-8000-000000000001"
MOVIE_OBJ3 = "AAAAAAAA-0000-4000-8000-000000000003"
LABEL_OBJ = "BBBBBBBB-0000-4000-8000-000000000002"
CITE_OBJ = "CCCCCCCC-0000-4000-8000-000000000003"
YT_OBJ = "DDDDDDDD-0000-4000-8000-000000000004"
CLIP = "clip.mp4-0.0000-1.0000"
CLIPYT = "Clipyt.mp4-0.0000-1.0000"
MOVIE_RECT = (200.0, 300.0, 640.0, 360.0)  # x, y, w, h

# ----------------------------------------------------------------------------
# Minimal PDF writer


def _pdf(pages: list[tuple[int, int, str]]) -> bytes:
    """A PDF with one page per ``(width, height, content stream)``."""
    objs: list[bytes] = []

    def add(body: str | bytes) -> int:
        objs.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objs)

    catalog = add("")  # placeholder, filled below
    pages_id = add("")
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for w, h, ops in pages:
        data = ops.encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {w} {h}] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
            )
        )
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objs[pages_id - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _rect(h: int, x: float, y: float, w: float, hh: float, rgb: tuple[float, float, float]) -> str:
    return f"{rgb[0]} {rgb[1]} {rgb[2]} rg {x} {h - y - hh} {w} {hh} re f\n"


def _text(h: int, x: float, y_base: float, size: float, text: str, rgb=(0, 0, 0)) -> str:
    return f"BT /F1 {size} Tf {rgb[0]} {rgb[1]} {rgb[2]} rg {x} {h - y_base} Td ({text}) Tj ET\n"


# ----------------------------------------------------------------------------
# Layer trees


def _state(x: float, y: float, w: float, h: float, hidden: bool = False) -> dict:
    """``initialState`` for a layer whose top-left is ``(x, y)`` in its parent."""
    return {
        "affineTransform": [1, 0, 0, 1, 0, 0],
        "masksToBounds": False,
        "rotation": 0,
        "scale": 1,
        "position": {"pointX": x + w / 2, "pointY": y + h / 2},
        "width": w,
        "height": h,
        "sublayerTransform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "contentsRect": {"x": 0, "y": 0, "width": 1, "height": 1},
        "opacity": 1,
        "hidden": hidden,
        "anchorPoint": {"pointX": 0.5, "pointY": 0.5},
    }


def _object(texture: str, rect, object_id: str | None = None, hidden: bool = False) -> dict:
    """Keynote nesting: full-canvas wrapper -> object layer -> texture layer."""
    x, y, w, h = rect
    obj = {"initialState": _state(x, y, w, h), "layers": [
        {"initialState": _state(0, 0, w, h, hidden=hidden), "texture": texture, "animations": []}
    ]}  # fmt: skip
    if object_id:
        obj["objectID"] = object_id
    return {"initialState": _state(0, 0, W, H), "layers": [obj]}


def _root(children: list[dict], object_id: str | None = None) -> dict:
    root = {"initialState": _state(0, 0, W, H), "layers": children}
    if object_id:
        root["objectID"] = object_id
    return root


def _flat(texture: str) -> dict:
    return _root([{"initialState": _state(0, 0, W, H), "layers": [
        {"initialState": _state(0, 0, W, H), "texture": texture}
    ]}], object_id="0")  # fmt: skip


def _transition(texture: str) -> dict:
    return {
        "automaticPlay": False,
        "accessibility": [],
        "hyperlinks": [],
        "baseLayer": _flat(texture),
        "effects": [{"type": "transition", "name": "none", "objectID": "0", "duration": 0.001, "beginTime": 0}],
    }


def _movie_effect(object_id: str, asset: str, rect) -> dict:
    x, y, w, h = rect
    render = {
        "type": "buildIn",
        "name": "renderMovie",
        "objectID": object_id,
        "duration": 0,
        "movie": {"asset": asset, "startTime": 0, "endTime": 1.0, "loopMode": "looping", "volume": 1},
        "baseLayer": {"initialState": _state(x, y, w, h), "objectID": object_id, "layers": []},
    }
    return {"type": "buildIn", "name": "apple:movie-start", "objectID": object_id, "duration": 0.001,
            "effects": [render]}  # fmt: skip


def _textures(uuid: str, sizes: list[tuple[int, int]]) -> dict:
    return {
        f"T{uuid[:4]}{i:02d}".ljust(32, "0"): {
            "type": "texture", "index": i, "url": {"native": f"assets/{uuid}.pdf"}, "width": w, "height": h,
        }
        for i, (w, h) in enumerate(sizes)
    }  # fmt: skip


def _tex(uuid: str, i: int) -> str:
    return f"T{uuid[:4]}{i:02d}".ljust(32, "0")


# ----------------------------------------------------------------------------


def _movie(path: Path, colour_shift: int = 0, ts_offset: float | None = None, src: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if src is not None:
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-c", "copy"]
        if ts_offset is not None:
            args += ["-output_ts_offset", str(ts_offset)]
        args += ["-metadata", "comment=copy", str(path)]
    else:
        args = [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size=320x180:rate=10:duration=1,hue=h={colour_shift}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-threads", "1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact", str(path),
        ]  # fmt: skip
    subprocess.run(args, check=True)


def write_export(root: Path) -> Path:
    """Write the export under ``root/Fixture deck`` and return its path."""
    export = root / "Fixture deck"
    assets = export / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "header.json").write_text(json.dumps({
        "slideList": [SLIDES[k] for k in ("S1", "S2", "S3", "S4")],
        "slideWidth": W, "slideHeight": H, "slideCount": 4,
    }, indent=1))  # fmt: skip
    flat_pages = []

    # S1: one flattened event
    u = SLIDES["S1"]
    final = _rect(H, 0, 0, W, H, (1, 1, 1)) + _text(H, 300, 200, 72, "Fixture title one")
    flat_pages.append(final)
    _slide(assets, u, [(W, H, final)], [_transition(_tex(u, 0))])

    # S2: movie with a label above it; citation built in on the second click
    u = SLIDES["S2"]
    mx, my, mw, mh = MOVIE_RECT
    bg = _rect(H, 0, 0, W, H, (1, 1, 1)) + _text(H, 100, 150, 64, "Movie with a label")
    poster = _rect(180, 0, 0, 320, 180, (0.2, 0.4, 0.8))
    label = _rect(60, 0, 0, 300, 60, (1, 0.9, 0)) + _text(60, 10, 42, 32, "label", (0, 0, 0))
    cite = _text(40, 0, 30, 28, "Smith et al. (2020), Phys.")
    final = bg + _rect(H, mx, my, mw, mh, (0.2, 0.4, 0.8)) + _rect(H, 700, 600, 300, 60, (1, 0.9, 0))
    final += _text(H, 710, 642, 32, "label") + _text(H, 200, 730, 28, "Smith et al. (2020), Phys.")
    flat_pages.append(final)
    pages = [(W, H, bg), (320, 180, poster), (300, 60, label), (400, 40, cite), (W, H, final)]
    t = [_tex(u, i) for i in range(5)]
    base0 = _root([
        _object(t[0], (0, 0, W, H)),
        _object(t[1], MOVIE_RECT, MOVIE_OBJ),
        _object(t[2], (700, 600, 300, 60), LABEL_OBJ),
        _object(t[3], (200, 700, 400, 40), CITE_OBJ, hidden=True),
    ])  # fmt: skip
    base1 = json.loads(json.dumps(base0))
    events = [
        {"automaticPlay": False, "accessibility": [], "hyperlinks": [], "baseLayer": base0,
         "effects": [_movie_effect(MOVIE_OBJ, CLIP, MOVIE_RECT)]},
        {"automaticPlay": False, "accessibility": [], "hyperlinks": [], "baseLayer": base1,
         "effects": [{"type": "buildIn", "name": "apple:dissolve", "objectID": CITE_OBJ, "duration": 0.5}]},
        _transition(t[4]),
    ]  # fmt: skip
    _slide(assets, u, pages, events, videos={CLIP: (320, 180)})
    _movie(assets / u / "assets" / f"{CLIP}.mp4")

    # S3: same stream, re-muxed copy, auto-start at the same rectangle
    u = SLIDES["S3"]
    bg3 = _rect(H, 0, 0, W, H, (1, 1, 1)) + _text(H, 100, 150, 64, "Same movie continues")
    final = bg3 + _rect(H, mx, my, mw, mh, (0.2, 0.4, 0.8))
    flat_pages.append(final)
    pages = [(W, H, bg3), (320, 180, poster), (W, H, final)]
    t = [_tex(u, i) for i in range(3)]
    base = _root([_object(t[0], (0, 0, W, H)), _object(t[1], MOVIE_RECT, MOVIE_OBJ3)])
    events = [
        {"automaticPlay": True, "accessibility": [], "hyperlinks": [], "baseLayer": base,
         "effects": [_movie_effect(MOVIE_OBJ3, CLIP, MOVIE_RECT)]},
        _transition(t[2]),
    ]  # fmt: skip
    _slide(assets, u, pages, events, videos={CLIP: (320, 180)})
    _movie(assets / u / "assets" / f"{CLIP}.mp4", src=assets / SLIDES["S2"] / "assets" / f"{CLIP}.mp4", ts_offset=2.5)

    # S4: full-canvas movie under a title and a white credit -> YouTube
    u = SLIDES["S4"]
    bg4 = _rect(H, 0, 0, W, H, (1, 1, 1))
    title = _text(90, 0, 70, 64, "Mud and more mud", (0.4, 0.1, 0.4))
    credit = _text(40, 0, 30, 28, "Video credit line", (1, 1, 1))
    ytposter = _rect(H, 0, 0, W, H, (0.5, 0.45, 0.4))
    final = bg4 + ytposter + _text(H, 300, 110, 64, "Mud and more mud", (0.4, 0.1, 0.4))
    final += _text(H, 300, 1060, 28, "Video credit line", (1, 1, 1))
    flat_pages.append(final)
    pages = [(W, H, bg4), (W, H, ytposter), (900, 90, title), (600, 40, credit), (W, H, final)]
    t = [_tex(u, i) for i in range(5)]
    base = _root([
        _object(t[0], (0, 0, W, H)),
        _object(t[1], (0, 0, W, H), YT_OBJ),
        _object(t[2], (300, 40, 900, 90)),
        _object(t[3], (300, 1030, 600, 40)),
    ])  # fmt: skip
    events = [
        {"automaticPlay": False, "accessibility": [], "hyperlinks": [], "baseLayer": base,
         "effects": [_movie_effect(YT_OBJ, CLIPYT, (0, 0, W, H))]},
        _transition(t[4]),
    ]  # fmt: skip
    _slide(assets, u, pages, events, videos={CLIPYT: (320, 180)})
    _movie(assets / u / "assets" / f"{CLIPYT}.mp4", colour_shift=90)

    (root / "Fixture deck.pdf").write_bytes(_pdf([(W, H, p) for p in flat_pages]))
    return export


def _slide(assets: Path, uuid: str, pages, events, videos: dict | None = None) -> None:
    sdir = assets / uuid
    (sdir / "assets").mkdir(parents=True, exist_ok=True)
    (sdir / "assets" / f"{uuid}.pdf").write_bytes(_pdf(pages))
    asset_map = _textures(uuid, [(w, h) for w, h, _ in pages])
    for name, (w, h) in (videos or {}).items():
        ext = name.split("-")[0].rsplit(".", 1)[1]
        asset_map[name] = {
            "type": "video", "index": 0, "width": w, "height": h,
            "url": {"native": f"assets/{name}.{ext}", "web": f"assets/{name}.{ext}"},
        }  # fmt: skip
    (sdir / f"{uuid}.json").write_text(json.dumps({"assets": asset_map, "events": events}, indent=1))


TALK_YAML = """\
slug: fixture-talk
title: Fixture talk
subtitle: A synthetic deck for tests
speaker: Test Speaker
venue_note: Generated by the test suite.
updated: "2026-01-02"
media_base: https://media.comphy-lab.org/talks/fixture-talk/
video:
  loop: true
  spans: auto
  max_scale: 2.0
youtube:
  - slide: 44444444-4444-4444-8444-444444444444
    movie: Clipyt
    id: abcdefghijk
    start: 1.5
    end: 9.25
    title: Fixture mud
    credit: "Fixture credit"
    rect: null
{youtube_extra}{patches}{titles}changelog:
  - date: "2026-01-02"
    note: Fixture.
"""

PATCHES_YAML = """\
patches:
  - slide: 22222222-2222-4222-8222-222222222222
    find: "Smith et al. (2020), Phys."
    replace: "Smith, Jones & Brown (2020), Phys. Rev. Fluids 5, 012345"
  - slide: 11111111-1111-4111-8111-111111111111
    find: "Fixture title one"
    replace: "Fixture title 1"
"""


def write_talk(
    root: Path, with_patches: bool = False, before_start: str | None = None, titles: dict | None = None
) -> Path:
    path = root / "talks-src" / "fixture-talk" / "talk.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = f"    before_start: {before_start}\n" if before_start else ""
    titles_yaml = "titles:\n" + "".join(f'  {k}: "{v}"\n' for k, v in titles.items()) if titles else ""
    path.write_text(
        TALK_YAML.format(patches=PATCHES_YAML if with_patches else "", youtube_extra=extra, titles=titles_yaml)
    )
    return path
