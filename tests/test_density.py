"""Density selection, network use and media continuity in Chromium and WebKit."""

from __future__ import annotations

import copy
import urllib.parse

import numpy as np
import pytest
from PIL import Image
from talk_helpers import wait_until


def _step_with_overlay(talk):
    return next(s for s in talk.steps if s.video_keys and s.state.get("overlay") and s.state.get("image2x"))


def _wait_pair(tp, state, scale):
    suffix = "2x" if scale == 2 else ""
    return wait_until(
        tp.page,
        """({image, overlay, width}) => {
          const img = document.querySelector('#layer-image > img:last-child');
          const ov = document.querySelector('#layer-overlay > img:last-child');
          const matches = (node, file) => node && node.complete && node.naturalWidth === width &&
            new URL(node.src).pathname.endsWith('/' + file);
          return matches(img, image) && (overlay ? matches(ov, overlay) : !ov);
        }""",
        {"image": state[f"image{suffix}"], "overlay": state[f"overlay{suffix}"], "width": 1920 * scale},
        timeout=10,
    )


def test_built_density_pairs_agree_on_every_state(talk):
    """Check the complete real talk, including masks, citations and raster content."""
    for step in talk.steps:
        if not step.state.get("image2x"):
            pytest.fail(f"state {step.label} has no 2x image")
        canvases = []
        for suffix, scale in (("", 1), ("2x", 2)):
            with Image.open(talk.media_path(step.state[f"image{suffix}"])) as im:
                canvas = im.convert("RGBA")
            assert canvas.size == (1920 * scale, 1080 * scale), step.label
            overlay = step.state[f"overlay{suffix}"]
            if overlay:
                with Image.open(talk.media_path(overlay)) as im:
                    assert im.size == canvas.size, step.label
                    canvas.alpha_composite(im.convert("RGBA"))
            if scale == 2:
                canvas = canvas.resize((1920, 1080), Image.Resampling.LANCZOS)
            canvases.append(np.asarray(canvas.convert("RGB"), dtype=np.int16))
        diff = np.abs(canvases[0] - canvases[1]).max(axis=2)
        assert diff.mean() < 3.0, (step.label, float(diff.mean()))
        assert (diff > 32).mean() < 0.02, (step.label, float((diff > 32).mean()))


@pytest.mark.parametrize("dpr", [1, 2])
def test_density_selection_only_fetches_the_needed_assets(open_talk, talk, dpr):
    step = _step_with_overlay(talk)
    tp = open_talk(viewport={"width": 1600, "height": 1100}, device_scale_factor=dpr)
    requests = set()
    tp.page.on("request", lambda r: requests.add(urllib.parse.urlsplit(r.url).path.rsplit("/", 1)[-1]))
    tp.goto(talk.hash_for(step.n, step.k))
    _wait_pair(tp, step.state, dpr)
    # DPR 2 would upscale 1x images here; DPR 1 fits the original images,
    # including while preloading neighbours.
    pixels = tp.stage_rect()[2] * dpr
    assert (pixels > 1920) is (dpr == 2)
    tp.page.wait_for_timeout(250)
    high = {s.state.get(field) for s in talk.steps for field in ("image2x", "overlay2x")} - {None}
    assert bool(requests & high) is (dpr == 2), requests & high
    tp.assert_clean()


def test_density_switches_on_resize_and_fullscreen_without_recreating_video(open_talk, talk):
    step = _step_with_overlay(talk)
    tp = open_talk(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    tp.goto(talk.hash_for(step.n, step.k))
    _wait_pair(tp, step.state, 1)
    assert tp.stage_rect()[2] * 2 <= 1920, "the original images must fit this smaller retina viewport"
    tp.wait_videos_playing(min_time=0.2)
    video = tp.page.query_selector("video")
    src = video.get_attribute("src")

    def same_video():
        assert video.evaluate("(el) => el.isConnected && el === document.querySelector('video')")
        assert video.get_attribute("src") == src
        assert tp.state()["slide"] == step.n and tp.state()["stateIndex"] == step.k

    tp.page.set_viewport_size({"width": 1600, "height": 1100})
    _wait_pair(tp, step.state, 2)
    same_video()
    tp.page.set_viewport_size({"width": 1280, "height": 800})
    _wait_pair(tp, step.state, 1)
    same_video()
    tp.press("f")
    _wait_pair(tp, step.state, 2)
    same_video()
    tp.press("f")
    _wait_pair(tp, step.state, 1)
    same_video()
    assert video.evaluate("(el) => !el.paused && el.currentTime > 0")
    tp.assert_clean()


def test_retina_1440_window_and_fullscreen_use_two_x_without_recreating_videos(open_talk, talk):
    step = _step_with_overlay(talk)
    tp = open_talk(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    tp.goto(talk.hash_for(step.n, step.k))
    _wait_pair(tp, step.state, 2)
    assert tp.stage_rect()[2] * 2 > 1920, "the issue's windowed stage must exceed the 1x image width"
    tp.wait_videos_playing(min_time=0.2)
    videos = tp.page.query_selector_all("#layer-media > video")
    sources = [video.get_attribute("src") for video in videos]

    def same_videos():
        for video, source in zip(videos, sources, strict=True):
            assert video.evaluate(
                "(el) => el.isConnected && [...document.querySelectorAll('#layer-media > video')].includes(el)"
            )
            assert video.get_attribute("src") == source
        assert tp.state()["slide"] == step.n and tp.state()["stateIndex"] == step.k

    tp.press("f")
    _wait_pair(tp, step.state, 2)
    same_videos()
    tp.press("f")
    _wait_pair(tp, step.state, 2)
    same_videos()
    tp.wait_videos_playing(min_time=0.2)
    tp.assert_clean()


def test_schema_one_talk_still_works_on_a_high_density_display(open_talk, talk):
    step = _step_with_overlay(talk)
    manifest = copy.deepcopy(talk.manifest)
    manifest["schema"] = 1
    for slide in manifest["slides"]:
        for state in slide["states"]:
            state.pop("image2x", None)
            state.pop("overlay2x", None)
    tp = open_talk(viewport={"width": 1600, "height": 1100}, device_scale_factor=2)
    tp.page.route("**/manifest.json", lambda route: route.fulfill(json=manifest))
    tp.goto(talk.hash_for(step.n, step.k))
    _wait_pair(tp, step.state, 1)
    tp.assert_clean()


def test_density_refresh_preserves_announcement_until_real_navigation(open_talk, talk):
    slide = next(s for s in talk.manifest["slides"] if len(s["states"]) > 1)
    step = talk.step(slide["n"], 0)
    tp = open_talk(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
    tp.goto(talk.hash_for(step.n, step.k))
    _wait_pair(tp, step.state, 1)
    tp.page.wait_for_timeout(100)
    initial = f"Slide {step.n} of {len(talk.manifest['slides'])}, step 1 of {len(slide['states'])}: {slide['title']}"
    assert tp.page.locator("#announcer").inner_text() == initial
    tp.page.evaluate("""() => {
      window.__densityAnnouncements = [];
      new MutationObserver(() => {
        window.__densityAnnouncements.push(document.getElementById('announcer').textContent);
      }).observe(document.getElementById('announcer'), { childList: true, characterData: true, subtree: true });
    }""")

    def unchanged():
        assert tp.page.locator("#announcer").inner_text() == initial
        assert tp.page.evaluate("() => window.__densityAnnouncements") == []

    tp.page.set_viewport_size({"width": 1600, "height": 1100})
    _wait_pair(tp, step.state, 2)
    unchanged()
    tp.page.set_viewport_size({"width": 1280, "height": 800})
    _wait_pair(tp, step.state, 1)
    unchanged()
    tp.press("f")
    _wait_pair(tp, step.state, 2)
    unchanged()
    tp.press("f")
    _wait_pair(tp, step.state, 1)
    unchanged()

    tp.press("ArrowRight")
    tp.wait_state(step.n, 1)
    wait_until(tp.page, "(expected) => document.getElementById('announcer').textContent === expected",
               f"Step 2 of {len(slide['states'])}")
    assert tp.page.evaluate("() => window.__densityAnnouncements") == [f"Step 2 of {len(slide['states'])}"]
    tp.assert_clean()
