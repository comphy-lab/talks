"""(f) Pause control, P/K keys and reduced motion; (g) deep links, Home/End and navigation."""

from __future__ import annotations

from talk_helpers import Talk, TalkPage, wait_until

ALL_VIDEOS_PAUSED = "() => window.__talk.media().filter((m) => m.el.tagName === 'VIDEO').every((m) => m.el.paused)"


def times(tp: TalkPage) -> dict[str, float]:
    return {v["key"]: v["t"] for v in tp.videos()}


def assert_frozen(tp: TalkPage, ms: int = 600) -> dict[str, float]:
    t0 = times(tp)
    tp.page.wait_for_timeout(ms)
    t1 = times(tp)
    for key in t0:
        assert abs(t1[key] - t0[key]) < 0.02, f"{key} moved while paused: {t0[key]:.3f} -> {t1[key]:.3f}"
    return t1


def assert_running(tp: TalkPage, ms: int = 700) -> None:
    t0 = times(tp)
    tp.page.wait_for_timeout(ms)
    t1 = times(tp)
    for key in t0:
        assert t1[key] > t0[key] + 0.2, f"{key} not playing: {t0[key]:.3f} -> {t1[key]:.3f}"


def pause_button(tp: TalkPage) -> dict:
    return tp.page.evaluate(
        "() => { const b = document.getElementById('btn-pause');"
        " return { label: b.getAttribute('aria-label'), paused: b.dataset.paused }; }"
    )


# ------------------------------------------------------------------- (f)


def test_pause_button_and_keys(open_talk, talk: Talk, engine: str):
    tp = open_talk()
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.wait_videos_playing(min_time=0.2)
    assert len(tp.videos()) == 2
    assert pause_button(tp) == {"label": "Pause videos", "paused": "false"}

    tp.page.click("#btn-pause")
    wait_until(tp.page, ALL_VIDEOS_PAUSED)
    assert tp.state()["paused"] is True
    assert pause_button(tp) == {"label": "Play videos", "paused": "true"}
    assert_frozen(tp)

    tp.page.click("#btn-pause")
    wait_until(tp.page, "() => window.__talk.media().every((m) => !m.el.paused)")
    assert tp.state()["paused"] is False
    assert pause_button(tp) == {"label": "Pause videos", "paused": "false"}
    assert_running(tp)

    for key in ("p", "k", "P", "K"):
        was = tp.state()["paused"]
        tp.page.evaluate("() => document.activeElement && document.activeElement.blur()")
        tp.press(key)
        wait_until(tp.page, "(want) => window.__talk.state().paused === want", not was)
        if was:
            assert_running(tp)
        else:
            assert_frozen(tp)
    assert tp.state()["paused"] is False
    tp.assert_clean()


def test_media_mounted_while_paused_stays_paused(open_talk, talk: Talk):
    tp = open_talk()
    tp.goto(talk.hash_for(4, 0))
    tp.wait_state(4, 0)
    tp.wait_videos_playing(min_time=0.2)
    tp.press("p")
    wait_until(tp.page, ALL_VIDEOS_PAUSED)
    tp.press("ArrowRight")
    tp.wait_state(4, 1)
    tp.page.wait_for_timeout(700)
    vids = {v["key"]: v for v in tp.videos()}
    assert len(vids) == 2 and all(v["paused"] for v in vids.values()), vids
    new_key = next(k for k in talk.step(4, 1).video_keys if k not in talk.step(4, 0).keys)
    assert vids[new_key]["t"] < 0.05, f"new video advanced while paused: {vids[new_key]['t']:.3f}"
    tp.press("p")
    wait_until(tp.page, "() => window.__talk.media().every((m) => !m.el.paused)")
    assert_running(tp)
    tp.assert_clean()


def test_youtube_follows_pause(open_talk, talk: Talk):
    step = next(s for s in talk.steps if any(k.startswith("youtube:") for k in s.keys))
    tp = open_talk(youtube="stub")
    tp.goto(talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    calls = "() => (window.__ytStub ? window.__ytStub.calls.map((c) => c.name) : [])"
    wait_until(tp.page, f"() => ({calls})().includes('playVideo')")
    tp.press("p")
    names = wait_until(tp.page, f"() => {{ const c = ({calls})(); return c[c.length - 1] === 'pauseVideo' && c; }}")
    tp.press("p")
    names = wait_until(tp.page, f"() => {{ const c = ({calls})(); return c[c.length - 1] === 'playVideo' && c; }}")
    assert names.count("pauseVideo") == 1, names
    tp.assert_clean()


def test_reduced_motion_starts_paused(open_talk, talk: Talk, engine: str):
    tp = open_talk(reduced_motion="reduce")
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    wait_until(tp.page, "() => window.__talk.media().every((m) => m.el.readyState >= 2)")
    assert tp.state()["paused"] is True
    assert pause_button(tp) == {"label": "Play videos", "paused": "true"}
    tp.page.wait_for_timeout(800)
    vids = tp.videos()
    assert vids and all(v["paused"] and v["t"] < 0.05 for v in vids), vids

    # No dissolve under reduced motion (slide 3's build is a dissolve).
    dissolve = next(s for s in talk.steps if s.state["transition"] == "dissolve")
    before = talk.steps[dissolve.index - 1]
    tp.goto(talk.hash_for(before.n, before.k))
    tp.wait_state(before.n, before.k)
    tp.wait_stills(before)
    tp.press("ArrowRight")
    tp.wait_state(dissolve.n, dissolve.k)
    assert tp.page.evaluate("() => document.querySelectorAll('.fade').length") == 0

    # The button resumes.
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.page.click("#btn-pause")
    wait_until(tp.page, "() => window.__talk.media().every((m) => !m.el.paused)")
    assert_running(tp)
    tp.assert_clean()


def test_dissolve_runs_forwards_only(open_talk, talk: Talk):
    dissolve = next(s for s in talk.steps if s.state["transition"] == "dissolve")
    before = talk.steps[dissolve.index - 1]
    tp = open_talk()
    tp.goto(talk.hash_for(before.n, before.k))
    tp.wait_state(before.n, before.k)
    tp.wait_stills(before)
    tp.press("ArrowRight")
    fading = tp.page.evaluate("() => document.querySelectorAll('#layer-image .fade').length")
    assert fading >= 1, "no dissolve on a 'dissolve' state"
    tp.wait_stills(dissolve)  # fade finished: one still, opacity 1
    tp.press("ArrowLeft")
    tp.wait_state(before.n, before.k)
    assert tp.page.evaluate("() => document.querySelectorAll('.fade').length") == 0
    tp.wait_stills(before)
    tp.assert_clean()


# ------------------------------------------------------------------- (g)


def test_deep_links(open_talk, talk: Talk):
    tp = open_talk()
    span_key = talk.step(34, 0).keys[0]

    tp.goto(talk.hash_for(34))
    st = tp.wait_state(34, 0)
    assert span_key in st["keys"]
    tp.wait_stills(talk.step(34, 0))
    tp.wait_videos_playing(min_time=0.1)
    assert tp.page.evaluate("() => location.hash") == talk.hash_for(34)

    cases = [
        (f"{talk.hash_for(34)}.1", (34, 0), talk.hash_for(34)),  # explicit first state
        (talk.hash_for(34).upper(), (34, 0), talk.hash_for(34)),  # case-insensitive
        (talk.hash_for(4, 1), (4, 1), talk.hash_for(4, 1)),  # second state, 1-based suffix
        (f"{talk.hash_for(4)}.9", (4, 1), talk.hash_for(4, 1)),  # clamped to the last state
        ("#deadbeef", (1, 0), talk.hash_for(1)),  # unknown: first state
    ]
    for hash_, (n, k), canonical in cases:
        tp.page.goto("about:blank")
        tp.goto(hash_)
        tp.wait_state(n, k)
        tp.wait_stills(talk.step(n, k))
        wait_until(tp.page, "(h) => location.hash === h", canonical, timeout=3)

    # Typing a new hash navigates; an invalid one snaps back.
    tp.page.evaluate("(h) => { location.hash = h; }", talk.hash_for(34))
    tp.wait_state(34, 0)
    tp.page.evaluate("() => { location.hash = '#zz'; }")
    wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(34), timeout=3)
    assert (tp.state()["slide"], tp.state()["stateIndex"]) == (34, 0)
    tp.assert_clean()


def test_home_end_and_history(open_talk, talk: Talk):
    tp = open_talk()
    tp.goto(talk.hash_for(34))
    tp.wait_state(34, 0)
    length = tp.page.evaluate("() => history.length")
    last = talk.steps[-1]
    tp.press("End")
    tp.wait_state(last.n, last.k)
    tp.wait_stills(last)
    wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(last.n, last.k), timeout=3)
    tp.press("Home")
    tp.wait_state(1, 0)
    tp.wait_stills(talk.steps[0])
    wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(1), timeout=3)
    tp.press("ArrowLeft")  # before the first state: nothing happens
    tp.page.wait_for_timeout(200)
    assert (tp.state()["slide"], tp.state()["stateIndex"]) == (1, 0)
    assert tp.page.evaluate("() => history.length") == length, "navigation added history entries"
    tp.assert_clean()


def test_key_repeat_does_not_break_deep_links(open_talk, talk: Talk):
    """Holding an arrow key: WebKit throws after 100 replaceState calls in 10 s."""
    tp = open_talk()
    tp.goto()
    tp.wait_state(1, 0)
    for _ in range(len(talk.steps) + 5):
        tp.page.keyboard.press("ArrowRight")
    last = talk.steps[-1]
    tp.wait_state(last.n, last.k, timeout=20)
    tp.wait_stills(last)
    wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(last.n, last.k), timeout=3)
    for _ in range(len(talk.steps) + 5):
        tp.page.keyboard.press("ArrowLeft")
    tp.wait_state(1, 0, timeout=20)
    wait_until(tp.page, "(h) => location.hash === h", talk.hash_for(1), timeout=5)
    assert not tp.page_errors, tp.page_errors
    tp.assert_clean()


def test_navigation_keys_and_stage_clicks(open_talk, talk: Talk):
    tp = open_talk()
    start = talk.step(10, 0)
    tp.goto(talk.hash_for(start.n))
    tp.wait_state(start.n, 0)
    pos = start.index

    def expect(index: int) -> None:
        s = talk.steps[index]
        tp.wait_state(s.n, s.k)

    for key, delta in [
        ("ArrowRight", 1), ("ArrowDown", 1), ("PageDown", 1), ("Space", 1), ("Enter", 1),
        ("ArrowLeft", -1), ("ArrowUp", -1), ("PageUp", -1), ("Backspace", -1),
    ]:  # fmt: skip
        tp.page.evaluate("() => document.activeElement && document.activeElement.blur()")
        tp.press(key)
        pos += delta
        expect(pos)

    box = tp.page.locator("#stage-frame").bounding_box()
    tp.page.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)
    pos += 1
    expect(pos)
    tp.page.mouse.click(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
    pos -= 1
    expect(pos)
    tp.page.click("#btn-next")
    pos += 1
    expect(pos)
    tp.page.click("#btn-prev")
    pos -= 1
    expect(pos)
    tp.assert_clean()


def test_hash_typed_right_after_a_step_wins(open_talk, talk: Talk):
    """A coalesced hash write pending from a step must not override a hash the user sets."""
    tp = open_talk()
    for n in (3, 12, 50):
        tp.goto(talk.hash_for(n))
        tp.wait_state(n, 0)
        tp.press("ArrowRight")
        tp.page.evaluate("(h) => { location.hash = h; }", talk.hash_for(34))
        tp.wait_state(34, 0)
        tp.page.wait_for_timeout(600)
        assert tp.page.evaluate("() => location.hash") == talk.hash_for(34)
        assert (tp.state()["slide"], tp.state()["stateIndex"]) == (34, 0)
    tp.assert_clean()


ANNOUNCER_JS = "() => document.getElementById('announcer').textContent"


def test_announcements_follow_click_steps(open_talk, talk: Talk):
    """The live region names a new slide, then each later click step of it."""
    total = len(talk.manifest["slides"])
    n = next(s["n"] for s in talk.manifest["slides"] if len(s["states"]) >= 3 and s["n"] > 1)
    count = len(talk.slide(n)["states"])
    title = talk.slide(n)["title"]
    tp = open_talk()
    tp.goto(talk.hash_for(n - 1, len(talk.slide(n - 1)["states"]) - 1))
    tp.wait_state(n - 1, len(talk.slide(n - 1)["states"]) - 1)
    tp.press("ArrowRight")
    tp.wait_state(n, 0)
    want = f"Slide {n} of {total}, step 1 of {count}: {title}"
    wait_until(tp.page, f"() => ({ANNOUNCER_JS})() === {want!r}", timeout=3)
    for k in range(1, count):
        tp.press("ArrowRight")
        tp.wait_state(n, k)
        wait_until(tp.page, f"() => ({ANNOUNCER_JS})() === 'Step {k + 1} of {count}'", timeout=3)
    nxt = talk.slide(n + 1)
    tp.press("ArrowRight")
    tp.wait_state(n + 1, 0)
    want = f"Slide {n + 1} of {total}" + (f", step 1 of {len(nxt['states'])}" if len(nxt["states"]) > 1 else "")
    wait_until(tp.page, f"() => ({ANNOUNCER_JS})() === {want + ': ' + nxt['title']!r}", timeout=3)
    tp.press("ArrowLeft")
    tp.wait_state(n, count - 1)
    want = f"Slide {n} of {total}, step {count} of {count}: {title}"
    wait_until(tp.page, f"() => ({ANNOUNCER_JS})() === {want!r}", timeout=3)
    tp.assert_clean()
