"""(a) Every click state is reachable with ArrowRight and shows its stills and media.

(d) Every video element loops and is muted (checked at every state of the walk,
and on its own for each video placement).

The first walk runs in a scaled stage (1600 x 1000 viewport) and checks the
DOM; the second runs in full screen at 1920 x 1080, one screen pixel per
canvas pixel, and checks the pixels on screen against the state's still.
"""

from __future__ import annotations

import numpy as np

from talk_helpers import CANVAS_H, CANVAS_W, VIDEO_COUNT_JS, Talk, TalkPage, wait_until

FULL_HD = {"width": 1920, "height": 1080}
# Media boxes are excluded from the pixel comparison with this margin (px).
MEDIA_MARGIN = 2


def check_video_element(v: dict, talk: Talk, tp: TalkPage) -> list[str]:
    """SPEC 3.2 'Video elements': muted, playsinline, loop, preload=auto, no controls, poster, src."""
    ref = v["key"].split(":")[1]
    info = talk.manifest["videos"][ref]
    problems = []
    if not v["loop"]:
        problems.append("loop is false")
    if not (v["muted"] and v["defaultMuted"] and v["mutedAttr"]):
        problems.append(f"not muted (muted={v['muted']}, defaultMuted={v['defaultMuted']}, attr={v['mutedAttr']})")
    if not v["playsInline"]:
        problems.append("no playsinline")
    if v["controls"]:
        problems.append("controls shown")
    if v["preload"] != "auto":
        problems.append(f"preload={v['preload']}")
    if not v["noPip"]:
        problems.append("no disablepictureinpicture")
    if v["src"] != tp.media_url(info["src"]):
        problems.append(f"src {v['src']} is not {info['src']}")
    if v["poster"] != tp.media_url(info["poster"]):
        problems.append(f"poster {v['poster']} is not {info['poster']}")
    return [f"{v['key']}: {p}" for p in problems]


def test_walk_every_state(open_talk, talk: Talk, engine: str):
    tp = open_talk(viewport={"width": 1600, "height": 1000})
    tp.goto()
    report = []
    for step in talk.steps:
        if step.index > 0:
            tp.press("ArrowRight")
        st = tp.wait_state(step.n, step.k)
        tp.wait_stills(step)
        assert st["keys"] == step.keys, f"{step.label}: mounted {st['keys']}, manifest {step.keys}"
        media = tp.media()
        assert [m["key"] for m in media] == step.keys
        for m in media:
            assert m["connected"] and m["rect"][2] > 0 and m["rect"][3] > 0, f"{step.label}: {m}"
        if step.video_keys:
            vids = tp.wait_videos_playing(min_time=0.05, timeout=10)
            for v in vids:
                assert not (problems := check_video_element(v, talk, tp)), f"{step.label}: {problems}"
                assert v["visibility"] == "visible" and v["display"] != "none", f"{step.label}: {v}"
            report.append(f"{step.label}: " + ", ".join(f"{v['key']} t={v['t']:.2f}" for v in vids))
        # No leaked or orphaned video elements.
        assert tp.page.evaluate(VIDEO_COUNT_JS) == len(step.video_keys), f"{step.label}: stray <video> elements"
        # Chrome reflects the state.
        chrome = tp.page.evaluate(
            "() => [document.getElementById('counter-num').textContent,"
            " document.getElementById('stage').getAttribute('aria-label')]"
        )
        assert chrome[0] == f"{step.n} / {len(talk.manifest['slides'])}", chrome
        assert chrome[1] == (step.slide["title"].strip() or f"Slide {step.n}"), chrome
        # The deep link follows (hash writes are coalesced to one per 250 ms).
        wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(step.n, step.k), timeout=3)

    # Past the end nothing changes.
    last = talk.steps[-1]
    tp.press("ArrowRight")
    tp.page.wait_for_timeout(300)
    st = tp.state()
    assert (st["slide"], st["stateIndex"]) == (last.n, last.k)
    assert len(talk.steps) == sum(len(s["states"]) for s in talk.manifest["slides"])
    tp.assert_clean()
    print(f"\n[{engine}] walked {len(talk.steps)} states; video states:\n  " + "\n  ".join(report))


def test_every_video_element_loops_and_is_muted(open_talk, talk: Talk, engine: str):
    tp = open_talk()
    seen: dict[str, str] = {}
    for step in talk.steps:
        if not step.video_keys:
            continue
        tp.goto(talk.hash_for(step.n, step.k))
        tp.wait_state(step.n, step.k)
        vids = tp.wait_videos_playing(min_time=0.05)
        assert {v["key"] for v in vids} == set(step.video_keys)
        for v in vids:
            assert not (problems := check_video_element(v, talk, tp)), f"{step.label}: {problems}"
            seen.setdefault(v["key"], step.label)
    every_ref = set(talk.manifest["videos"])
    assert {k.split(":")[1] for k in seen} == every_ref, "some manifest videos were never placed"
    tp.assert_clean()
    print(f"\n[{engine}] {len(seen)} video placements, {len(every_ref)} sources: all loop, muted, playsinline")


def outside_media(step) -> np.ndarray:
    """Canvas mask of the pixels no media placement (plus a margin) covers."""
    mask = np.ones((CANVAS_H, CANVAS_W), dtype=bool)
    for m in step.state["media"]:
        x, y, w, h = (round(v) for v in m["rect"])
        x0, y0 = max(0, x - MEDIA_MARGIN), max(0, y - MEDIA_MARGIN)
        x1, y1 = min(CANVAS_W, x + w + MEDIA_MARGIN), min(CANVAS_H, y + h + MEDIA_MARGIN)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = False
    return mask


def test_every_state_paints_its_still(open_talk, talk: Talk, engine: str):
    """At 1:1, the screen outside the media boxes is the state's image with its overlay on top."""
    tp = open_talk(viewport=FULL_HD)
    tp.goto()
    tp.enter_fullscreen()
    worst = []
    for step in talk.steps:
        if step.index > 0:
            tp.press("ArrowRight")
        tp.wait_state(step.n, step.k)
        tp.wait_stills(step)
        screen = tp.stage_pixels().astype(np.int16)
        assert screen.shape == (CANVAS_H, CANVAS_W, 3), f"{step.label}: stage is {screen.shape}, not 1920 x 1080"
        diff = np.abs(screen - talk.expected_still(step).astype(np.int16)).max(axis=2)[outside_media(step)]
        mean, off = float(diff.mean()), float((diff > 16).mean())
        worst.append((mean, off, step.label))
        assert mean <= 0.5 and off <= 0.001, (
            f"{step.label}: screen differs from its still (mean {mean:.2f}, {off:.2%} of pixels by more than 16)"
        )
    tp.assert_clean()
    worst.sort(reverse=True)
    print(
        f"\n[{engine}] {len(talk.steps)} states painted at 1:1; largest differences outside media: "
        + ", ".join(f"{label} mean {mean:.3f} (>16 on {off:.3%})" for mean, off, label in worst[:3])
    )
