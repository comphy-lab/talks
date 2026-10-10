"""(j) Narrow touch screen (390 x 844): swipes and taps navigate, controls are reachable.

Chromium gets real touch input through the DevTools protocol
(``Input.dispatchTouchEvent``), so its swipes go through the browser's own
touch-to-pointer conversion and ``touch-action``. Playwright cannot inject
touch moves into WebKit, so there the swipe is a sequence of synthetic
``PointerEvent``s with ``pointerType: 'touch'``; taps are real in both.
"""

from __future__ import annotations

from talk_helpers import Talk, TalkPage, wait_until

PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True, "device_scale_factor": 3}
# iPhone Safari has no element full screen; the player then fills the viewport itself.
NO_ELEMENT_FULLSCREEN = """
delete Element.prototype.requestFullscreen; delete Element.prototype.webkitRequestFullscreen;
delete Document.prototype.exitFullscreen; delete Document.prototype.webkitExitFullscreen;
"""
CONTROLS = ["#btn-prev", "#btn-next", "#btn-pause", "#btn-fullscreen", ".controls [data-theme-toggle]"]

REACH_JS = """
(sel) => {
  const el = document.querySelector(sel);
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return { rect: [r.left, r.top, r.width, r.height], hit: Boolean(hit && (hit === el || el.contains(hit))),
           visible: cs.visibility === 'visible' && cs.display !== 'none' && Number(cs.opacity) > 0.3 };
}
"""


CHROME = [".skip-link", ".site-header", ".talk-head", ".progress", ".controls", ".about", ".site-footer"]

FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { body: true };
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return { body: false, id: el.id || el.className, inFrame: Boolean(el.closest('#stage-frame')),
           covered: !(hit && (hit === el || el.contains(hit))), visible: r.width > 0 && r.height > 0 };
}
"""

RING_JS = """
(sel) => { const cs = getComputedStyle(document.querySelector(sel));
  return { outline: cs.outlineColor, style: cs.outlineStyle, width: cs.outlineWidth, shadow: cs.boxShadow,
           focusVisible: document.querySelector(sel).matches(':focus-visible') }; }
"""


def _rgb(css: str) -> tuple[float, float, float]:
    import re

    r, g, b = (float(v) for v in re.findall(r"[\d.]+", css)[:3])
    return r, g, b


def contrast_with_black(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])
    return (lum + 0.05) / 0.05


def swipe(tp: TalkPage, engine: str, x0: float, y0: float, x1: float, y1: float, steps: int = 8) -> None:
    if engine == "chromium":
        cdp = tp.page.context.new_cdp_session(tp.page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
        for i in range(1, steps + 1):
            x, y = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        cdp.detach()
        return
    common = {"pointerId": 41, "pointerType": "touch", "isPrimary": True, "bubbles": True, "cancelable": True}
    tp.page.dispatch_event("#stage-frame", "pointerdown", {**common, "clientX": x0, "clientY": y0, "buttons": 1})
    for i in range(1, steps + 1):
        x, y = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
        tp.page.dispatch_event("#stage-frame", "pointermove", {**common, "clientX": x, "clientY": y, "buttons": 1})
    tp.page.dispatch_event("#stage-frame", "pointerup", {**common, "clientX": x1, "clientY": y1, "buttons": 0})


def position(tp: TalkPage) -> tuple[int, int]:
    st = tp.state()
    return st["slide"], st["stateIndex"]


def settle(tp: TalkPage, ms: int = 400) -> tuple[int, int]:
    tp.page.wait_for_timeout(ms)
    return position(tp)


def assert_reachable(tp: TalkPage, selector: str) -> list[float]:
    tp.page.locator(selector).scroll_into_view_if_needed()
    r = tp.page.evaluate(REACH_JS, selector)
    x, y, w, h = r["rect"]
    vw, vh = tp.page.viewport_size["width"], tp.page.viewport_size["height"]
    assert r["visible"], f"{selector} hidden"
    assert 0 <= x and x + w <= vw and 0 <= y and y + h <= vh, f"{selector} outside the viewport: {r['rect']}"
    assert w >= 44 and h >= 44, f"{selector} target {w:.0f} x {h:.0f} px is under 44 x 44"
    assert r["hit"], f"{selector} is covered at its centre"
    return r["rect"]


def test_phone_swipe_tap_and_controls(open_talk, talk: Talk, engine: str):
    tp = open_talk(**PHONE)
    start = talk.step(10, 0)
    tp.goto(talk.hash_for(start.n))
    tp.wait_state(start.n, 0)
    pos = start.index

    def expect(index: int, what: str) -> None:
        s = talk.steps[index]
        try:
            tp.wait_state(s.n, s.k, timeout=3)
        except AssertionError as exc:
            raise AssertionError(f"{what}: expected {s.label}, at {position(tp)}") from exc
        assert settle(tp, 300) == (s.n, s.k), f"{what}: moved more than one step"

    # Layout: no sideways scrolling, a 16:9 stage across the width.
    layout = tp.page.evaluate(
        "() => { const r = document.getElementById('stage').getBoundingClientRect();"
        " return { sw: document.documentElement.scrollWidth, w: innerWidth,"
        " stage: [r.left, r.top, r.width, r.height] }; }"
    )
    assert layout["sw"] <= layout["w"], f"horizontal overflow: {layout}"
    sx, sy, sw, sh = layout["stage"]
    assert sw >= 340 and abs(sw / sh - 16 / 9) < 0.02, layout

    for selector in CONTROLS:
        assert_reachable(tp, selector)

    # Taps on the buttons.
    tp.page.locator("#btn-next").tap()
    pos += 1
    expect(pos, "tap Next")
    tp.page.locator("#btn-prev").tap()
    pos -= 1
    expect(pos, "tap Previous")

    # Swipes on the stage.
    tp.page.locator("#stage-frame").scroll_into_view_if_needed()
    box = tp.page.locator("#stage-frame").bounding_box()
    cy = box["y"] + box["height"] / 2
    swipe(tp, engine, box["x"] + box["width"] * 0.8, cy, box["x"] + box["width"] * 0.2, cy)
    pos += 1
    expect(pos, "swipe left")
    swipe(tp, engine, box["x"] + box["width"] * 0.2, cy, box["x"] + box["width"] * 0.8, cy)
    pos -= 1
    expect(pos, "swipe right")
    # A short or mostly vertical drag is not a swipe.
    swipe(tp, engine, box["x"] + box["width"] * 0.5, cy, box["x"] + box["width"] * 0.5 + 20, cy)
    expect(pos, "short drag")
    swipe(
        tp,
        engine,
        box["x"] + box["width"] * 0.6,
        box["y"] + 20,
        box["x"] + box["width"] * 0.5,
        box["y"] + box["height"] - 10,
    )
    expect(pos, "vertical drag")

    # Taps on the stage halves.
    tp.page.touchscreen.tap(box["x"] + box["width"] * 0.8, cy)
    pos += 1
    expect(pos, "tap right half")
    tp.page.touchscreen.tap(box["x"] + box["width"] * 0.2, cy)
    pos -= 1
    expect(pos, "tap left half")

    # Pause by tap.
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.wait_videos_playing(min_time=0.1)
    tp.page.locator("#btn-pause").tap()
    wait_until(tp.page, "() => window.__talk.media().every((m) => m.el.paused)")
    tp.assert_clean()


def test_phone_pseudo_fullscreen_keeps_controls(open_talk, talk: Talk, engine: str):
    tp = open_talk(**PHONE)
    tp.page.add_init_script(NO_ELEMENT_FULLSCREEN)
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.wait_videos_playing(min_time=0.1)
    assert tp.page.evaluate("() => typeof document.documentElement.requestFullscreen") == "undefined"
    # Outside full screen the extra controls are not shown.
    assert tp.page.evaluate("() => getComputedStyle(document.getElementById('stage-tools')).display") == "none"

    tp.page.locator("#btn-fullscreen").tap()
    wait_until(tp.page, "() => document.documentElement.classList.contains('is-pseudo-fullscreen')")
    frame = tp.page.locator("#stage-frame").bounding_box()
    assert (frame["x"], frame["y"], frame["width"], frame["height"]) == (0, 0, 390, 844), frame
    stage = tp.stage_rect()
    for selector in ("#btn-pause-fs", "#btn-exit-fs"):
        x, y, w, h = assert_reachable(tp, selector)
        # In portrait the letterbox band holds them: they never cover the slide.
        assert y + h <= stage[1] or y >= stage[1] + stage[3], f"{selector} covers the slide"

    # The page hidden behind the stage is inert: Tab (and VoiceOver) reach only what is on screen.
    assert tp.page.evaluate("(sels) => sels.every((s) => document.querySelector(s).inert)", CHROME)
    assert not tp.page.evaluate("() => document.getElementById('announcer').closest('[inert]')")
    # WebKit, like Safari, moves Tab through buttons only with Option held.
    tab = "Alt+Tab" if engine == "webkit" else "Tab"
    reached = set()
    for _ in range(6):
        tp.press(tab)
        f = tp.page.evaluate(FOCUS_JS)
        if f["body"]:
            continue
        assert f["inFrame"] and f["visible"] and not f["covered"], f"Tab reached a hidden control: {f}"
        reached.add(f["id"])
    assert {"btn-pause-fs", "btn-exit-fs"} <= reached, reached

    # The focus ring on the black backdrop: at least 3:1 in both themes (WCAG 1.4.11).
    tp.page.locator("#btn-pause-fs").focus()
    tp.press(f"Shift+{tab}")
    tp.press(tab)
    for theme in ("light", "dark"):
        tp.page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
        ring = tp.page.evaluate(RING_JS, "#btn-pause-fs")
        assert ring["focusVisible"] and ring["style"] != "none", ring
        parts = [contrast_with_black(_rgb(ring["outline"]))]
        if ring["shadow"] not in ("", "none"):
            parts.append(contrast_with_black(_rgb(ring["shadow"])))
        assert max(parts) >= 3.0, f"{theme} theme: focus ring contrast on black {parts} ({ring})"
    tp.page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")

    tp.page.locator("#btn-pause-fs").tap()
    wait_until(tp.page, "() => window.__talk.state().paused && window.__talk.media().every((m) => m.el.paused)")
    assert tp.page.evaluate("() => document.getElementById('btn-pause-fs').getAttribute('aria-label')") == "Play videos"
    assert tp.page.evaluate("() => document.getElementById('btn-pause').getAttribute('aria-label')") == "Play videos"
    tp.page.locator("#btn-pause-fs").tap()
    wait_until(tp.page, "() => !window.__talk.state().paused && window.__talk.media().every((m) => !m.el.paused)")
    assert position(tp) == (4, 1), "a tap on a full-screen control also navigated"

    # Swipes still navigate in full screen.
    cy = stage[1] + stage[3] / 2
    swipe(tp, engine, 320, cy, 60, cy)
    tp.wait_state(5, 0)

    tp.page.locator("#btn-exit-fs").tap()
    wait_until(tp.page, "() => !document.documentElement.classList.contains('is-pseudo-fullscreen')")
    assert tp.page.evaluate("() => getComputedStyle(document.getElementById('stage-tools')).display") == "none"
    assert not tp.page.evaluate("(sels) => sels.some((s) => document.querySelector(s).inert)", CHROME)
    # Focus goes back where it was before full screen: the button (Chromium focuses
    # a tapped button), or the viewer (WebKit, like Safari, does not).
    back = wait_until(
        tp.page,
        "() => { const a = document.activeElement; return a && ['btn-fullscreen', 'viewer'].includes(a.id) && a.id; }",
        timeout=3,
    )
    assert back == ("btn-fullscreen" if engine == "chromium" else "viewer"), back
    assert_reachable(tp, "#btn-fullscreen")
    tp.assert_clean()


def test_about_explains_swipes_on_the_youtube_slide(open_talk, talk: Talk):
    """Touches inside YouTube's cross-origin frame never reach the page, so the help says so."""
    tp = open_talk(**PHONE)
    tp.goto()
    hint = tp.page.evaluate("() => document.querySelector('.about__hint').textContent")
    assert "swipe" in hint and "YouTube" in hint and "outside the video" in hint, hint


def test_phone_landscape_pseudo_fullscreen(open_talk, talk: Talk, engine: str):
    tp = open_talk(**{**PHONE, "viewport": {"width": 844, "height": 390}})
    tp.page.add_init_script(NO_ELEMENT_FULLSCREEN)
    tp.goto(talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.page.locator("#btn-fullscreen").tap()
    wait_until(tp.page, "() => document.documentElement.classList.contains('is-pseudo-fullscreen')")
    stage = tp.stage_rect()
    for selector in ("#btn-pause-fs", "#btn-exit-fs"):
        x, y, w, h = assert_reachable(tp, selector)
        # In landscape the pillarbox holds them.
        assert x >= stage[0] + stage[2] or x + w <= stage[0], f"{selector} covers the slide: {[x, y, w, h]} {stage}"
    tp.page.locator("#btn-exit-fs").tap()
    wait_until(tp.page, "() => !document.documentElement.classList.contains('is-pseudo-fullscreen')")
    tp.assert_clean()
