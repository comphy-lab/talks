"""Videos on the stage.

(b) Slide 4: both videos are present, visible, uncovered except by the
    intended overlay, playing, and actually on screen (pixels).
(c) Span groups: a video on consecutive states/slides keeps the same element
    and its time keeps running.
(e) Overlays: labels and masks above a movie are drawn above the video.

Pixel checks run with the stage in full screen in a 1920 x 1080 viewport, so
one screenshot pixel is one canvas pixel. Video frames are read back through
a canvas (same-origin media) and compared with the screenshot. The seam test
runs on scaled stages instead, where the browser resamples the overlay.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from PIL import Image

from talk_helpers import Step, Talk, TalkPage, erode, sample, smooth, visible_rect, wait_until

FULL_HD = {"width": 1920, "height": 1080}

# Spans observed in the Keynote export: one source at one place on consecutive slides.
SPAN_GROUPS = [(21, 24), (34, 36), (37, 38), (106, 110)]


def open_state(open_talk, talk: Talk, n: int, k: int, **ctx) -> TalkPage:
    tp = open_talk(viewport=FULL_HD, **ctx)
    tp.goto(talk.hash_for(n, k))
    tp.wait_state(n, k)
    tp.wait_stills(talk.step(n, k))
    return tp


def pause_and_capture(tp: TalkPage) -> tuple[np.ndarray, dict]:
    """Full screen, pause, and return (screen RGB, {key: (rect, frame RGBA)})."""
    tp.enter_fullscreen()
    tp.wait_videos_playing(min_time=0.4)
    tp.press("p")
    wait_until(tp.page, "() => window.__talk.media().every((m) => m.el.tagName !== 'VIDEO' || m.el.paused)")
    tp.page.wait_for_timeout(300)  # let the compositor show the paused frame
    frames = tp.video_frames()
    screen = tp.stage_pixels()
    assert screen.shape == (1080, 1920, 3), f"stage is not 1920 x 1080 in full screen: {screen.shape}"
    return screen, frames


def frame_canvas(frames: dict) -> tuple[np.ndarray, np.ndarray]:
    """Compose the paused video frames on a canvas-sized array: (RGB, coverage mask), z-order kept."""
    rgb = np.zeros((1080, 1920, 3), dtype=np.uint8)
    have = np.zeros((1080, 1920), dtype=bool)
    for rect, arr in frames.values():
        x0, y0, x1, y1 = visible_rect(rect)
        fx, fy = x0 - rect[0], y0 - rect[1]
        rgb[y0:y1, x0:x1] = arr[fy : fy + (y1 - y0), fx : fx + (x1 - x0), :3]
        have[y0:y1, x0:x1] = True
    return rgb, have


def agreement(screen: np.ndarray, ref: np.ndarray, points: list[tuple[int, int]], tol: int) -> float:
    if not points:
        return 0.0
    ok = [int(np.abs(screen[y, x].astype(int) - ref[y, x].astype(int)).max()) <= tol for x, y in points]
    return sum(ok) / len(ok)


# ------------------------------------------------------------------- (b)


def test_slide4_both_videos_visible_and_playing(open_talk, talk: Talk, engine: str):
    slide4 = talk.slide(4)
    assert len(slide4["states"]) == 2
    step = talk.step(4, 1)
    refs = {m["ref"] for m in step.state["media"] if m["kind"] == "video"}
    assert len(refs) == 2, f"slide 4's last state should place two videos: {step.keys}"

    tp = open_state(open_talk, talk, 4, 1)
    tp.enter_fullscreen()
    vids = tp.wait_videos_playing(min_time=0.3)
    assert {v["key"] for v in vids} == set(step.video_keys)
    for v in vids:
        rect = [int(p) for p in v["key"].split(":")[2].split(",")]
        assert v["visibility"] == "visible" and v["display"] != "none" and v["opacity"] == 1, v
        assert v["videoWidth"] > 0 and v["readyState"] >= 2 and v["error"] is None and not v["failed"], v
        assert all(abs(a - b) <= 1 for a, b in zip(v["rect"], rect, strict=True)), (v["rect"], rect)

    # Time advances on both.
    t0 = {v["key"]: v["t"] for v in tp.videos()}
    tp.page.wait_for_timeout(800)
    t1 = {v["key"]: v["t"] for v in tp.videos()}
    for key in t0:
        assert t1[key] > t0[key] + 0.3, f"{key} not advancing: {t0[key]:.2f} -> {t1[key]:.2f}"

    # Nothing but the video itself is hit-tested inside its box (overlay excluded: it is
    # the one intended cover, checked by pixels below and in the overlay tests).
    overlay = talk.rgba(step.state["overlay"]) if step.state["overlay"] else None
    uncovered_share = {}
    for key in step.video_keys:
        rect = [int(p) for p in key.split(":")[2].split(",")]
        x0, y0, x1, y1 = visible_rect(rect)
        grid = [(x, y) for y in range(y0 + 3, y1 - 3, 24) for x in range(x0 + 3, x1 - 3, 24)]
        hits = tp.hit_test(grid, include_overlay=False)
        wrong = [(p, h) for p, h in zip(grid, hits, strict=True) if h != key]
        assert not wrong, f"{key}: covered at {wrong[:5]} ({len(wrong)} of {len(grid)} points)"
        if overlay is not None:
            alpha = overlay[y0:y1, x0:x1, 3]
            uncovered_share[key] = float((alpha == 0).mean())
        else:
            uncovered_share[key] = 1.0
        assert uncovered_share[key] > 0.2, f"{key}: overlay hides {1 - uncovered_share[key]:.0%} of the movie"

    # Pixels: where the overlay is transparent, the screen shows the video frame.
    screen, frames = pause_and_capture(tp)
    ref, have = frame_canvas(frames)
    clear = have & (overlay[..., 3] == 0 if overlay is not None else True)
    clear = erode(clear, 3) & smooth(ref, r=2, tol=10)
    for key in step.video_keys:
        rect = [int(p) for p in key.split(":")[2].split(",")]
        x0, y0, x1, y1 = visible_rect(rect)
        local = np.zeros_like(clear)
        local[y0:y1, x0:x1] = clear[y0:y1, x0:x1]
        points = sample(local, 300)
        assert len(points) >= 50, f"{key}: too few clear, smooth pixels to compare ({len(points)})"
        share = agreement(screen, ref, points, tol=24)
        assert share >= 0.9, f"{key}: screen matches the video frame at only {share:.0%} of {len(points)} points"
        print(f"\n[{engine}] {key}: uncovered {uncovered_share[key]:.0%} of box; screen = video frame at {share:.0%}")
    tp.assert_clean()


def test_videos_wrap_at_the_end(open_talk, talk: Talk, engine: str):
    """Every video loops: near its end it wraps to the start, same element, no `ended`, still playing."""
    step = talk.step(4, 1)
    tp = open_state(open_talk, talk, 4, 1)
    tp.wait_videos_playing(min_time=0.2)
    keys = step.video_keys
    tp.page.evaluate(
        """(keys) => {
          window.__wrap = {};
          for (const { key, el } of window.__talk.media()) {
            if (!keys.includes(key)) continue;
            const rec = { el, ended: 0, peak: 0 };
            el.addEventListener('ended', () => { rec.ended += 1; });
            el.addEventListener('timeupdate', () => { rec.peak = Math.max(rec.peak, el.currentTime); });
            window.__wrap[key] = rec;
            el.currentTime = Math.max(0, el.duration - 0.4);
          }
        }""",
        keys,
    )
    got = wait_until(
        tp.page,
        """(keys) => {
          const now = new Map(window.__talk.media().map(({ key, el }) => [key, el]));
          const out = keys.map((key) => { const r = window.__wrap[key]; const el = now.get(key);
            return { key, same: el === r.el, t: el.currentTime, peak: r.peak, paused: el.paused,
                     ended: r.ended, duration: el.duration }; });
          return out.every((o) => o.same && !o.paused && o.peak > o.duration - 0.5 && o.t > 0.05 && o.t < 1.5)
            ? out : null;
        }""",
        keys,
        timeout=8,
    )
    for o in got:
        assert o["ended"] == 0, f"{o['key']}: 'ended' fired, so it did not loop"
    tp.assert_clean()
    print(
        f"\n[{engine}] wrapped: "
        + ", ".join(f"{o['key']} {o['peak']:.2f}/{o['duration']:.2f} s -> {o['t']:.2f} s" for o in got)
    )


# ------------------------------------------------------------------- (c)


def runs_of(talk: Talk, key: str) -> list[list[Step]]:
    runs: list[list[Step]] = []
    for step in talk.steps:
        if key in step.keys:
            if runs and runs[-1][-1].index == step.index - 1:
                runs[-1].append(step)
            else:
                runs.append([step])
    return runs


def span_run(talk: Talk, first: int, last: int) -> tuple[str, list[Step]]:
    """The video key shared by slides first..last and its run of consecutive states."""
    common = None
    for n in range(first, last + 1):
        keys = {k for s in talk.steps if s.n == n for k in s.video_keys}
        common = keys if common is None else common & keys
    assert common, f"no video placement is shared by slides {first}-{last}"
    assert len(common) == 1, common
    key = common.pop()
    run = next(r for r in runs_of(talk, key) if any(s.n == first for s in r))
    return key, run


@pytest.mark.parametrize("first,last", SPAN_GROUPS, ids=[f"slides{a}-{b}" for a, b in SPAN_GROUPS])
def test_span_group_keeps_playing(open_talk, talk: Talk, engine: str, first: int, last: int):
    key, run = span_run(talk, first, last)
    assert run[0].n == first and run[-1].n == last, f"span of {key}: {[s.label for s in run]}"
    duration = talk.duration(key.split(":")[1])

    tp = open_talk()
    tp.goto(talk.hash_for(run[0].n, run[0].k))
    tp.wait_state(run[0].n, run[0].k)
    tp.wait_videos_playing(min_time=0.3)
    tp.page.evaluate(
        """(key) => {
          const el = window.__talk.media().find((m) => m.key === key).el;
          window.__spanEl = el;
          window.__spanEvents = [];
          for (const t of ['emptied', 'loadstart', 'abort', 'seeking', 'ended'])
            el.addEventListener(t, () => window.__spanEvents.push(t));
        }""",
        key,
    )
    probe = """(key) => { const m = window.__talk.media().find((x) => x.key === key);
      return m ? { same: m.el === window.__spanEl, connected: m.el.isConnected, paused: m.el.paused,
                   t: m.el.currentTime, events: window.__spanEvents.slice() } : null; }"""
    trace = [f"{run[0].label} t={tp.page.evaluate(probe, key)['t']:.2f}"]
    last_t = tp.page.evaluate(probe, key)["t"]

    def step_to(step: Step, key_name: str) -> None:
        nonlocal last_t
        tp.page.wait_for_timeout(500)
        before = tp.page.evaluate(probe, key)["t"]
        tp.press(key_name)
        tp.wait_state(step.n, step.k)
        got = tp.page.evaluate(probe, key)
        assert got is not None, f"{step.label}: {key} not mounted"
        assert got["same"], f"{step.label}: {key} element was replaced"
        assert got["connected"] and not got["paused"], f"{step.label}: {got}"
        assert not got["events"], f"{step.label}: media events {got['events']} (reload, seek or loop)"
        assert got["t"] >= before - 0.01, f"{step.label}: time went back {before:.2f} -> {got['t']:.2f}"
        assert got["t"] > 0.25, f"{step.label}: time reset to {got['t']:.2f}"
        last_t = got["t"]
        trace.append(f"{step.label} t={got['t']:.2f}")

    for step in run[1:]:
        step_to(step, "ArrowRight")
    # Back and forth inside the run keeps the element too.
    if len(run) >= 2:
        step_to(run[-2], "ArrowLeft")
        step_to(run[-1], "ArrowRight")
    assert last_t < duration, "test window exceeded one loop; time checks would be ambiguous"

    # Leaving the run detaches and pauses the element.
    after = talk.steps[run[-1].index + 1]
    tp.press("ArrowRight")
    tp.wait_state(after.n, after.k)
    gone = tp.page.evaluate("() => ({ connected: window.__spanEl.isConnected, paused: window.__spanEl.paused })")
    assert gone == {"connected": False, "paused": True}, gone
    tp.assert_clean()
    print(f"\n[{engine}] {key} slides {first}-{last}: " + " -> ".join(trace))


# ------------------------------------------------------------------- (e)


def overlay_steps(talk: Talk) -> list[Step]:
    return [s for s in talk.steps if s.state["overlay"]]


def pytest_generate_tests(metafunc):
    """Parametrise the overlay test with the manifest's overlay states."""
    if "overlay_label" in metafunc.fixturenames:
        cfg = metafunc.config
        slug = cfg.getoption("--slug")
        try:
            labels = [
                s.label
                for s in overlay_steps(
                    Talk.load(cfg.getoption("--site"), cfg.getoption("--build") / slug / "media", slug)
                )
            ]
        except FileNotFoundError:
            labels = []
        metafunc.parametrize("overlay_label", labels or ["none"])


def test_overlay_slides_expected(talk: Talk):
    """Labels and masks sit above movies on slides 2, 4 and 5; YouTube slides never get one."""
    slides = {s.n for s in overlay_steps(talk)}
    assert {2, 4, 5} <= slides, slides
    for s in overlay_steps(talk):
        assert s.video_keys, f"{s.label}: overlay without a video"
        assert not [k for k in s.keys if k.startswith("youtube:")], f"{s.label}: overlay on a YouTube state"


def test_overlay_drawn_above_video(open_talk, talk: Talk, engine: str, overlay_label: str):
    if overlay_label == "none":
        pytest.skip("no overlay states in this talk")
    step = talk.by_label(overlay_label)
    n, k = step.n, step.k
    overlay = talk.rgba(step.state["overlay"])
    tp = open_state(open_talk, talk, n, k)

    # Z-order of the layers themselves.
    z = tp.page.evaluate(
        "() => ['layer-image', 'layer-media', 'layer-overlay', 'stage-ui']"
        ".map((id) => Number(getComputedStyle(document.getElementById(id)).zIndex))"
    )
    assert z == sorted(z) and len(set(z)) == 4, f"layer z-order {z}"

    screen, frames = pause_and_capture(tp)
    ref, have = frame_canvas(frames)
    ov_rgb = overlay[..., :3]
    # Opaque overlay pixels away from edges (1 px), inside a mounted video.
    label = erode(overlay[..., 3] == 255, 1) & smooth(ov_rgb, r=1, tol=8) & have
    # Pixels where the overlay and the paused frame differ: the screen tells which is on top.
    differs = np.abs(ov_rgb.astype(int) - ref.astype(int)).max(axis=2) > 60
    points = sample(label & differs & smooth(ref, r=1, tol=12), 250)
    assert len(points) >= 15, f"{step.label}: only {len(points)} label pixels differ from the video frame"

    hits_with = tp.hit_test(points, include_overlay=True)
    assert set(hits_with) == {"overlay"}, f"{step.label}: elementFromPoint at label pixels: {sorted(set(hits_with))}"
    hits_without = tp.hit_test(points, include_overlay=False)
    assert all(h.startswith("video:") for h in hits_without), f"{step.label}: below the label: {set(hits_without)}"

    on_top = agreement(screen, ov_rgb, points, tol=30)
    assert on_top >= 0.95, f"{step.label}: screen shows the overlay at only {on_top:.0%} of label pixels"

    # Sanity: with the overlay hidden the same pixels show the video, so the check above can fail.
    tp.page.evaluate("() => { document.getElementById('layer-overlay').style.visibility = 'hidden'; }")
    tp.page.wait_for_timeout(200)
    bare = tp.stage_pixels()
    tp.page.evaluate("() => { document.getElementById('layer-overlay').style.visibility = ''; }")
    shows_video = agreement(bare, ref, points, tol=30)
    assert shows_video >= 0.8, f"{step.label}: without the overlay the video shows at only {shows_video:.0%}"
    tp.assert_clean()
    print(
        f"\n[{engine}] {step.label}: {len(points)} label pixels; overlay on screen {on_top:.0%}; "
        f"video there once the overlay is hidden {shows_video:.0%}"
    )


# Scaled stages: a laptop window, a Retina window, a phone (device pixels per
# CSS pixel as the devices report them).
SCALED_VIEWS = [
    ("1366x768", {"viewport": {"width": 1366, "height": 768}, "device_scale_factor": 1}),
    ("1440x900@2", {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}),
    ("412x915@2.625", {"viewport": {"width": 412, "height": 915}, "device_scale_factor": 2.625, "has_touch": True}),
]
# A screen pixel whose colour moves by more than this between black and white
# videos shows the video (here: by more than about 16 %).
SEAM_TOLERANCE = 40

# Paint every video solid black or solid white (same layers, same compositing).
VIDEOS_STYLE_JS = """(mode) => { for (const { el } of window.__talk.media()) {
  if (el.tagName !== 'VIDEO') continue;
  el.style.filter = { black: 'brightness(0)', white: 'brightness(0) invert(1)' }[mode] || '';
} }"""


def _device_stage(tp: TalkPage) -> np.ndarray:
    x, y, w, h = tp.stage_rect()
    png = tp.screenshot(clip={"x": x, "y": y, "width": w, "height": h})
    with Image.open(io.BytesIO(png)) as im:
        return np.asarray(im.convert("RGB")).astype(np.int16)


def test_overlay_has_no_seams_when_scaled(open_talk, talk: Talk, engine: str, overlay_label: str):
    """Where the overlay covers a movie, the movie never shows at any stage size.

    With the stage scaled, the browser blends the overlay's edge pixels with
    whatever lies beyond them. The stage is captured with every video painted
    solid black and then solid white: away from the places where a video is
    meant to be seen, the two must agree, or the video shows through.
    """
    if overlay_label == "none":
        pytest.skip("no overlay states in this talk")
    step = talk.by_label(overlay_label)
    alpha = talk.rgba(step.state["overlay"])[..., 3]
    seen = np.zeros(alpha.shape, dtype=bool)  # canvas pixels where a video is meant to show
    for key in step.video_keys:
        x0, y0, x1, y1 = visible_rect([int(p) for p in key.split(":")[2].split(",")])
        seen[y0:y1, x0:x1] |= alpha[y0:y1, x0:x1] < 255
    report = []
    for name, options in SCALED_VIEWS:
        tp = open_talk(**options)
        tp.goto(talk.hash_for(step.n, step.k))
        tp.wait_state(step.n, step.k)
        tp.wait_stills(step)
        tp.wait_videos_playing(min_time=0.1)
        tp.page.evaluate(VIDEOS_STYLE_JS, "black")
        tp.page.wait_for_timeout(300)
        # Discarded: WebKit's first capture of a page can land one device pixel
        # off the later ones when the stage sits at a fractional offset, which
        # would read as a difference across the whole slide.
        _device_stage(tp)
        black = _device_stage(tp)
        tp.page.evaluate(VIDEOS_STYLE_JS, "white")
        tp.page.wait_for_timeout(300)
        white = _device_stage(tp)
        tp.page.evaluate(VIDEOS_STYLE_JS, "")
        h, w = black.shape[:2]
        scale = w / 1920  # device pixels per canvas pixel
        # Leave out what lies within reach of the resampling filter of a visible video pixel.
        reach = math.ceil(3 / scale) + 2
        near = erode(~seen, reach)
        allowed = np.asarray(Image.fromarray((~near).astype(np.float32)).resize((w, h), Image.Resampling.BOX)) > 0
        diff = np.abs(white - black).max(axis=2)
        bad = (diff > SEAM_TOLERANCE) & ~allowed
        # Sanity: the black videos are on screen where they are meant to show.
        shown = np.asarray(Image.fromarray(erode(seen, 4).astype(np.float32)).resize((w, h), Image.Resampling.BOX))
        assert (diff[shown >= 1] > SEAM_TOLERANCE).mean() > 0.5, f"{step.label} at {name}: black videos not drawn"
        ys, xs = np.nonzero(bad)
        where = sorted({(int(x / scale), int(y / scale)) for x, y in zip(xs[:2000], ys[:2000], strict=True)})[:8]
        report.append(f"{name}: {int(bad.sum())} seam pixels (max diff {int(diff[~allowed].max())})")
        assert not bad.any(), (
            f"{step.label} at {name} ({engine}): {int(bad.sum())} screen pixels show the video through the "
            f"overlay, near canvas {where}"
        )
        tp.assert_clean()
    print(f"\n[{engine}] {step.label}: " + "; ".join(report))
