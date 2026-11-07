"""Magic Move (SPEC 3.2): a video whose source is in both states at a new rect
keeps its element, keeps playing and glides to the new rect; a jump or reduced
motion places it at once. Skipped for talks without a magic-move state.
"""

from __future__ import annotations

import pytest

import math

import numpy as np
from PIL import Image

from talk_helpers import Talk, wait_until


def placement_key(m: dict) -> str:
    """The player's key: Math.round, i.e. floor(v + 0.5), not Python's round()."""
    return f"{m['kind']}:{m['ref']}:" + ",".join(str(math.floor(float(v) + 0.5)) for v in m["rect"])


def magic_steps(talk: Talk):
    return [s for s in talk.steps if s.state.get("transition") == "magic-move"]


def _moved_key(talk: Talk, step):
    """(previous step, old placement, new placement) of the video that glides."""
    before = talk.steps[step.index - 1]
    old = [m for m in before.state["media"] if m["kind"] == "video"]
    new = [m for m in step.state["media"] if m["kind"] == "video"]
    kept = {placement_key(m) for m in new} & {placement_key(m) for m in old}
    free = [o for o in old if placement_key(o) not in kept]
    for m in new:
        if placement_key(m) in kept:
            continue
        for o in free:
            if o["ref"] == m["ref"]:
                return before, o, m
    raise AssertionError(f"{step.label}: magic-move without a moved video")


PROBE = """(key) => { const m = window.__talk.media().find((x) => x.key === key);
  if (!m) return null; const r = m.el.getBoundingClientRect(); const s = m.el.parentElement.getBoundingClientRect();
  return { same: m.el === window.__mmEl, key: m.key, paused: m.el.paused, t: m.el.currentTime,
           x: (r.left - s.left) / s.width * 1920, y: (r.top - s.top) / s.height * 1080,
           w: r.width / s.width * 1920, h: r.height / s.height * 1080, events: window.__mmEvents.slice() }; }"""


def test_magic_move_glides_and_keeps_playing(open_talk, talk: Talk, engine: str):
    steps = magic_steps(talk)
    if not steps:
        pytest.skip("talk has no magic-move state")
    step = steps[0]
    before, origin, moved = _moved_key(talk, step)
    ref = moved["ref"]
    duration = step.state["transition_duration"]

    tp = open_talk()
    tp.goto(talk.hash_for(before.n, before.k))
    tp.wait_state(before.n, before.k)
    tp.wait_videos_playing(min_time=0.3)
    from_key, to_key = placement_key(origin), placement_key(moved)
    tp.page.evaluate(
        """(key) => { const m = window.__talk.media().find((x) => x.key === key);
          window.__mmEl = m.el; window.__mmEvents = [];
          for (const t of ['emptied', 'loadstart', 'abort', 'seeking'])
            m.el.addEventListener(t, () => window.__mmEvents.push(t)); }""",
        from_key,
    )
    start = tp.page.evaluate(PROBE, from_key)
    tp.press("ArrowRight")
    tp.wait_state(step.n, step.k)
    tp.page.wait_for_timeout(int(duration * 1000 * 0.55))
    mid = tp.page.evaluate(PROBE, to_key)
    shot = np.asarray(Image.fromarray(tp.stage_pixels()).resize((1920, 1080)), dtype=float)
    assert mid and mid["same"], f"{step.label}: the moving video was replaced: {mid}"
    assert not mid["paused"] and not mid["events"], mid
    # Check interpolation on the coordinate that changes most (x, y, w or h).
    axis = max(range(4), key=lambda i: abs(moved["rect"][i] - origin["rect"][i]))
    a, b = origin["rect"][axis], moved["rect"][axis]
    got_mid = [mid["x"], mid["y"], mid["w"], mid["h"]][axis]
    assert abs(b - a) > 10, f"move too small to observe: {origin['rect']} -> {moved['rect']}"
    assert min(a, b) + 2 < got_mid < max(a, b) - 2, f"mid-glide {'xywh'[axis]}={got_mid:.0f} not between {a} and {b}"

    # No poster copy beside the moving video: the part of the destination rect
    # the video has not reached yet shows the glide background, not the still.
    if "glide_image" in step.state:
        x, y, w, h = moved["rect"]
        x1 = int(min(x + w, mid["x"])) - 4
        assert x1 - x > 40, f"no uncovered strip to check (video at x={mid['x']:.0f})"
        box = (slice(int(y) + 20, int(y + h) - 20), slice(int(x) + 4, x1))
        bare = np.asarray(Image.open(talk.media_path(step.state["glide_image"])).convert("RGB"), dtype=float)[box]
        full = np.asarray(Image.open(talk.media_path(step.state["image"])).convert("RGB"), dtype=float)[box]
        seen = shot[box]
        d_bare, d_full = np.abs(seen - bare).mean(), np.abs(seen - full).mean()
        assert np.abs(bare - full).mean() > 5, "glide background equals the still here; check is vacuous"
        assert d_bare < d_full, f"poster copy visible mid-glide (diff to bare {d_bare:.1f}, to still {d_full:.1f})"
    tp.page.wait_for_timeout(int(duration * 1000 * 0.65) + 300)
    end = tp.page.evaluate(PROBE, to_key)
    assert end["same"] and not end["paused"] and not end["events"], end
    assert end["t"] > start["t"], f"time went back {start['t']:.2f} -> {end['t']:.2f}"
    want = moved["rect"]
    got = [end["x"], end["y"], end["w"], end["h"]]
    assert all(abs(a - b) <= 2 for a, b in zip(got, want)), f"end rect {got} != {want}"
    assert end["key"] == to_key, end["key"]
    tp.assert_clean()
    print(f"\n[{engine}] {step.label}: {ref} glided {origin['rect']} -> {want} "
          f"(mid {'xywh'[axis]}={got_mid:.0f}), t {start['t']:.2f} -> {end['t']:.2f}")


def test_magic_move_jump_places_at_once(open_talk, talk: Talk, engine: str):
    steps = magic_steps(talk)
    if not steps:
        pytest.skip("talk has no magic-move state")
    step = steps[0]
    _, _, moved = _moved_key(talk, step)
    tp = open_talk()
    tp.goto(talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    wait_until(tp.page, "() => window.__talk.media().length > 0")
    got = tp.page.evaluate(
        """(key) => { const m = window.__talk.media().find((x) => x.key === key);
          return m ? m.el.classList.contains('glide') : null; }""",
        placement_key(moved),
    )
    assert got is False, "a direct jump must not glide"
    tp.assert_clean()


def test_moved_key_picks_the_moving_copy_of_a_repeated_ref():
    class _S:
        def __init__(self, media, index):
            self.state, self.index, self.label = {"media": media}, index, f"s{index}"

    v = lambda x: {"kind": "video", "ref": "v01", "rect": [x, 0, 100, 100]}
    talk = type("T", (), {})()
    talk.steps = [_S([v(0), v(500)], 0), _S([v(0), v(900)], 1)]
    _, origin, moved = _moved_key(talk, talk.steps[1])
    assert origin["rect"][0] == 500 and moved["rect"][0] == 900
