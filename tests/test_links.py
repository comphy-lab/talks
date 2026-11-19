"""Slide links (SPEC 1.2 and 3.2): clickable areas over the stage, and the page's list of them."""

from __future__ import annotations

import pytest

from talk_helpers import Talk, wait_until

LINKS_JS = """() => [...document.querySelectorAll('#stage-links a.slide-link')].map((a) => {
  const r = a.getBoundingClientRect();
  return { href: a.href, target: a.target, rel: a.rel, name: a.textContent,
           box: [r.left, r.top, r.width, r.height] };
})"""
STAGE_JS = "() => { const r = document.getElementById('stage').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }"


def linked(talk: Talk) -> list[dict]:
    slides = [s for s in talk.manifest["slides"] if s.get("links")]
    if not slides:
        pytest.skip(f"{talk.slug} has no slide links")
    return slides


def test_links_sit_over_their_rects(open_talk, talk: Talk):
    tp = open_talk()
    for slide in linked(talk):
        tp.goto(talk.hash_for(slide["n"]))
        tp.wait_state(slide["n"])
        links = wait_until(tp.page, f"() => {{ const l = ({LINKS_JS})(); return l.length === {len(slide['links'])} ? l : null; }}")
        sx, sy, sw, sh = tp.page.evaluate(STAGE_JS)
        for want, got in zip(slide["links"], links):
            assert got["href"] == want["url"]
            assert got["target"] == "_blank" and set(got["rel"].split()) >= {"noopener", "noreferrer"}
            assert want["label"] in got["name"]
            x, y, w, h = want["rect"]
            expect = [sx + x / 1920 * sw, sy + y / 1080 * sh, w / 1920 * sw, h / 1080 * sh]
            assert all(abs(a - b) <= 1.5 for a, b in zip(got["box"], expect)), (slide["n"], got["box"], expect)


def test_links_follow_the_slide(open_talk, talk: Talk):
    tp = open_talk()
    with_links = {s["n"] for s in linked(talk)}
    without = next((s["n"] for s in talk.manifest["slides"] if s["n"] not in with_links), None)
    if without is None:
        pytest.skip("every slide has links")
    tp.goto(talk.hash_for(without))
    tp.wait_state(without)
    assert tp.page.evaluate(LINKS_JS) == []


def test_clicking_a_link_does_not_change_slide(open_talk, talk: Talk):
    tp = open_talk()
    slide = linked(talk)[0]
    tp.goto(talk.hash_for(slide["n"]))
    tp.wait_state(slide["n"])
    want = slide["links"][0]["url"]
    # The destination is answered locally, so the test proves where the link goes.
    tp.page.context.route(want, lambda route: route.fulfill(status=200, body="ok", headers={"Content-Type": "text/plain"}))
    link = tp.page.locator("#stage-links a.slide-link").first
    with tp.page.context.expect_page() as popup:
        link.click()
    popup.value.wait_for_load_state()
    assert popup.value.url == want
    popup.value.close()
    assert tp.state()["slide"] == slide["n"]


def test_page_lists_each_link_once(open_talk, talk: Talk):
    tp = open_talk()
    slides = linked(talk)
    tp.goto(talk.hash_for(1))
    want = []
    for s in slides:
        for ln in s["links"]:
            if ln["url"] not in [u for u, _ in want]:
                want.append((ln["url"], s["n"]))
    got = tp.page.evaluate(
        """() => { const card = document.getElementById('links-card');
          return { hidden: card.hidden, items: [...document.querySelectorAll('#talk-links li')].map((li) => {
            const [ext, slide] = li.querySelectorAll('a'); return [ext.href, slide.textContent]; }) }; }"""
    )
    assert got["hidden"] is False
    assert got["items"] == [[u, f"slide {n}"] for u, n in want]


def test_card_slide_link_shows_the_slide_in_view(open_talk, talk: Talk):
    tp = open_talk()
    slide = linked(talk)[-1]
    tp.goto(talk.hash_for(1))
    tp.wait_state(1)
    item = tp.page.locator("#talk-links .talk-links__slide").last
    item.scroll_into_view_if_needed()
    target = int(item.text_content().split()[-1])
    item.click()
    tp.wait_state(target)
    # The scroll is smooth unless reduced motion is asked for: wait for it to arrive.
    got = wait_until(
        tp.page,
        """() => { const r = document.getElementById('viewer').getBoundingClientRect();
          return Math.abs(r.top) < 120 ? { top: r.top, focus: document.activeElement && document.activeElement.id } : null; }""",
        timeout=4,
    )
    assert got["focus"] == "stage-frame", got
    assert slide["n"] >= target


def test_focus_stays_on_the_stage_when_its_link_goes(open_talk, talk: Talk):
    tp = open_talk()
    with_links = {s["n"] for s in linked(talk)}
    total = len(talk.manifest["slides"])
    pairs = [(s["n"], s["n"] + d, key) for s in linked(talk) for d, key in ((1, "ArrowRight"), (-1, "ArrowLeft"))
             if 1 <= s["n"] + d <= total and s["n"] + d not in with_links]
    if not pairs:
        pytest.skip("no linked slide has an unlinked neighbour")
    n, neighbour, key = pairs[0]
    tp.goto(talk.hash_for(n, len(talk.slide(n)["states"]) - 1 if key == "ArrowRight" else 0))
    tp.wait_state(n, len(talk.slide(n)["states"]) - 1 if key == "ArrowRight" else 0)
    tp.page.locator("#stage-links a.slide-link").first.focus()
    tp.press(key)
    tp.wait_state(neighbour, 0 if key == "ArrowRight" else len(talk.slide(neighbour)["states"]) - 1)
    assert tp.page.evaluate("() => document.activeElement && document.activeElement.id") == "stage-frame"
