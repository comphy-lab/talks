"""Text corrections drawn onto slide images (``talk.yaml`` ``patches[]``).

A patch names a slide, the text to ``find`` and its ``replace``ment. The
pipeline

1. finds the text line in the slide's per-object textures with
   ``pdftotext -bbox-layout`` (so the correction follows the object through
   every build state, and the overlay layer above videos);
2. identifies the original font by rendering the ``find`` text in each font
   used on that texture page and keeping the best pixel match (the glyphs
   decide, not the font name alone), and fits its size to the original ink;
3. erases the original glyphs from that texture;
4. lays out the replacement with the same font, size, colour and baseline:
   one line where it fits, otherwise wrapped at a comma, right-aligned or
   reduced, avoiding the other text on the slide and the canvas edge;
5. draws it at the texture's own depth, so layers above it (a translucent
   reflection, for example) still tint it as in Keynote, unless an opaque
   layer above would hide part of it, in which case it is drawn on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .geometry import Rect, snap
from .keynote import Leaf, Slide, State
from .raster import Compositor, Sprite
from .text import Line, find_words, font_file, page_fonts, page_lines

CANVAS_MARGIN = 8.0
INK_THRESHOLD = 96


class PatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PatchSpec:
    slide: str
    find: str
    replace: str
    doi: str | None = None


@dataclass
class AppliedPatch:
    spec: PatchSpec
    texture: str
    font_name: str
    font_path: Path
    size: float
    colour: tuple[int, int, int]
    fit_iou: float
    original_box: Rect
    box: Rect
    lines: list[str]
    layout: str
    mode: str  # "texture" (same depth as the original) or "top"
    sprite: Sprite
    erased: Image.Image
    texture_pdf: Path
    texture_page: int
    erase_bounds: tuple[int, int, int, int]
    layout_plan: Layout
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {
            "slide": self.spec.slide,
            "find": self.spec.find,
            "replace": self.spec.replace,
            "font": self.font_name,
            "size_px": round(self.size, 2),
            "colour": "#{:02x}{:02x}{:02x}".format(*self.colour),
            "fit_iou": round(self.fit_iou, 3),
            "original_box": [round(v, 1) for v in self.original_box.tenths()],
            "box": [round(v, 1) for v in self.box.tenths()],
            "lines": self.lines,
            "layout": self.layout,
            "depth": self.mode,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Text rendering


def _font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size, layout_engine=ImageFont.Layout.BASIC)


def render_line(path: Path, size: float, text: str, fill=(0, 0, 0, 255)) -> tuple[Image.Image, int, int]:
    """Render one line; return ``(image, anchor_x, anchor_y)`` (left baseline)."""
    font = _font(path, size)
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    pad = 4
    w = int(right - left) + 2 * pad
    h = int(bottom - top) + 2 * pad
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ax, ay = pad - left, pad - top
    ImageDraw.Draw(img).text((ax, ay), text, font=font, anchor="ls", fill=fill)
    return img, ax, ay


def _ink(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha > INK_THRESHOLD)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    h = max(a.shape[0], b.shape[0])
    w = max(a.shape[1], b.shape[1])
    pa = np.zeros((h, w), bool)
    pb = np.zeros((h, w), bool)
    pa[: a.shape[0], : a.shape[1]] = a
    pb[: b.shape[0], : b.shape[1]] = b
    union = np.logical_or(pa, pb).sum()
    return float(np.logical_and(pa, pb).sum() / union) if union else 0.0


@dataclass
class FontFit:
    name: str
    path: Path
    size: float
    iou: float
    # anchor (left baseline) relative to the original ink top-left
    anchor_dx: float
    anchor_dy: float


def fit_font(original_alpha: np.ndarray, text: str, fonts: list[tuple[str, Path]]) -> FontFit:
    """Choose the font and size whose rendering of ``text`` best matches the ink."""
    ink = _ink(original_alpha)
    if ink is None:
        raise PatchError("no ink found under the text to replace")
    x1, y1, x2, y2 = ink
    target = original_alpha[y1:y2, x1:x2] > INK_THRESHOLD
    best: FontFit | None = None
    for name, path in fonts:
        probe, _, _ = render_line(path, 100.0, text)
        pink = _ink(np.asarray(probe.getchannel("A")))
        if pink is None:
            continue
        size = 100.0 * (x2 - x1) / (pink[2] - pink[0])
        # refine once: glyph hinting makes width slightly non-linear in size
        for _ in range(2):
            img, ax, ay = render_line(path, size, text)
            rink = _ink(np.asarray(img.getchannel("A")))
            if rink is None:
                break
            size *= (x2 - x1) / max(1, rink[2] - rink[0])
        img, ax, ay = render_line(path, size, text)
        alpha = np.asarray(img.getchannel("A"))
        rink = _ink(alpha)
        if rink is None:
            continue
        rendered = alpha[rink[1] : rink[3], rink[0] : rink[2]] > INK_THRESHOLD
        score = _iou(target, rendered)
        fit = FontFit(name, path, size, score, anchor_dx=ax - rink[0], anchor_dy=ay - rink[1])
        if best is None or fit.iou > best.iou:
            best = fit
    if best is None:
        raise PatchError("no usable font found for the text to replace")
    return best


# --------------------------------------------------------------------------
# Layout


@dataclass
class Layout:
    name: str
    size: float
    lines: list[tuple[str, float, float]]  # (text, anchor_x, anchor_y) in canvas px
    box: Rect


def _line_box(path: Path, size: float, text: str, ax: float, ay: float) -> Rect:
    left, top, right, bottom = _font(path, size).getbbox(text, anchor="ls")
    return Rect.from_corners(ax + left, ay + top, ax + right, ay + bottom)


def _splits(text: str) -> list[tuple[str, str]]:
    """Two-line splits after a comma, best balanced first."""
    out = []
    for m in re.finditer(r",\s+", text):
        a, b = text[: m.start() + 1].strip(), text[m.end() :].strip()
        if a and b:
            out.append((a, b))
    if not out:
        words = text.split()
        for i in range(1, len(words)):
            out.append((" ".join(words[:i]), " ".join(words[i:])))
    out.sort(key=lambda ab: abs(len(ab[0]) - len(ab[1])))
    return out


def candidate_layouts(
    path: Path,
    size: float,
    text: str,
    anchor_x: float,
    anchor_y: float,
    canvas_w: float,
    end_x: float | None = None,
) -> list[Layout]:
    """Layouts to try, best first.

    ``end_x`` is the original text's right edge when more text follows the
    match on the same line; the first choice then keeps that edge, so the gap
    before the following text is unchanged.
    """
    right_limit = canvas_w - CANVAS_MARGIN
    out: list[Layout] = []

    def single(sz: float, align: str) -> Layout:
        box = _line_box(path, sz, text, anchor_x, anchor_y)
        if align == "left":
            ax = anchor_x
        elif align == "end":
            ax = anchor_x + (end_x - box.x2)
        else:
            ax = anchor_x + (right_limit - box.x2)
        return Layout(
            f"one line, {align}-aligned, {sz:.1f}px",
            sz,
            [(text, ax, anchor_y)],
            _line_box(path, sz, text, ax, anchor_y),
        )

    def double(sz: float, align: str, first: str, second: str) -> Layout:
        f = _font(path, sz)
        p = float(sum(f.getmetrics()))
        b1 = _line_box(path, sz, first, anchor_x, anchor_y)
        b2 = _line_box(path, sz, second, anchor_x, anchor_y + p)
        if align == "left":
            lines = [(first, anchor_x, anchor_y), (second, anchor_x, anchor_y + p)]
        else:
            lines = [
                (first, anchor_x + (right_limit - b1.x2), anchor_y),
                (second, anchor_x + (right_limit - b2.x2), anchor_y + p),
            ]
        box = _line_box(path, sz, lines[0][0], lines[0][1], lines[0][2]).union(
            _line_box(path, sz, lines[1][0], lines[1][1], lines[1][2])
        )
        return Layout(f"two lines, {align}-aligned, {sz:.1f}px", sz, lines, box)

    if end_x is not None:
        out.append(single(size, "end"))
    out.append(single(size, "left"))
    for first, second in _splits(text)[:3]:
        out.append(double(size, "left", first, second))
    out.append(single(size, "right"))
    for first, second in _splits(text)[:3]:
        out.append(double(size, "right", first, second))
    for factor in (0.92, 0.85, 0.78, 0.7):
        out.append(single(size * factor, "left"))
        out.append(single(size * factor, "right"))
    return out


def layout_ok(layout: Layout, obstacles: list[Rect], canvas: Rect) -> bool:
    if not canvas.inflate(-CANVAS_MARGIN).contains(layout.box):
        return False
    return not any(layout.box.intersects(o) for o in obstacles)


def draw_layout(layout: Layout, path: Path, colour: tuple[int, int, int], scale: int = 1) -> Sprite:
    """Draw a logical layout directly at its target density, without resizing ink."""
    font = _font(path, layout.size * scale)
    box = layout.box.inflate(4)
    x0, y0 = int(np.floor(box.x)), int(np.floor(box.y))
    w, h = int(np.ceil(box.x2)) - x0, int(np.ceil(box.y2)) - y0
    img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for text, ax, ay in layout.lines:
        draw.text(((ax - x0) * scale, (ay - y0) * scale), text, font=font, anchor="ls", fill=colour + (255,))
    return Sprite(img, x0, y0)


# --------------------------------------------------------------------------
# Planning against a slide


def _slide_leaves(slide: Slide) -> list[tuple[State, Leaf]]:
    out = []
    for state in slide.states:
        for leaf in state.leaves():
            if leaf.visible:
                out.append((state, leaf))
    return out


def _text_obstacles(slide: Slide, exclude: tuple[str, Line] | None) -> list[Rect]:
    """Canvas boxes of every text line on any state of the slide."""
    seen: set[str] = set()
    boxes: list[Rect] = []
    for _state, leaf in _slide_leaves(slide):
        if leaf.texture in seen:
            continue
        seen.add(leaf.texture)
        lines = page_lines(slide.texture_pdf(leaf.texture), slide.texture_page(leaf.texture))
        ox, oy = snap(leaf.rect.x), snap(leaf.rect.y)
        for line in lines:
            if exclude and exclude[0] == leaf.texture and exclude[1] == line:
                continue
            b = line.box
            boxes.append(Rect(b.x + ox, b.y + oy, b.w, b.h))
    return boxes


def plan_patch(
    slide: Slide,
    spec: PatchSpec,
    compositor: Compositor,
    canvas_w: int,
    canvas_h: int,
    extra_obstacles: list[Rect],
) -> AppliedPatch:
    match = None
    for _state, leaf in _slide_leaves(slide):
        pdf, page = slide.texture_pdf(leaf.texture), slide.texture_page(leaf.texture)
        found = find_words(page_lines(pdf, page), spec.find)
        if found:
            match = (leaf, pdf, page, found)
            break
    if match is None:
        raise PatchError(f"slide {slide.n}: text not found: {spec.find!r}")
    leaf, pdf, page, (line, i0, i1) = match
    words = line.words[i0:i1]
    wbox = words[0].box
    for w in words[1:]:
        wbox = wbox.union(w.box)
    notes = []
    if i1 < len(line.words):
        notes.append("text follows the match on the same line; it is left in place")

    tex = compositor.pages.page(pdf, page)
    ox, oy = snap(leaf.rect.x), snap(leaf.rect.y)
    # pixel box of the words inside the texture, padded by a pixel
    bx1 = max(0, int(np.floor(wbox.x)) - 1)
    by1 = max(0, int(np.floor(wbox.y)))
    bx2 = min(tex.size[0], int(np.ceil(wbox.x2)) + 1)
    by2 = min(tex.size[1], int(np.ceil(wbox.y2)))
    arr = np.asarray(tex)
    alpha, colour = ink_and_colour(arr, (bx1, by1, bx2, by2))
    if leaf.texture in compositor.overrides:
        # The texture was already recoloured (e.g. legibility fix on a YouTube
        # slide): draw the replacement in the colour the viewer actually sees.
        _, colour = ink_and_colour(np.asarray(compositor.overrides[leaf.texture][0]), (bx1, by1, bx2, by2))
    ink = _ink(alpha)
    if ink is None:
        raise PatchError(f"slide {slide.n}: no ink under {spec.find!r}")

    fonts = []
    for name in page_fonts(pdf, page):
        path = font_file(name)
        if path is not None:
            fonts.append((name, path))
    if not fonts:
        raise PatchError(f"slide {slide.n}: none of the page fonts is installed locally")
    fit = fit_font(alpha, spec.find, fonts)

    # original ink top-left in canvas coordinates -> baseline anchor
    ink_x = ox + bx1 + ink[0]
    ink_y = oy + by1 + ink[1]
    anchor_x = ink_x + fit.anchor_dx
    anchor_y = ink_y + fit.anchor_dy
    original_box = Rect.from_corners(ox + bx1 + ink[0], oy + by1 + ink[1], ox + bx1 + ink[2], oy + by1 + ink[3])

    canvas = Rect(0, 0, canvas_w, canvas_h)
    obstacles = _text_obstacles(slide, (leaf.texture, line)) + list(extra_obstacles)
    # Movies are obstacles unless the original text already sat on one.
    for state in slide.states:
        for movie in state.movies:
            if not movie.rect.intersects(original_box):
                obstacles.append(movie.rect)
    chosen = None
    end_x = float(original_box.x2) if i1 < len(line.words) else None
    for layout in candidate_layouts(fit.path, fit.size, spec.replace, anchor_x, anchor_y, canvas_w, end_x):
        if layout_ok(layout, obstacles, canvas):
            chosen = layout
            break
    if chosen is None:
        raise PatchError(f"slide {slide.n}: no layout fits for {spec.replace!r}")

    base = compositor.overrides[leaf.texture][0] if leaf.texture in compositor.overrides else tex
    erased = erase_box(base, (bx1, by1, bx2, by2))

    sprite = draw_layout(chosen, fit.path, colour)
    mode = _depth(slide, leaf, sprite, compositor)
    return AppliedPatch(
        spec=spec,
        texture=leaf.texture,
        font_name=fit.name,
        font_path=fit.path,
        size=fit.size,
        colour=colour,
        fit_iou=fit.iou,
        original_box=original_box,
        box=chosen.box,
        lines=[t for t, _, _ in chosen.lines],
        layout=chosen.name,
        mode=mode,
        sprite=sprite,
        erased=erased,
        texture_pdf=pdf,
        texture_page=page,
        erase_bounds=(bx1, by1, bx2, by2),
        layout_plan=chosen,
        notes=notes,
    )


def _ring(arr: np.ndarray, box: tuple[int, int, int, int], ring: int = 3) -> np.ndarray:
    """Pixels in a band ``ring`` px wide around ``box``."""
    x1, y1, x2, y2 = box
    h, w = arr.shape[:2]
    mask = np.zeros((h, w), bool)
    mask[max(0, y1 - ring) : min(h, y2 + ring), max(0, x1 - ring) : min(w, x2 + ring)] = True
    mask[y1:y2, x1:x2] = False
    return arr[mask]


def ink_and_colour(arr: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, tuple[int, int, int]]:
    """Ink coverage (0-255) and text colour inside ``box`` of an RGBA texture.

    A text-box texture is transparent around its glyphs, so alpha is the ink.
    A flattened texture is opaque: there the ink is how far each pixel is
    from the background colour (the median of the ring around the box).
    """
    x1, y1, x2, y2 = box
    crop = arr[y1:y2, x1:x2].astype(np.float64)
    around = _ring(arr, box)
    if len(around) and np.median(around[:, 3]) > 250:
        bg = np.median(around[:, :3], axis=0)
        dist = np.abs(crop[:, :, :3] - bg).max(axis=2)
        top = dist.max()
        if top < 32:
            return np.zeros(dist.shape, np.uint8), (0, 0, 0)
        ink = np.clip(dist * 255.0 / top, 0, 255).astype(np.uint8)
        strong = crop[dist >= 0.8 * top][:, :3]
    else:
        ink = crop[:, :, 3].astype(np.uint8)
        strong = crop[crop[:, :, 3] > 200][:, :3]
    colour = tuple(int(v) for v in np.median(strong, axis=0)) if len(strong) else (0, 0, 0)
    return ink, colour


def erase_box(img: Image.Image, box: tuple[int, int, int, int], ring: int = 3) -> Image.Image:
    """Remove the pixels in ``box``.

    On a transparent texture (a text box) the box becomes transparent. On an
    opaque one (a flattened slide) it is filled with the median colour of the
    ring of pixels around it, so no hole shows through to the canvas.
    """
    arr = np.asarray(img.convert("RGBA")).copy()
    x1, y1, x2, y2 = box
    around = _ring(arr, box, ring)
    if len(around) and np.median(around[:, 3]) > 250:
        arr[y1:y2, x1:x2, :3] = np.median(around[:, :3], axis=0).astype(np.uint8)
        arr[y1:y2, x1:x2, 3] = 255
    else:
        arr[y1:y2, x1:x2, 3] = 0
    return Image.fromarray(arr, "RGBA")


def _depth(slide: Slide, owner: Leaf, sprite: Sprite, compositor: Compositor) -> str:
    """'texture' unless an opaque layer above the owner would hide the text."""
    ink = np.asarray(sprite.image.getchannel("A")) > INK_THRESHOLD
    total = int(ink.sum())
    if total == 0:
        return "texture"
    worst = 0.0
    for state in slide.states:
        leaves = state.leaves()
        mine = [lf for lf in leaves if lf.texture == owner.texture and lf.visible]
        if not mine:
            continue
        order = mine[0].order
        above = compositor.composite(
            leaves, background=None, include=lambda lf, o=order: lf.order > o, top_sprites=False
        )
        a = np.zeros(ink.shape, np.uint8)
        x0, y0 = sprite.x, sprite.y
        cw, ch = above.size
        sx1, sy1 = max(0, x0), max(0, y0)
        sx2, sy2 = min(cw, x0 + ink.shape[1]), min(ch, y0 + ink.shape[0])
        if sx2 > sx1 and sy2 > sy1:
            region = np.asarray(above.getchannel("A"))[sy1:sy2, sx1:sx2]
            a[sy1 - y0 : sy2 - y0, sx1 - x0 : sx2 - x0] = region
        # off-canvas ink counts as hidden too
        hidden = np.logical_and(ink, a >= 250).sum()
        worst = max(worst, hidden / total)
    return "top" if worst > 0.002 else "texture"


def install(compositor: Compositor, applied: list[AppliedPatch]) -> None:
    """Register patches, re-rendering fonts at the compositor's target density.

    Layout and font fitting are decided once at 1x. Higher densities keep that
    geometry and erase the corresponding pixels of a fresh vector PDF render.
    """
    for p in applied:
        if compositor.scale == 1:
            erased, sprite = p.erased, p.sprite
        else:
            scale = compositor.scale
            base = (
                compositor.overrides[p.texture][0]
                if p.texture in compositor.overrides
                else compositor.pages.page(p.texture_pdf, p.texture_page, scale)
            )
            bounds = tuple(v * scale for v in p.erase_bounds)
            erased = erase_box(base, bounds, ring=3 * scale)
            sprite = draw_layout(p.layout_plan, p.font_path, p.colour, scale)
        compositor.overrides[p.texture] = (erased, 0, 0)
        target = compositor.after if p.mode == "texture" else compositor.on_top
        target.setdefault(p.texture, []).append(sprite)


def plan_slide(
    slide: Slide, specs: list[PatchSpec], compositor: Compositor, canvas_w: int, canvas_h: int
) -> list[AppliedPatch]:
    applied: list[AppliedPatch] = []
    for spec in specs:
        # Install earlier patches first so later ones see the erased textures
        # and avoid the replacements already placed.
        p = plan_patch(slide, spec, compositor, canvas_w, canvas_h, [a.box for a in applied])
        applied.append(p)
        install(compositor, [p])
    return applied
