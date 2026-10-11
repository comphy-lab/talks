"""Real-CDN playback; a locked Mac's WebKit diagnostic is explicitly distinguished."""

from __future__ import annotations

import ctypes
import sys
import time
import urllib.parse

import pytest

from talk_helpers import Talk, wait_until


def _locked_mac():
    """Read one session flag, without exposing the rest of the login-session dictionary."""
    if sys.platform != "darwin":
        return None
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return None
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        try:
            value = cf.CFDictionaryGetValue(session, key)
            return bool(cf.CFBooleanGetValue(value)) if value else False
        finally:
            cf.CFRelease(key)
            cf.CFRelease(session)
    except (AttributeError, OSError):
        return None


def _production_origin(tp, talk, pytestconfig):
    """Serve only our HTML/JS locally; media and YouTube remain genuinely unrouted.

    The production origin is needed for the media bucket's existing CORS
    policy. Intercepting CDN media itself hides the diagnosed WebKit delay.
    """
    site = pytestconfig.getoption("--site").resolve()
    base = f"https://comphy-lab.org/talks/{talk.slug}/"
    html = (site / talk.slug / "index.html").read_text()
    player = (site / "player" / "player.js").read_text()
    tp.page.context.unroute_all()
    tp.page.route(base, lambda route: route.fulfill(body=html, content_type="text/html"))
    tp.page.route(
        lambda url: urllib.parse.urlsplit(url).scheme == "https"
        and urllib.parse.urlsplit(url).netloc == "comphy-lab.org"
        and urllib.parse.urlsplit(url).path == "/talks/player/player.js",
        lambda route: route.fulfill(body=player, content_type="text/javascript"),
    )
    return base


def _watch_traffic(page):
    requests, streamed = [], []
    page.on("request", lambda request: requests.append(request.url))

    def response_received(response):
        parts = urllib.parse.urlsplit(response.url)
        if (parts.hostname or "").endswith(".googlevideo.com") and parts.path.endswith("/videoplayback"):
            if response.status in (200, 206):
                streamed.append(time.monotonic())

    page.on("response", response_received)
    return requests, streamed


def _wait_stream(page, streamed):
    deadline = time.monotonic() + 30
    while not streamed and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert streamed, "YouTube never started a successful googlevideo.com media response"


def _native_seconds(tp, talk, target_slide):
    started = time.monotonic()
    target = talk.step(target_slide, 1 if target_slide == 5 else 0)
    tp.page.evaluate("(h) => { location.hash = h; }", talk.hash_for(target.n, target.k))
    tp.wait_state(target.n, target.k, timeout=2)
    tp.wait_videos_playing(min_time=0.01, timeout=2)
    elapsed = time.monotonic() - started
    assert elapsed < 2, f"{tp.engine}: native media waited {elapsed:.2f}s after the YouTube slide"
    return elapsed


@pytest.mark.live
@pytest.mark.parametrize("dwell_ms", [4000, 8000, 24000])
@pytest.mark.parametrize("target_slide", [5, 21])
def test_native_video_after_youtube(open_talk, talk: Talk, engine: str, pytestconfig, dwell_ms, target_slide):
    tp = open_talk(youtube="live", viewport={"width": 1440, "height": 900})
    base = _production_origin(tp, talk, pytestconfig)
    _, streamed = _watch_traffic(tp.page)
    step = next(s for s in talk.steps if any(k.startswith("youtube:") for k in s.keys))
    tp.page.goto(base + talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    wait_until(tp.page, "() => document.querySelector('.media--youtube iframe') !== null", timeout=20)
    _wait_stream(tp.page, streamed)
    tp.page.wait_for_timeout(dwell_ms)
    try:
        elapsed = _native_seconds(tp, talk, target_slide)
    except AssertionError:
        if engine == "webkit" and _locked_mac() is True:
            pytest.xfail("talks #1: locked-host Playwright WebKit delay; foreground native Safari passed")
        raise
    print(f"\n{engine}: slide {target_slide} after {dwell_ms / 1000:g}s: {elapsed:.2f}s")


@pytest.mark.live
def test_webkit_locked_host_diagnostic(open_talk, talk: Talk, engine: str, pytestconfig):
    """Preserve the reported live path; no user-agent override changes the viewer's experience."""
    if engine != "webkit":
        pytest.skip("WebKit diagnostic; Chromium inline playback is covered above")
    tp = open_talk(youtube="live", viewport={"width": 1440, "height": 900})
    base = _production_origin(tp, talk, pytestconfig)
    _, streamed = _watch_traffic(tp.page)
    step = next(s for s in talk.steps if any(k.startswith("youtube:") for k in s.keys))
    tp.page.goto(base + talk.hash_for(step.n, step.k))
    tp.wait_state(step.n, step.k)
    wait_until(tp.page, "() => document.querySelector('.media--youtube iframe') !== null", timeout=20)
    _wait_stream(tp.page, streamed)
    tp.page.wait_for_timeout(4000)
    try:
        elapsed = _native_seconds(tp, talk, 21)
    except AssertionError:
        if _locked_mac() is True:
            pytest.xfail("talks #1 reproduced in locked-host Playwright WebKit; not native foreground Safari")
        raise
    print(f"\nWebKit diagnostic: host_locked={_locked_mac()}, native_seconds={elapsed:.2f}")
