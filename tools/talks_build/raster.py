"""Rasterise texture-atlas pages and composite layer trees.

Texture pages are vector PDF pages whose size in points equals the layer
size in canvas pixels, so rendering at 72 or 144 dpi gives one or two image
pixels per point. Pages are rendered with ``pdftocairo -transp`` to keep their
alpha. Logical geometry is snapped before multiplying by the render scale.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .geometry import Rect, snap
from .keynote import Layer, Leaf, leaves_of
from .proc import run


class PageCache:
    """Render PDF pages once per build; keep them on disk, decode on demand."""

    def __init__(self, workdir: Path | None = None):
        if workdir is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="talks-build-pages-")
            workdir = Path(self._tmp.name)
        self.dir = Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[tuple[str, int, int], Image.Image] = {}
        self._pdf_digest: dict[str, str] = {}

    def _key(self, pdf: Path, page: int, scale: int = 1) -> str:
        """Cache key from the PDF's content (not its path) and the page."""
        pdf = Path(pdf)
        digest = self._pdf_digest.get(str(pdf))
        if digest is None:
            h = hashlib.sha256()
            with open(pdf, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            digest = self._pdf_digest[str(pdf)] = h.hexdigest()[:24]
        return f"{digest}-{page:04d}-{72 * scale}"

    def page(self, pdf: Path, page_index: int, scale: int = 1) -> Image.Image:
        """RGBA render of 0-based ``page_index`` at ``72 * scale`` dpi."""
        if type(scale) is not int or scale not in (1, 2):
            raise ValueError("render scale must be 1 or 2")
        mkey = (str(pdf), page_index, scale)
        if mkey in self._mem:
            return self._mem[mkey]
        stem = self.dir / self._key(pdf, page_index, scale)
        png = stem.with_suffix(".png")
        if not png.exists():
            stem = self.dir / f".tmp-{os.getpid()}-{threading.get_ident()}-{stem.name}"
            run(
                [
                    "pdftocairo",
                    "-png",
                    "-transp",
                    "-r",
                    str(72 * scale),
                    "-f",
                    str(page_index + 1),
                    "-l",
                    str(page_index + 1),
                    "-singlefile",
                    str(pdf),
                    str(stem),
                ]
            )
            stem.with_suffix(".png").replace(png)
        with Image.open(png) as im:
            img = im.convert("RGBA")
            img.load()
        self._mem[mkey] = img
        return img

    def forget(self) -> None:
        """Drop decoded pages from memory (the PNG cache stays on disk)."""
        self._mem.clear()


@dataclass
class Sprite:
    """An image at the compositor's density, positioned in logical canvas pixels."""

    image: Image.Image
    x: int
    y: int


def paste(canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """Alpha-composite ``img`` with its top-left at ``(x, y)``, clipping to the canvas."""
    cw, ch = canvas.size
    iw, ih = img.size
    left, top = max(0, x), max(0, y)
    right, bottom = min(cw, x + iw), min(ch, y + ih)
    if right <= left or bottom <= top:
        return
    crop = img.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(crop, (left, top))


def with_opacity(img: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1.0:
        return img
    a = np.asarray(img.getchannel("A"), dtype=np.float32) * float(opacity)
    out = img.copy()
    out.putalpha(Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "L"))
    return out


class Compositor:
    """Draws leaves of a layer tree; textures may be substituted (patches)."""

    def __init__(self, slide, pages: PageCache, width: int, height: int, scale: int = 1):
        if type(scale) is not int or scale not in (1, 2):
            raise ValueError("render scale must be 1 or 2")
        self.slide = slide
        self.pages = pages
        self.width = width
        self.height = height
        self.scale = scale
        self.size = (width * scale, height * scale)
        # texture id -> (image, dx, dy) replacing the page render; dx/dy shift
        # the image relative to the layer origin (patched textures may grow).
        self.overrides: dict[str, tuple[Image.Image, int, int]] = {}
        # texture id -> sprites drawn immediately after that texture
        self.after: dict[str, list[Sprite]] = {}
        # sprites drawn above everything when their owning texture is visible
        self.on_top: dict[str, list[Sprite]] = {}

    def texture_image(self, leaf: Leaf) -> tuple[Image.Image, int, int]:
        if leaf.texture in self.overrides:
            return self.overrides[leaf.texture]
        img = self.pages.page(self.slide.texture_pdf(leaf.texture), self.slide.texture_page(leaf.texture), self.scale)
        w, h = snap(leaf.rect.w) * self.scale, snap(leaf.rect.h) * self.scale
        if abs(img.size[0] - w) > self.scale or abs(img.size[1] - h) > self.scale:
            img = img.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
        return img, 0, 0

    def draw_leaf(self, canvas: Image.Image, leaf: Leaf) -> None:
        img, dx, dy = self.texture_image(leaf)
        img = with_opacity(img, leaf.opacity)
        paste(canvas, img, (snap(leaf.rect.x) + dx) * self.scale, (snap(leaf.rect.y) + dy) * self.scale)
        for sprite in self.after.get(leaf.texture, []):
            paste(canvas, with_opacity(sprite.image, leaf.opacity), sprite.x * self.scale, sprite.y * self.scale)

    def composite(
        self,
        leaves: list[Leaf],
        *,
        background: tuple[int, int, int, int] | None = (255, 255, 255, 255),
        include=None,
        top_sprites: bool = True,
    ) -> Image.Image:
        """Paint visible ``leaves`` (optionally filtered by ``include``).

        Sprites registered in ``on_top`` are drawn above everything whenever
        their owning texture is visible in ``leaves``, even if ``include``
        skips the owner (so overlays above a movie keep lifted patch text).
        """
        bg = background if background is not None else (0, 0, 0, 0)
        canvas = Image.new("RGBA", self.size, bg)
        visible = [leaf for leaf in leaves if leaf.visible]
        for leaf in visible:
            if include is not None and not include(leaf):
                continue
            self.draw_leaf(canvas, leaf)
        if top_sprites:
            # Sprites lifted above every layer follow their owner's visibility.
            for leaf in visible:
                for sprite in self.on_top.get(leaf.texture, []):
                    paste(canvas, with_opacity(sprite.image, leaf.opacity), sprite.x * self.scale, sprite.y * self.scale)
        return canvas

    def flattened(self, tree: Layer) -> Image.Image:
        """Render a flattened (single full-canvas texture) tree."""
        return self.composite(leaves_of(tree))


def alpha_bbox(img: Image.Image, threshold: int = 8) -> tuple[int, int, int, int] | None:
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.nonzero(a > threshold)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def ink_rect(img: Image.Image, origin_x: int, origin_y: int, threshold: int = 8) -> Rect | None:
    box = alpha_bbox(img, threshold)
    if box is None:
        return None
    x1, y1, x2, y2 = box
    return Rect.from_corners(origin_x + x1, origin_y + y1, origin_x + x2, origin_y + y2)


def image_diff(a: Image.Image, b: Image.Image) -> dict:
    """Simple RGB difference statistics between two same-size images."""
    x = np.asarray(a.convert("RGB"), dtype=np.int16)
    y = np.asarray(b.convert("RGB"), dtype=np.int16)
    d = np.abs(x - y).max(axis=2)
    return {
        "mean": float(d.mean()),
        "frac_gt32": float((d > 32).mean()),
        "frac_gt96": float((d > 96).mean()),
    }
