"""(h) YouTube placement: a youtube-nocookie IFrame player, with nothing overlapping it.

The deterministic tests answer ``www.youtube.com/iframe_api`` and the embed
page with local stand-ins (``stubs/``) that record what the player asks for.
``test_youtube_live`` loads the real API and video (``--live-youtube``).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import yaml

from talk_helpers import Step, Talk, TalkPage, visible_rect, wait_until

REPO = Path(__file__).resolve().parent.parent
FULL_HD = {"width": 1920, "height": 1080}
NOCOOKIE = "https://www.youtube-nocookie.com"
CALLS = "() => (window.__ytStub ? window.__ytStub.calls : [])"


def youtube_steps(talk: Talk) -> list[Step]:
    return [s for s in talk.steps if any(k.startswith("youtube:") for k in s.keys)]


def youtube_placement(step: Step) -> tuple[str, dict]:
    m = next(m for m in step.state["media"] if m["kind"] == "youtube")
    key = next(k for k in step.keys if k.startswith("youtube:"))
    return key, m


@pytest.fixture(scope="module")
def decisions(talk: Talk) -> dict:
    path = REPO / "talks-src" / talk.slug / "talk.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_manifest_youtube_matches_talk_yaml(talk: Talk, decisions: dict):
    wanted = decisions.get("youtube") or []
    entries = talk.manifest.get("youtube") or {}
    assert len(entries) == len(wanted), (entries, wanted)
    by_id = {e["id"]: e for e in entries.values()}
    for y in wanted:
        e = by_id[y["id"]]
        assert e["start"] == pytest.approx(float(y["start"])) and e["end"] == pytest.approx(float(y["end"]))
        assert e["title"] == y["title"] and e["credit"] == y["credit"]
        slide = next(s for s in talk.manifest["slides"] if s["id"] == y["slide"])
        placed = [m for st in slide["states"] for m in st["media"] if m["kind"] == "youtube"]
        assert placed, f"slide {slide['n']}: no YouTube placement"
        # The source movie is replaced, not shown as well.
        for st in slide["states"]:
            assert not [m for m in st["media"] if m["kind"] == "video"], f"slide {slide['n']}: a video remains"


def test_youtube_rect_clear_of_slide_content(talk: Talk):
    """SPEC 3.1: no poster under the embed, and the title and credit lie outside it.

    A state of that slide without the embed (before the click that starts the
    movie in Keynote) shows the movie's poster in the embed box instead, as
    Keynote does: no state shows an empty box.
    """
    steps = youtube_steps(talk)
    assert steps
    for step in steps:
        _, m = youtube_placement(step)
        x0, y0, x1, y1 = visible_rect(m["rect"])
        assert (x1 - x0) * (y1 - y0) == m["rect"][2] * m["rect"][3], "embed must lie inside the canvas"
        assert m["rect"][2] * 9 == pytest.approx(m["rect"][3] * 16, rel=0.01), "embed should be 16:9"
        for k, st in enumerate(step.slide["states"]):
            label = f"{step.n}.{k + 1}"
            rgb = talk.rgba(st["image"])[..., :3].astype(int)
            under = rgb[y0:y1, x0:x1]
            if any(mm["kind"] == "youtube" for mm in st["media"]):
                assert under.std() < 1.0 and under.min() > 245, f"{label}: slide content under the embed"
            else:
                assert under.std() > 10 and under.mean() < 235, f"{label}: an empty box where the embed will be"
            ink = rgb.min(axis=2) < 128
            assert ink[:y0].sum() > 1000, f"{label}: no title above the embed"
            assert ink[y1:].sum() > 1000, f"{label}: no credit below the embed"
        assert step.state["overlay"] is None


def open_youtube(open_talk, talk: Talk, youtube: str) -> tuple[TalkPage, Step, str, dict]:
    step = youtube_steps(talk)[0]
    key, m = youtube_placement(step)
    tp = open_talk(youtube=youtube, viewport=FULL_HD)
    tp.goto(talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    tp.wait_stills(step)
    tp.enter_fullscreen()
    return tp, step, key, m


def assert_nothing_over(tp: TalkPage, key: str, rect: list[int]) -> int:
    x0, y0, x1, y1 = visible_rect(rect)
    grid = [(x, y) for y in range(y0 + 2, y1 - 2, 20) for x in range(x0 + 2, x1 - 2, 20)]
    grid += [(x0, y0), (x1 - 1, y0), (x0, y1 - 1), (x1 - 1, y1 - 1)]  # corners
    hits = tp.hit_test(grid, include_overlay=True)
    wrong = [(p, h) for p, h in zip(grid, hits, strict=True) if h != f"{key}:iframe"]
    assert not wrong, f"something over the YouTube player at {wrong[:5]} ({len(wrong)} of {len(grid)})"
    assert tp.page.evaluate("() => document.querySelectorAll('#layer-overlay > img').length") == 0
    assert tp.page.evaluate("() => document.getElementById('stage-ui').children.length") == 0
    return len(grid)


def test_youtube_embed(open_talk, talk: Talk, engine: str):
    tp, step, key, m = open_youtube(open_talk, talk, "stub")
    info = talk.manifest["youtube"][m["ref"]]
    frame = wait_until(
        tp.page,
        "() => { const f = document.querySelector('.media--youtube > iframe'); if (!f) return null;"
        " const r = f.getBoundingClientRect(); return { src: f.src, title: f.title, tabIndex: f.tabIndex,"
        " rect: [r.left, r.top, r.width, r.height] }; }",
    )
    assert frame["src"].startswith(f"{NOCOOKIE}/embed/{info['id']}?"), frame["src"]
    assert frame["title"] == f"YouTube: {info['title']}"
    assert all(abs(a - b) <= 1 for a, b in zip(frame["rect"], m["rect"], strict=True)), (frame["rect"], m["rect"])

    created = tp.page.evaluate("() => window.__ytStub.created")
    assert len(created) == 1
    c = created[0]
    assert c["host"] == NOCOOKIE and c["videoId"] == info["id"]
    pv = c["playerVars"]
    assert (pv["mute"], pv["controls"], pv["playsinline"], pv["rel"]) == (1, 0, 1, 0), pv
    assert pv["start"] == int(info["start"]) and pv["end"] == int(np.ceil(info["end"])), pv

    calls = wait_until(
        tp.page, f"() => {{ const c = ({CALLS})(); return c.some((x) => x.name === 'playVideo') && c; }}"
    )
    names = [x["name"] for x in calls]
    assert "mute" in names and names.index("mute") < names.index("playVideo"), names
    seeks = [x["args"] for x in calls if x["name"] == "seekTo"]
    assert seeks and seeks[0][0] == pytest.approx(info["start"]), seeks

    points = assert_nothing_over(tp, key, m["rect"])

    # Loop: at ENDED, and when the poll sees the time pass `end`, it seeks back to `start`.
    def seek_count() -> int:
        return sum(1 for x in tp.page.evaluate(CALLS) if x["name"] == "seekTo")

    n0 = seek_count()
    tp.page.evaluate("() => window.__ytStub.players[0]._fire(0)")
    wait_until(tp.page, f"(n) => ({CALLS})().filter((x) => x.name === 'seekTo').length > n", n0, timeout=3)
    n1 = seek_count()
    tp.page.evaluate("(t) => window.__ytStub.players[0]._setTime(t)", info["end"] + 0.05)
    wait_until(tp.page, f"(n) => ({CALLS})().filter((x) => x.name === 'seekTo').length > n", n1, timeout=3)
    last_seek = [x for x in tp.page.evaluate(CALLS) if x["name"] == "seekTo"][-1]["args"][0]
    assert last_seek == pytest.approx(info["start"])

    # Leaving destroys the player; coming back creates a new one from `start`.
    tp.press("ArrowRight")
    after = talk.steps[step.index + 1]
    tp.wait_state(after.n, after.k)
    assert "destroy" in [x["name"] for x in tp.page.evaluate(CALLS)]
    assert tp.page.evaluate("() => document.querySelectorAll('iframe').length") == 0
    tp.press("ArrowLeft")
    tp.wait_state(step.n, step.k)
    wait_until(tp.page, "() => window.__ytStub.created.length === 2")
    wait_until(tp.page, f"() => ({CALLS})().filter((x) => x.player === 1 && x.name === 'playVideo').length > 0")
    tp.assert_clean()
    print(f"\n[{engine}] {key}: iframe {frame['src'].split('?')[0]}; {points} hit-test points all on the iframe")


def test_youtube_fallback_when_blocked(open_talk, talk: Talk, engine: str):
    tp, step, key, m = open_youtube(open_talk, talk, "abort")
    info = talk.manifest["youtube"][m["ref"]]
    link = wait_until(
        tp.page,
        "() => { const a = document.querySelector('#stage-ui a.yt-fallback'); if (!a) return null;"
        " const r = a.getBoundingClientRect(); return { href: a.href, text: a.textContent, target: a.target,"
        " rel: a.rel, rect: [r.left, r.top, r.width, r.height] }; }",
        timeout=12,
    )
    assert link["href"] == f"https://www.youtube.com/watch?v={info['id']}&t={int(info['start'])}s"
    assert "Watch on YouTube" in link["text"] and info["title"] in link["text"] and info["credit"] in link["text"]
    assert link["target"] == "_blank" and "noopener" in link["rel"]
    assert all(abs(a - b) <= 1 for a, b in zip(link["rect"], m["rect"], strict=True))
    # Only the blocked YouTube requests may have logged errors.
    tp.console = [
        c
        for c in tp.console
        if not (
            c[0] == "error" and c[1].startswith("Failed to load resource") and ("youtube" in c[2] or "ytimg" in c[2])
        )
    ]
    tp.assert_clean()


def test_youtube_fallback_when_only_the_embed_host_is_blocked(open_talk, talk: Talk, engine: str):
    """The API script loads but youtube-nocookie.com does not: the link replaces the black box."""
    tp, step, key, m = open_youtube(open_talk, talk, "partial")
    info = talk.manifest["youtube"][m["ref"]]
    wait_until(tp.page, "() => window.__ytStub && window.__ytStub.created.length === 1")
    assert tp.page.evaluate("() => document.querySelector('.yt-fallback') === null"), "fallback before the timeout"
    link = wait_until(
        tp.page,
        "() => { const a = document.querySelector('#stage-ui a.yt-fallback'); return a && a.href; }",
        timeout=15,
    )
    assert link == f"https://www.youtube.com/watch?v={info['id']}&t={int(info['start'])}s"
    assert tp.page.evaluate("() => document.querySelectorAll('.media--youtube iframe').length") == 0
    assert "destroy" in [x["name"] for x in tp.page.evaluate(CALLS)]
    tp.console = [
        c
        for c in tp.console
        if not (c[0] == "error" and ("youtube" in c[2] or "ytimg" in c[2] or "youtube-nocookie" in c[1]))
    ]
    tp.assert_clean()


def test_keys_still_work_after_a_click_on_the_embed(open_talk, talk: Talk, engine: str):
    """A click inside the embed must not leave the presenter's keys in YouTube's frame."""
    tp, step, key, m = open_youtube(open_talk, talk, "stub")
    wait_until(tp.page, f"() => ({CALLS})().some((x) => x.name === 'playVideo')")
    box = tp.page.locator(".media--youtube > iframe").bounding_box()
    tp.page.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] * 0.5)
    tp.page.wait_for_timeout(300)
    assert tp.state()["slide"] == step.n and tp.state()["stateIndex"] == step.k, "the click itself navigated"
    active = tp.page.evaluate("() => document.activeElement && document.activeElement.tagName")
    assert active != "IFRAME", "keyboard focus stayed in the YouTube frame"
    after = talk.steps[step.index + 1]
    tp.press("PageDown")
    tp.wait_state(after.n, after.k, timeout=3)
    tp.press("PageUp")
    tp.wait_state(step.n, step.k, timeout=3)
    # Again after the embed is re-created, with Space and the arrow key.
    wait_until(tp.page, "() => window.__ytStub.created.length === 2")
    wait_until(tp.page, f"() => ({CALLS})().some((x) => x.player === 1 && x.name === 'playVideo')")
    box = tp.page.locator(".media--youtube > iframe").bounding_box()
    tp.page.mouse.click(box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.5)
    tp.page.wait_for_timeout(300)
    tp.press("Space")
    tp.wait_state(after.n, after.k, timeout=3)
    tp.press("ArrowLeft")
    tp.wait_state(step.n, step.k, timeout=3)
    tp.assert_clean()


@pytest.mark.live
def test_youtube_live(open_talk, talk: Talk, engine: str):
    tp, step, key, m = open_youtube(open_talk, talk, "live")
    info = talk.manifest["youtube"][m["ref"]]
    src = wait_until(
        tp.page,
        "() => { const f = document.querySelector('.media--youtube > iframe'); return f && f.src; }",
        timeout=20,
    )
    assert src.startswith(f"{NOCOOKIE}/embed/{info['id']}?"), src
    assert tp.page.evaluate("() => document.querySelector('.yt-fallback') === null")

    # Sample the <video> inside the embed until it has played at least 1 s
    # past the first sample taken after the seek to `start` (12 s allowed).
    deadline = time.monotonic() + 30
    samples = []
    first_after_start = None
    while time.monotonic() < deadline:
        frame = next((f for f in tp.page.frames if f.url.startswith(f"{NOCOOKIE}/embed/")), None)
        if frame:
            try:
                v = frame.evaluate(
                    "() => { const v = document.querySelector('video'); return v &&"
                    " { t: v.currentTime, paused: v.paused, muted: v.muted, w: v.videoWidth, rs: v.readyState }; }"
                )
            except Exception:  # noqa: BLE001 - the frame may navigate while it boots
                v = None
            if v:
                samples.append(v)
                if first_after_start is None and v["t"] >= info["start"] - 0.5:
                    first_after_start = v
                    deadline = min(deadline, time.monotonic() + 12)
                if first_after_start and not v["paused"] and v["t"] >= first_after_start["t"] + 1.0:
                    break
        tp.page.wait_for_timeout(500)
    print(
        f"\n[{engine}] live YouTube: iframe {src.split('?')[0]}; "
        f"first at start {first_after_start}; last {samples[-1:]}"
    )

    # What the talks player controls: the embed, its parameters, start, mute, layout, no errors.
    assert samples, "no <video> in the YouTube frame"
    assert first_after_start, f"never reached start = {info['start']} s: {samples[-3:]}"
    last = samples[-1]
    assert last["muted"] and not last["paused"] and last["w"] > 0, last
    assert info["start"] - 0.5 <= last["t"] <= info["end"] + 0.5, last
    assert_nothing_over(tp, key, m["rect"])
    main = tp.base
    errors = [c for c in tp.console if c[0] == "error" and (not c[2] or c[2].startswith(main))]
    csp = [v for v in tp.csp() if (v.get("document") or "").startswith(main)]
    print(f"[{engine}] live YouTube: main-page console errors {errors}; main-page CSP reports {csp}")
    assert not csp, csp
    assert not errors, errors
    assert not tp.page_errors, tp.page_errors

    # What YouTube's own player does: play. Playwright's WebKit build stalls
    # YouTube's MSE playback at the first frame (readyState 4, paused false,
    # data buffered) even for a bare youtube-nocookie embed without this
    # player, headless or headed, while it plays our MP4s; Safari is not
    # automated here. Chromium must advance.
    advancing = last["t"] >= first_after_start["t"] + 1.0
    if not advancing and engine == "webkit" and last["rs"] >= 3:
        pytest.xfail(f"Playwright WebKit does not advance YouTube MSE playback (stuck at t={last['t']:.3f} s)")
    assert advancing, f"YouTube video is not advancing: {samples[-3:]}"

    # The loop on the real player: seek YouTube's player to 1.5 s before `end`
    # through its own command channel (what the IFrame API's seekTo posts);
    # the talks player must bring it back to `start` and keep playing.
    frame = next(f for f in tp.page.frames if f.url.startswith(f"{NOCOOKIE}/embed/"))
    tp.page.evaluate(
        """(t) => { const f = document.querySelector('.media--youtube > iframe');
          f.contentWindow.postMessage(JSON.stringify({ event: 'command', func: 'seekTo', args: [t, true],
            id: f.id, channel: 'widget' }), 'https://www.youtube-nocookie.com'); }""",
        info["end"] - 1.5,
    )
    looped = None
    deadline = time.monotonic() + 15
    peak = 0.0
    while time.monotonic() < deadline:
        t = frame.evaluate("() => document.querySelector('video').currentTime")
        peak = max(peak, t)
        if peak >= info["end"] - 0.5 and info["start"] - 0.5 <= t < info["start"] + 3:
            looped = t
            break
        tp.page.wait_for_timeout(250)
    print(
        f"[{engine}] live YouTube loop: reached {peak:.2f} s, back at {looped} s "
        f"(start {info['start']}, end {info['end']})"
    )
    assert looped is not None, f"no loop back to start after {peak:.2f} s"
    assert peak <= info["end"] + 1.0, f"played past end: {peak:.2f} s"


def test_youtube_links_out_when_too_small_to_embed(open_talk, talk: Talk, engine: str):
    """YouTube's terms require an embedded player of at least 200 x 200 CSS px.

    On a phone the slide 6 box renders below that, so the player shows the
    'Watch on YouTube' link instead and never loads YouTube's script.
    """
    from test_mobile import PHONE

    step = youtube_steps(talk)[0]
    key, m = youtube_placement(step)
    tp = open_talk(youtube="stub", **PHONE)
    tp.goto(talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    tp.wait_stills(step)
    size = tp.page.evaluate(
        "(r) => { const s = document.getElementById('stage').getBoundingClientRect();"
        " return [r[2] / 1920 * s.width, r[3] / 1080 * s.height]; }",
        m["rect"],
    )
    assert min(size) < 200, f"test premise: embed box {size} is not below 200 px on a phone"
    link = wait_until(
        tp.page,
        "() => { const a = document.querySelector('#stage-ui a.yt-fallback'); return a && a.href; }",
        timeout=5,
    )
    info = talk.manifest["youtube"][m["ref"]]
    assert link == f"https://www.youtube.com/watch?v={info['id']}&t={int(info['start'])}s"
    assert tp.page.evaluate("() => document.querySelectorAll('.media--youtube iframe').length") == 0
    assert tp.page.evaluate(
        "() => !document.querySelector('script[src*=\"youtube.com/iframe_api\"]')"
    ), "YouTube API loaded although the box is too small to embed"
    # The fallback's thumbnail comes from i.ytimg.com, which the harness blocks.
    tp.external = [u for u in tp.external if not u.startswith("https://i.ytimg.com/")]
    tp.console = [c for c in tp.console if not (c[0] == "error" and "ytimg" in c[2])]
    tp.assert_clean()
