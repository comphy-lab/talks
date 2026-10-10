"""Rectangle maths in canvas pixels (origin top-left, y down).

Keynote layer geometry is a Core Animation tree: every layer has a
``position`` (in its parent's coordinate space), ``width``/``height`` and an
``anchorPoint`` in unit coordinates. With identity transforms, which is all
this exporter emits for slide content, a layer's top-left corner is

    origin = parent_origin + position - size * anchor.

Rounding is deliberately two-stage: first to 0.1 px, which removes float
noise between copies of the same object on different slides (for example
1347.6999 and 1347.7001), then half-up to an integer. Identical inputs
therefore always give identical integer rectangles, which is what the
player's placement key relies on.

Integer rectangles round their *edges*, not their size: ``x0 = snap(x)``,
``x1 = snap(x + w)``, ``w = x1 - x0``. Rounding the width on its own can
move the far edge by a whole pixel (x = 132.7, w = 891.6 would cover
133..1025 instead of Keynote's 132.7..1024.3), so a video would be stretched
by a pixel against the poster frame baked into the still, or leave a row of
that static poster showing beside the moving picture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def snap(value: float) -> int:
    """Round to 0.1 px, then half-up to an integer pixel."""
    tenth = math.floor(value * 10.0 + 0.5) / 10.0
    return int(math.floor(tenth + 0.5))


def snap_tenth(value: float) -> float:
    """Round to 0.1 px (half-up), returning a float."""
    return math.floor(value * 10.0 + 0.5) / 10.0


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle ``(x, y, w, h)`` in canvas pixels."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @classmethod
    def from_layer_state(cls, state: dict, parent_x: float = 0.0, parent_y: float = 0.0) -> Rect:
        """Absolute rectangle of a layer from its ``initialState``."""
        w = float(state.get("width", 0.0))
        h = float(state.get("height", 0.0))
        pos = state.get("position", {})
        anchor = state.get("anchorPoint", {})
        ax = float(anchor.get("pointX", 0.5))
        ay = float(anchor.get("pointY", 0.5))
        x = parent_x + float(pos.get("pointX", 0.0)) - w * ax
        y = parent_y + float(pos.get("pointY", 0.0)) - h * ay
        return cls(x, y, w, h)

    @classmethod
    def from_corners(cls, x1: float, y1: float, x2: float, y2: float) -> Rect:
        return cls(x1, y1, x2 - x1, y2 - y1)

    def intersection(self, other: Rect) -> Rect | None:
        x1 = max(self.x, other.x)
        y1 = max(self.y, other.y)
        x2 = min(self.x2, other.x2)
        y2 = min(self.y2, other.y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def intersects(self, other: Rect) -> bool:
        return self.intersection(other) is not None

    def inflate(self, margin: float) -> Rect:
        return Rect(self.x - margin, self.y - margin, self.w + 2 * margin, self.h + 2 * margin)

    def union(self, other: Rect) -> Rect:
        x1 = min(self.x, other.x)
        y1 = min(self.y, other.y)
        x2 = max(self.x2, other.x2)
        y2 = max(self.y2, other.y2)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def contains(self, other: Rect) -> bool:
        return other.x >= self.x and other.y >= self.y and other.x2 <= self.x2 and other.y2 <= self.y2

    def snapped(self) -> tuple[int, int, int, int]:
        """Integer ``(x, y, w, h)`` for the manifest and placement keys.

        Each edge is snapped (see the module docstring), so every edge lies
        within 0.55 px (0.05 to the tenth, then 0.5) of the exact one.
        """
        x0, y0 = snap(self.x), snap(self.y)
        return (x0, y0, snap(self.x + self.w) - x0, snap(self.y + self.h) - y0)

    def tenths(self) -> tuple[float, float, float, float]:
        return (snap_tenth(self.x), snap_tenth(self.y), snap_tenth(self.w), snap_tenth(self.h))

    def pixel_box(self) -> tuple[int, int, int, int]:
        """Integer ``(left, top, right, bottom)`` box covering the snapped rect."""
        x, y, w, h = self.snapped()
        return (x, y, x + w, y + h)


def even_floor(value: float, minimum: int = 2) -> int:
    """Largest even integer not above ``value`` (at least ``minimum``)."""
    v = int(math.floor(value + 1e-9))
    v -= v % 2
    return max(minimum, v)


def scaled_video_size(src_w: int, src_h: int, boxes: list[tuple[float, float]], max_scale: float) -> tuple[int, int]:
    """Encoded video size from the source size and its on-slide boxes.

    The output is at most ``max_scale`` times the largest box the source
    occupies (per axis, so a box of a different aspect ratio never makes the
    video smaller than either box dimension times ``max_scale`` allows), never
    larger than the source, and has even dimensions.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source size must be positive")
    need = 0.0
    for bw, bh in boxes:
        need = max(need, bw / src_w, bh / src_h)
    factor = 1.0 if not boxes else min(1.0, max_scale * need)
    return even_floor(src_w * factor), even_floor(src_h * factor)
