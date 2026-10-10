"""Helpers shared by the browser tests: manifest access, page driver, probes.

The talk page's CSP forbids ``eval``, so Playwright's ``wait_for_function``
cannot poll there; :func:`wait_until` polls ``page.evaluate`` instead
(evaluation through the automation protocol is not subject to the page CSP).
"""

from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

CANVAS_W, CANVAS_H = 1920, 1080

# Collect CSP violations from the first script that runs in each document,
# and report each one to Python at once (so none is lost on navigation).
INIT_JS = """
(() => {
  window.__csp = [];
  document.addEventListener('securitypolicyviolation', (e) => {
    const v = { directive: e.violatedDirective, blocked: e.blockedURI, sample: e.sample,
                source: e.sourceFile, line: e.lineNumber, document: e.documentURI };
    window.__csp.push(v);
    if (typeof window.__reportCsp === 'function') window.__reportCsp(v);
  });
})();
"""


# ----------------------------------------------------------------- manifest


def placement_key(m: dict) -> str:
    """SPEC 3.2: kind + ref + rect rounded to 1 px (the player's format)."""
    return f"{m['kind']}:{m['ref']}:" + ",".join(str(round(v) + 0) for v in m["rect"])


@dataclass(frozen=True)
class Step:
    index: int  # 0-based position in the flat sequence of click states
    n: int  # 1-based slide number
    k: int  # 0-based state within the slide
    slide: dict
    state: dict

    @property
    def keys(self) -> list[str]:
        out: list[str] = []
        for m in self.state["media"]:
            key = placement_key(m)
            if key not in out:
                out.append(key)
        return out

    @property
    def video_keys(self) -> list[str]:
        return [k for k in self.keys if k.startswith("video:")]

    @property
    def label(self) -> str:
        """``<slide>.<state>``, the state counted from 1 as in deep links and screenshot names."""
        return f"{self.n}.{self.k + 1}"


@dataclass
class Talk:
    slug: str
    manifest: dict
    media_dir: Path
    steps: list[Step] = field(default_factory=list)

    @classmethod
    def load(cls, site_dir: Path, media_dir: Path, slug: str) -> Talk:
        manifest = json.loads((site_dir / slug / "manifest.json").read_text(encoding="utf-8"))
        talk = cls(slug=slug, manifest=manifest, media_dir=media_dir)
        i = 0
        for slide in manifest["slides"]:
            for k, state in enumerate(slide["states"]):
                talk.steps.append(Step(i, slide["n"], k, slide, state))
                i += 1
        return talk

    def slide(self, n: int) -> dict:
        return self.manifest["slides"][n - 1]

    def step(self, n: int, k: int = 0) -> Step:
        for s in self.steps:
            if s.n == n and s.k == k:
                return s
        raise KeyError(f"no state {n}.{k}")

    def prefix(self, n: int) -> str:
        return "".join(c for c in self.slide(n)["id"] if c in "0123456789abcdefABCDEF")[:8].lower()

    def hash_for(self, n: int, k: int = 0) -> str:
        """Deep link: the state suffix counts from 1 and is omitted for the first state."""
        return f"#{self.prefix(n)}" if k == 0 else f"#{self.prefix(n)}.{k + 1}"

    def by_label(self, label: str) -> Step:
        """The step for a label such as ``"4.2"`` (state counted from 1)."""
        n, k = (int(p) for p in label.split("."))
        return self.step(n, k - 1)

    def media_path(self, name: str) -> Path:
        return self.media_dir / name

    def expected_still(self, step: Step) -> np.ndarray:
        """What the stage should show outside the media boxes: ``image`` with ``overlay`` on top (RGB)."""
        with Image.open(self.media_path(step.state["image"])) as im:
            canvas = im.convert("RGBA")
        if step.state["overlay"]:
            with Image.open(self.media_path(step.state["overlay"])) as ov:
                canvas.alpha_composite(ov.convert("RGBA"))
        return np.asarray(canvas.convert("RGB"))

    def rgba(self, name: str) -> np.ndarray:
        with Image.open(self.media_path(name)) as im:
            return np.asarray(im.convert("RGBA"))

    def duration(self, ref: str) -> float:
        return float(self.manifest["videos"][ref]["duration"])


# --------------------------------------------------------------------- page


class Timeout(AssertionError):
    pass


def wait_until(page, js: str, arg: Any = None, timeout: float = 10.0, interval: float = 0.05):
    """Poll ``page.evaluate(js, arg)`` until it returns a truthy value."""
    deadline = time.monotonic() + timeout
    while True:
        value = page.evaluate(js, arg)
        if value:
            return value
        if time.monotonic() > deadline:
            raise Timeout(f"timed out after {timeout:.1f} s waiting for: {js.strip()[:200]} (last value {value!r})")
        page.wait_for_timeout(int(interval * 1000))


STATE_JS = "() => window.__talk ? window.__talk.state() : null"

STILLS_JS = """
() => {
  const top = (id) => {
    const imgs = [...document.querySelectorAll(`#${id} > img`)];
    const img = imgs[imgs.length - 1];
    if (!img) return { count: 0 };
    const cs = getComputedStyle(img);
    return { count: imgs.length, src: img.getAttribute('src'), complete: img.complete,
             w: img.naturalWidth, h: img.naturalHeight, broken: img.classList.contains('is-broken'),
             visible: cs.visibility === 'visible' && cs.display !== 'none', opacity: Number(cs.opacity) };
  };
  return { image: top('layer-image'), overlay: top('layer-overlay') };
}
"""

MEDIA_JS = """
() => window.__talk.media().map(({ key, el }) => {
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const base = { key, tag: el.tagName.toLowerCase(), connected: el.isConnected,
                 rect: [r.left, r.top, r.width, r.height], visibility: cs.visibility,
                 display: cs.display, opacity: Number(cs.opacity) };
  if (el.tagName !== 'VIDEO') {
    const frame = el.querySelector('iframe');
    return { ...base, iframe: frame ? frame.src : null, fallback: el.classList.contains('is-fallback') };
  }
  return { ...base, src: el.currentSrc || el.src, poster: el.poster, paused: el.paused, t: el.currentTime,
           readyState: el.readyState, videoWidth: el.videoWidth, failed: el.classList.contains('is-failed'),
           error: el.error ? el.error.code : null, loop: el.loop, muted: el.muted, defaultMuted: el.defaultMuted,
           mutedAttr: el.hasAttribute('muted'), playsInline: el.playsInline !== false && el.hasAttribute('playsinline'),
           controls: el.controls, preload: el.preload, noPip: el.hasAttribute('disablepictureinpicture'),
           autoplayAttr: el.hasAttribute('autoplay') };
})
"""

VIDEO_COUNT_JS = "() => document.querySelectorAll('video').length"

STAGE_RECT_JS = """
() => { const r = document.getElementById('stage').getBoundingClientRect();
        return [r.left, r.top, r.width, r.height]; }
"""

# Make every stage layer hit-testable so elementFromPoint reports the paint
# order. Production keeps stills, videos and the overlay `pointer-events:
# none` so that clicks fall through to the stage's navigation handler.
HIT_TEST_JS = """
({ points, includeOverlay }) => {
  const stage = document.getElementById('stage');
  const sr = stage.getBoundingClientRect();
  const sx = sr.width / 1920, sy = sr.height / 1080;
  const touched = [];
  const enable = (node) => { touched.push([node, node.style.pointerEvents]); node.style.pointerEvents = 'auto'; };
  stage.querySelectorAll('.still--image, video, iframe').forEach(enable);
  // Only painted elements: the layer <div>s are empty full-canvas boxes.
  if (includeOverlay) document.querySelectorAll('#layer-overlay > img').forEach(enable);
  const keyOf = new Map(window.__talk.media().map(({ key, el }) => [el, key]));
  const describe = (node) => {
    if (!node) return 'none';
    if (keyOf.has(node)) return keyOf.get(node);
    if (node.tagName === 'IFRAME' && keyOf.has(node.parentElement)) return keyOf.get(node.parentElement) + ':iframe';
    if (node.closest('#layer-overlay')) return 'overlay';
    if (node.closest('#layer-image')) return 'image';
    if (node.closest('#stage-ui')) return 'ui:' + node.className;
    return `other:${node.tagName.toLowerCase()}#${node.id}.${node.className}`;
  };
  try {
    return points.map(([x, y]) =>
      describe(document.elementFromPoint(sr.left + (x + 0.5) * sx, sr.top + (y + 0.5) * sy)));
  } finally {
    for (const [node, value] of touched) node.style.pointerEvents = value;
  }
}
"""

# Read the current frame of each mounted video, scaled to its placement
# rectangle, as RGBA bytes (same-origin media, so the canvas is not tainted).
VIDEO_FRAMES_JS = """
() => window.__talk.media().filter(({ el }) => el.tagName === 'VIDEO').map(({ key, el }) => {
  const [x, y, w, h] = key.split(':')[2].split(',').map(Number);
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(el, 0, 0, w, h);
  const data = g.getImageData(0, 0, w, h).data;
  let bin = '';
  for (let i = 0; i < data.length; i += 0x8000) bin += String.fromCharCode.apply(null, data.subarray(i, i + 0x8000));
  return { key, rect: [x, y, w, h], t: el.currentTime, b64: btoa(bin) };
})
"""


@dataclass
class TalkPage:
    """One browser page on the talk, with everything it logged."""

    page: Any
    base: str
    talk: Talk
    engine: str
    console: list[tuple[str, str, str]] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    csp_events: list[dict] = field(default_factory=list)
    ignored_csp: set[int] = field(default_factory=set)
    ignored_console: int = 0

    # ---- navigation
    def url(self, hash_: str = "", media: bool = True) -> str:
        query = f"?media=/media/{self.talk.slug}/" if media else ""
        return f"{self.base}/talks/{self.talk.slug}/{query}{hash_}"

    def goto(self, hash_: str = "", media: bool = True, wait: bool = True) -> dict | None:
        self.page.goto(self.url(hash_, media))
        if not wait:
            return None
        return wait_until(self.page, STATE_JS, timeout=15)

    def state(self) -> dict | None:
        return self.page.evaluate(STATE_JS)

    def wait_state(self, n: int, k: int = 0, timeout: float = 10.0) -> dict:
        return wait_until(
            self.page,
            "([n, k]) => { const s = window.__talk && window.__talk.state();"
            " return s && s.slide === n && s.stateIndex === k ? s : null; }",
            [n, k],
            timeout=timeout,
        )

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    # ---- inspection
    def stills(self) -> dict:
        return self.page.evaluate(STILLS_JS)

    def media(self) -> list[dict]:
        return self.page.evaluate(MEDIA_JS)

    def videos(self) -> list[dict]:
        return [m for m in self.media() if m["tag"] == "video"]

    def stage_rect(self) -> list[float]:
        return self.page.evaluate(STAGE_RECT_JS)

    def media_url(self, name: str) -> str:
        """Absolute URL the player should request for a media file (the ?media= override)."""
        return f"{self.base}/media/{self.talk.slug}/{name}"

    def wait_stills(self, step: Step, timeout: float = 10.0) -> dict:
        """Wait until the still (and overlay) of `step` are the only, decoded, visible images."""
        want_image = self.media_url(step.state["image"])
        want_overlay = self.media_url(step.state["overlay"]) if step.state["overlay"] else None

        def ok() -> dict | None:
            s = self.stills()
            im, ov = s["image"], s["overlay"]
            good_image = (
                im["count"] == 1
                and im["src"] == want_image
                and im["complete"]
                and im["w"] == CANVAS_W
                and im["h"] == CANVAS_H
                and not im["broken"]
                and im["visible"]
                and im["opacity"] == 1
            )
            if want_overlay is None:
                good_overlay = ov["count"] == 0
            else:
                good_overlay = (
                    ov["count"] == 1
                    and ov["src"] == want_overlay
                    and ov["complete"]
                    and ov["w"] == CANVAS_W
                    and ov["h"] == CANVAS_H
                    and not ov["broken"]
                    and ov["visible"]
                    and ov["opacity"] == 1
                )
            return s if good_image and good_overlay else None

        deadline = time.monotonic() + timeout
        while True:
            s = ok()
            if s:
                return s
            if time.monotonic() > deadline:
                raise Timeout(f"{step.label}: stills not shown: {self.stills()} (want {want_image}, {want_overlay})")
            self.page.wait_for_timeout(50)

    def wait_videos_playing(self, min_time: float = 0.05, timeout: float = 10.0) -> list[dict]:
        def ok():
            vids = self.videos()
            if all(v["t"] > min_time and not v["paused"] and v["readyState"] >= 2 and not v["failed"] for v in vids):
                return vids or [{}]
            return None

        deadline = time.monotonic() + timeout
        while True:
            v = ok()
            if v:
                return [x for x in v if x]
            if time.monotonic() > deadline:
                raise Timeout(f"videos not playing: {self.videos()}")
            self.page.wait_for_timeout(50)

    def csp(self) -> list[dict]:
        """Every CSP violation reported so far, across navigations and frames."""
        self.page.wait_for_timeout(10)  # deliver pending binding calls
        return list(self.csp_events)

    def screenshot(self, **kwargs) -> bytes:
        """Screenshot that discounts the CSP report Playwright's own style injection causes.

        In WebKit, Playwright injects an inline <style> while it captures (for
        the caret); the page CSP rightly refuses it. Only violations with that
        signature raised during this call are discounted.
        """
        before_csp = len(self.csp())
        before_console = len(self.console)
        png = self.page.screenshot(caret="initial", **kwargs)
        self.page.wait_for_timeout(30)
        after = self.csp()
        new = after[before_csp:]
        artefact = [
            v for v in new if v["directive"] == "style-src-elem" and v["blocked"] == "inline" and not v["source"]
        ]
        if len(artefact) != len(new):
            return png  # something else happened: leave it for assert_clean
        self.ignored_csp.update(range(before_csp, len(after)))
        newer = self.console[before_console:]
        for msg in newer:
            if msg[0] == "error" and msg[1].startswith("Refused to apply a stylesheet") and "style-src" in msg[1]:
                self.console.remove(msg)
                self.ignored_console += 1
        return png

    def stage_pixels(self) -> np.ndarray:
        """The stage as an RGB array (canvas resolution when the stage is 1920 x 1080)."""
        x, y, w, h = self.stage_rect()
        png = self.screenshot(clip={"x": x, "y": y, "width": w, "height": h})
        with Image.open(io.BytesIO(png)) as im:
            return np.asarray(im.convert("RGB"))

    FILLS_JS = (
        "() => { const r = document.getElementById('stage').getBoundingClientRect();"
        " return Math.abs(r.width - innerWidth) < 1 && Math.abs(r.height - innerHeight) < 1"
        " && Math.abs(r.left) < 1 && Math.abs(r.top) < 1 ? [r.left, r.top, r.width, r.height] : null; }"
    )

    def enter_fullscreen(self) -> list[float]:
        """Press F (once); the stage then fills a 1920 x 1080 viewport, one CSS pixel per canvas pixel."""
        rect = self.page.evaluate(self.FILLS_JS)
        if rect:
            return rect
        self.press("f")
        return wait_until(self.page, self.FILLS_JS, timeout=5)

    def hit_test(self, points: list[tuple[int, int]], include_overlay: bool) -> list[str]:
        return self.page.evaluate(
            HIT_TEST_JS, {"points": [list(map(int, p)) for p in points], "includeOverlay": include_overlay}
        )

    def video_frames(self) -> dict[str, tuple[list[int], np.ndarray]]:
        import base64

        out = {}
        for item in self.page.evaluate(VIDEO_FRAMES_JS):
            x, y, w, h = item["rect"]
            arr = np.frombuffer(base64.b64decode(item["b64"]), dtype=np.uint8).reshape(h, w, 4)
            out[item["key"]] = (item["rect"], arr)
        return out

    def problems(self) -> list[str]:
        out = []
        out += [f"console {t}: {text} ({where})" for t, text, where in self.console if t == "error"]
        out += [f"page error: {e}" for e in self.page_errors]
        out += [f"CSP violation: {v}" for i, v in enumerate(self.csp()) if i not in self.ignored_csp]
        out += [f"HTTP error: {r}" for r in self.bad_responses]
        out += [f"unexpected external request: {u}" for u in self.external]
        return out

    def assert_clean(self) -> None:
        problems = self.problems()
        assert not problems, "\n".join(problems)


def canvas_to_client(stage_rect: list[float], x: float, y: float) -> tuple[float, float]:
    sx, sy, sw, sh = stage_rect
    return sx + (x + 0.5) * sw / CANVAS_W, sy + (y + 0.5) * sh / CANVAS_H


def visible_rect(rect: list[int]) -> tuple[int, int, int, int]:
    """Clip a placement rectangle to the canvas: (x0, y0, x1, y1), exclusive ends."""
    x, y, w, h = (round(v) for v in rect)
    return max(0, x), max(0, y), min(CANVAS_W, x + w), min(CANVAS_H, y + h)


def erode(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary erosion with a (2r+1)^2 square, without SciPy."""
    out = mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, r, constant_values=False)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out &= padded[r + dy : r + dy + h, r + dx : r + dx + w]
    return out


def smooth(rgb: np.ndarray, r: int = 2, tol: int = 12) -> np.ndarray:
    """Pixels whose (2r+1)^2 neighbourhood varies by at most `tol` per channel."""
    h, w, _ = rgb.shape
    lo = rgb.astype(np.int16).copy()
    hi = lo.copy()
    padded = np.pad(rgb.astype(np.int16), ((r, r), (r, r), (0, 0)), mode="edge")
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            win = padded[r + dy : r + dy + h, r + dx : r + dx + w]
            lo = np.minimum(lo, win)
            hi = np.maximum(hi, win)
    return ((hi - lo) <= tol).all(axis=2)


def sample(mask: np.ndarray, count: int, seed: int = 7) -> list[tuple[int, int]]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return []
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(xs), size=min(count, len(xs)), replace=False)
    return [(int(xs[i]), int(ys[i])) for i in sorted(idx)]
