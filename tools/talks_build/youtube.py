"""Place a YouTube embed in place of a Keynote movie.

YouTube's terms forbid drawing over its player, so the embed rectangle must
avoid every other object on the slide (the title and the attribution in
particular). We take as obstacles the layer rectangles of all visible
objects except the replaced movie and full-canvas backgrounds, plus every
text line box ``pdftotext`` reports for any visible texture (backgrounds
included), inflate them by a margin, and pick the largest 16:9 rectangle
(whole multiples of 16 x 9 px) that avoids them, closest to the centre of
the original movie.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geometry import Rect, snap
from .keynote import Slide
from .text import page_lines

MARGIN = 16


@dataclass(frozen=True)
class YouTubeSpec:
    slide: str
    movie: str
    id: str
    start: float
    end: float
    title: str
    credit: str
    rect: tuple[int, int, int, int] | None = None
    # What the slide shows in a click state where Keynote shows the movie's
    # poster before the click that starts it: "poster" draws that poster
    # frame, fitted into the embed rectangle (Keynote's own first state);
    # "embed" mounts the embed already, so it plays from that state on.
    before_start: str = "poster"


BEFORE_START = ("poster", "embed")


def movie_object_ids(slide: Slide, prefix: str) -> set[str]:
    return {m.object_id for m in slide.movies() if m.asset_id.startswith(prefix)}


def obstacles(slide: Slide, exclude_objects: set[str], canvas_w: int, canvas_h: int) -> list[Rect]:
    rects: list[Rect] = []
    seen: set[str] = set()
    for state in slide.states:
        for leaf in state.leaves():
            if not leaf.visible or leaf.object_id in exclude_objects:
                continue
            full = leaf.rect.w >= canvas_w - 1 and leaf.rect.h >= canvas_h - 1
            if not full:
                rects.append(leaf.rect)
            if leaf.texture in seen:
                continue
            seen.add(leaf.texture)
            ox, oy = snap(leaf.rect.x), snap(leaf.rect.y)
            for line in page_lines(slide.texture_pdf(leaf.texture), slide.texture_page(leaf.texture)):
                b = line.box
                rects.append(Rect(b.x + ox, b.y + oy, b.w, b.h))
    return rects


def choose_rect(
    blockers: list[Rect],
    canvas_w: int,
    canvas_h: int,
    prefer: Rect | None = None,
    margin: int = MARGIN,
) -> tuple[int, int, int, int]:
    """Largest 16:9 rectangle clear of ``blockers`` (inflated by ``margin``)."""
    mask = np.zeros((canvas_h, canvas_w), dtype=np.int32)
    for r in blockers:
        g = r.inflate(margin)
        x1, y1 = max(0, int(np.floor(g.x))), max(0, int(np.floor(g.y)))
        x2, y2 = min(canvas_w, int(np.ceil(g.x2))), min(canvas_h, int(np.ceil(g.y2)))
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = 1
    sat = np.zeros((canvas_h + 1, canvas_w + 1), dtype=np.int64)
    sat[1:, 1:] = mask.cumsum(0).cumsum(1)
    target = prefer or Rect(0, 0, canvas_w, canvas_h)
    cx, cy = target.x + target.w / 2, target.y + target.h / 2
    kmax = min(canvas_w // 16, canvas_h // 9)
    for k in range(kmax, 0, -1):
        w, h = 16 * k, 9 * k
        s = sat[h:, w:] - sat[:-h, w:] - sat[h:, :-w] + sat[:-h, :-w]
        ys, xs = np.nonzero(s == 0)
        if len(xs) == 0:
            continue
        d = (xs + w / 2 - cx) ** 2 + (ys + h / 2 - cy) ** 2
        # deterministic tie-break: distance, then y, then x
        i = np.lexsort((xs, ys, d))[0]
        return int(xs[i]), int(ys[i]), w, h
    raise ValueError("no room for a 16:9 embed")


# --------------------------------------------------------------------------
# Contrast after removing the movie

DARK_TEXT = (34, 34, 34)
MIN_CONTRAST = 3.0


def _luminance(rgb) -> float:
    def lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(v)) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb)


def contrast_ratio(a, b) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def fix_contrast(slide: Slide, compositor, exclude_objects: set[str], canvas_w: int, canvas_h: int) -> list[dict]:
    """Recolour layers whose ink relied on the removed movie for contrast.

    A title or credit drawn in white over a full-screen movie disappears on
    the plain slide background once the movie is replaced by an embed. For
    every visible non-background layer, compare its ink colour with what is
    now beneath it; below a WCAG contrast of 3:1 the ink is redrawn in a dark
    neutral (alpha, and so the glyph shapes, unchanged).
    """
    from PIL import Image

    changed: list[dict] = []
    done: set[str] = set()
    for state in slide.states:
        leaves = state.leaves()
        for leaf in leaves:
            if not leaf.visible or leaf.object_id in exclude_objects or leaf.texture in done:
                continue
            if leaf.rect.w >= canvas_w - 1 and leaf.rect.h >= canvas_h - 1:
                continue
            img, dx, dy = compositor.texture_image(leaf)
            arr = np.asarray(img.convert("RGBA"))
            ink = arr[..., 3] > 200
            if ink.sum() < 20:
                continue
            below = compositor.composite(
                leaves, include=lambda lf, o=leaf.order: lf.order < o and lf.object_id not in exclude_objects
            )
            x0, y0 = snap(leaf.rect.x) + dx, snap(leaf.rect.y) + dy
            bg = np.asarray(below.convert("RGB"))
            ys, xs = np.nonzero(ink)
            cx, cy = xs + x0, ys + y0
            keep = (cx >= 0) & (cx < canvas_w) & (cy >= 0) & (cy < canvas_h)
            if not keep.any():
                continue
            ink_rgb = np.median(arr[ys[keep], xs[keep], :3], axis=0)
            bg_rgb = np.median(bg[cy[keep], cx[keep]], axis=0)
            ratio = contrast_ratio(ink_rgb, bg_rgb)
            done.add(leaf.texture)
            if ratio >= MIN_CONTRAST:
                continue
            out = arr.copy()
            out[..., 0], out[..., 1], out[..., 2] = DARK_TEXT
            compositor.overrides[leaf.texture] = (Image.fromarray(out, "RGBA"), dx, dy)
            changed.append(
                {
                    "slide": slide.n,
                    "texture": leaf.texture,
                    "rect": [round(v, 1) for v in leaf.rect.tenths()],
                    "ink": _hex(ink_rgb),
                    "background": _hex(bg_rgb),
                    "contrast_before": round(ratio, 2),
                    "recoloured_to": _hex(DARK_TEXT),
                }
            )
    return changed


# --------------------------------------------------------------------------
# Before the movie starts


def poster_in_rect(compositor, leaves, exclude_objects: set[str], rect: tuple[int, int, int, int]):
    """The replaced movie's poster, as Keynote draws it, fitted into ``rect``.

    Returns an RGBA canvas-sized image with the poster (aspect ratio kept,
    centred) inside ``rect`` and transparency elsewhere, or ``None`` when the
    movie is not visible in ``leaves``.
    """
    from PIL import Image

    movie = [lf for lf in leaves if lf.visible and lf.object_id in exclude_objects]
    if not movie:
        return None
    layer = compositor.composite(movie, background=None, include=lambda lf: True, top_sprites=False)
    w, h = layer.size
    box = None
    for lf in movie:
        r = Rect(*lf.rect.snapped())
        box = r if box is None else box.union(r)
    clipped = box.intersection(Rect(0, 0, w, h)) if box is not None else None
    if clipped is None or clipped.w < 2 or clipped.h < 2:
        return None
    x1, y1, x2, y2 = int(clipped.x), int(clipped.y), int(clipped.x2), int(clipped.y2)
    poster = layer.crop((x1, y1, x2, y2))
    rx, ry, rw, rh = rect
    scale = min(rw / poster.width, rh / poster.height)
    pw, ph = max(1, round(poster.width * scale)), max(1, round(poster.height * scale))
    fitted = poster.resize((pw, ph), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(fitted, (rx + (rw - pw) // 2, ry + (rh - ph) // 2))
    return out
