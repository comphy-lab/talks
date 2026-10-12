"""(l) The download control: the manifest's optional ``downloads`` as a disclosure panel of links.

The real manifest is served with its ``downloads`` replaced (or removed)
through route interception, so these tests hold whether or not the build
lists any. The files themselves are never fetched, except where a test
follows a link: that request is answered with 204 No Content, which leaves
the talk page in place.
"""

from __future__ import annotations

import json
import re

import pytest

from talk_helpers import Talk, TalkPage, wait_until

ABSENT = object()

DOWNLOADS = [
    {"format": "pdf", "file": "0123456789abcdef/Sanjay-singular-bursting-bubbles-2026-10-12.pdf", "bytes": 51234567},
    {"format": "pptx", "file": "fedcba9876543210/Sanjay-singular-bursting-bubbles-2026-10-12.pptx", "bytes": 104857600},
    {"format": "key", "file": "00112233aabbccdd/Sanjay-singular-bursting-bubbles-2026-10-12.key", "bytes": 110000000},
]
EXPECTED = [
    ("pdf", "PDF", "51 MB", "One page per slide; movies as still frames"),
    ("pptx", "PowerPoint", "105 MB", "Every click as here; movies play through"),
    ("key", "Keynote", "110 MB", "Every click as here; movies play through"),
]

# Every entry here must be left out.
MALFORMED = [
    None,
    42,
    "pdf",
    [],
    {"format": "zip", "file": "0123456789abcdef/talk.zip", "bytes": 1000},
    {"format": "PDF", "file": "0123456789abcdef/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcdef/talk.pptx", "bytes": 1000},
    {"format": "pdf", "file": "0123456789ABCDEF/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcde/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "../0123456789abcdef/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcdef/../talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcdef/a/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "https://example.org/0123456789abcdef/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "//example.org/0123456789abcdef/talk.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcdef/talk name.pdf", "bytes": 1000},
    {"format": "pdf", "file": "0123456789abcdef/talk.pdf", "bytes": 0},
    {"format": "pdf", "file": "0123456789abcdef/talk.pdf", "bytes": -5},
    {"format": "pdf", "file": "0123456789abcdef/talk.pdf", "bytes": 1.5},
    {"format": "pdf", "file": "0123456789abcdef/talk.pdf", "bytes": "1000"},
    {"format": "pdf", "file": "0123456789abcdef/talk.pdf"},
    {"format": "pdf", "bytes": 1000},
    {"file": "0123456789abcdef/talk.pdf", "bytes": 1000},
]

PHONE = {"has_touch": True, "is_mobile": True, "device_scale_factor": 3}

PANEL_JS = """
() => {
  const box = (node) => {
    const r = node.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height };
  };
  const shown = (node) => {
    const cs = getComputedStyle(node);
    return node.getClientRects().length > 0 && cs.display !== 'none' && cs.visibility === 'visible';
  };
  const button = document.getElementById('btn-download');
  const panel = document.getElementById('downloads');
  const note = document.getElementById('downloads-note');
  const tools = document.querySelector('.controls__tools');
  return {
    buttonHidden: button.hidden, buttonShown: shown(button),
    expanded: button.getAttribute('aria-expanded'), controls: button.getAttribute('aria-controls'),
    label: button.getAttribute('aria-label'), title: button.getAttribute('title'),
    panelHidden: panel.hidden, panelShown: shown(panel),
    order: [...tools.children].map((c) => c.id),
    note: { hidden: note.hidden, shown: shown(note), text: note.textContent },
    button: box(button), panel: box(panel), tools: box(tools),
    stage: box(document.getElementById('stage')),
    header: box(document.querySelector('.s-header')),
    vw: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth,
  };
}
"""

LINKS_JS = """
() => [...document.querySelectorAll('#downloads-list > li')].map((li) => {
  const a = li.querySelector('a');
  const described = (a.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)
    .map((id) => { const node = document.getElementById(id); return node ? node.textContent : null; });
  const r = li.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const text = (sel) => { const node = li.querySelector(sel); return node ? node.textContent : null; };
  // Every part of the row lies inside it (no wrapped text spilling into the next row).
  const contained = [...li.children].every((child) => {
    const c = child.getBoundingClientRect();
    return c.top >= r.top - 0.5 && c.bottom <= r.bottom + 0.5 && c.left >= r.left - 0.5 && c.right <= r.right + 0.5;
  });
  return { format: a.dataset.format, label: a.textContent, href: a.href, rel: a.getAttribute('rel'),
           target: a.getAttribute('target'), download: a.hasAttribute('download'),
           links: li.querySelectorAll('a').length,
           size: text('.downloads__size'), note: text('.downloads__note'), described,
           rect: [r.left, r.top, r.right, r.bottom], hitsLink: hit === a, contained };
})
"""

OPEN_JS = """
() => [!document.getElementById('downloads').hidden,
       document.getElementById('btn-download').getAttribute('aria-expanded')]
"""

ACTIVE_JS = "() => document.activeElement ? document.activeElement.id || document.activeElement.tagName : null"
FULLSCREEN_JS = (
    "() => Boolean(document.fullscreenElement || document.webkitFullscreenElement)"
    " || document.documentElement.classList.contains('is-pseudo-fullscreen')"
)

COLOURS_JS = """
() => {
  const panel = document.getElementById('downloads');
  const colour = (sel) => getComputedStyle(panel.querySelector(sel)).color;
  return { background: getComputedStyle(panel).backgroundColor, title: colour('.downloads__title'),
           label: colour('.downloads__link'), size: colour('.downloads__size'), note: colour('.downloads__note') };
}
"""


# ----------------------------------------------------------------- helpers


def serve_downloads(tp: TalkPage, downloads=ABSENT) -> None:
    """Serve the real manifest, read at request time, with ``downloads`` replaced (or removed)."""

    def handle(route) -> None:
        response = route.fetch()
        manifest = response.json()
        manifest.pop("downloads", None)
        if downloads is not ABSENT:
            manifest["downloads"] = downloads
        route.fulfill(status=200, content_type="application/json", body=json.dumps(manifest))

    tp.page.route(f"{tp.base}/talks/{tp.talk.slug}/manifest.json", handle)


def open_with(open_talk, downloads=DOWNLOADS, hash_: str = "", **options) -> TalkPage:
    tp = open_talk(**options)
    serve_downloads(tp, downloads)
    tp.goto(hash_)
    return tp


def follow_files(tp: TalkPage) -> list[str]:
    """Record requests for download files, from the page or a tab it opens.

    A PDF gets a tiny PDF (Chromium opens no tab for a 204); any other file
    gets 204 No Content, so the talk page stays.
    """
    seen: list[str] = []

    def handle(route) -> None:
        seen.append(route.request.url)
        if route.request.url.endswith(".pdf"):
            route.fulfill(status=200, body="%PDF-1.4\n%%EOF\n", content_type="application/pdf")
        else:
            route.fulfill(status=204, body="")

    pattern = re.compile(rf"^{re.escape(tp.base)}/media/{re.escape(tp.talk.slug)}/[0-9a-f]{{16}}/")
    tp.page.context.route(pattern, handle)
    return seen


def wait_for_len(tp: TalkPage, items: list, n: int, timeout_ms: int = 5000) -> None:
    for _ in range(timeout_ms // 50):
        if len(items) >= n:
            return
        tp.page.wait_for_timeout(50)
    raise AssertionError(f"expected {n} requests, saw {items}")


def is_open(tp: TalkPage) -> bool:
    shown, expanded = tp.page.evaluate(OPEN_JS)
    assert expanded == ("true" if shown else "false"), (shown, expanded)
    return shown


def position(tp: TalkPage) -> tuple[int, int]:
    st = tp.state()
    return st["slide"], st["stateIndex"]


def step_position(talk: Talk, index: int) -> tuple[int, int]:
    step = talk.steps[index]
    return step.n, step.k


def contrast(fg: str, bg: str) -> float:
    def lum(css: str) -> float:
        r, g, b = (float(v) for v in re.findall(r"[\d.]+", css)[:3])

        def lin(c: float) -> float:
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------- tests


@pytest.mark.parametrize(
    "downloads",
    [ABSENT, [], None, "pdf", DOWNLOADS[0], MALFORMED],
    ids=["absent", "empty", "null", "string", "object", "all-malformed"],
)
def test_no_valid_download_hides_the_control(open_talk, downloads):
    tp = open_with(open_talk, downloads)
    s = tp.page.evaluate(PANEL_JS)
    assert s["buttonHidden"] and not s["buttonShown"], s
    assert s["panelHidden"] and not s["panelShown"], s
    assert s["note"] == {"hidden": True, "shown": False, "text": ""}
    assert tp.page.evaluate(LINKS_JS) == []
    assert tp.page.is_visible("#btn-pause") and tp.page.is_visible("#btn-fullscreen")
    tp.assert_clean()


def test_panel_lists_each_download(open_talk, talk: Talk):
    tp = open_with(open_talk)
    s = tp.page.evaluate(PANEL_JS)
    assert not s["buttonHidden"] and s["buttonShown"], s
    assert s["order"] == ["btn-download", "downloads", "btn-pause", "btn-fullscreen"]
    assert (s["label"], s["title"], s["controls"], s["expanded"]) == (
        "Download this talk",
        "Download",
        "downloads",
        "false",
    )
    assert s["button"]["width"] >= 44 and s["button"]["height"] >= 44
    assert s["panelHidden"] and not s["panelShown"]
    assert s["note"]["shown"]
    assert s["note"]["text"] == " The download button offers the slides as a PDF, PowerPoint or Keynote file."
    stage = s["stage"]

    tp.page.click("#btn-download")
    s = tp.page.evaluate(PANEL_JS)
    assert s["expanded"] == "true" and s["panelShown"], s
    assert s["stage"] == stage, "opening the panel moved or resized the stage"
    p = s["panel"]
    assert p["bottom"] <= s["tools"]["top"] + 0.5, "the panel is not above the toolbar"
    assert abs(p["right"] - s["button"]["right"]) <= 1, "the panel is not right-aligned to the button"
    assert p["left"] >= 0 and p["right"] <= s["vw"]

    links = tp.page.evaluate(LINKS_JS)
    assert [(a["format"], a["label"], a["size"], a["note"]) for a in links] == EXPECTED
    for a, d in zip(links, DOWNLOADS, strict=True):
        assert a["href"] == tp.media_url(d["file"])
        assert a["rel"] == "noopener" and not a["download"]
        assert a["target"] == ("_blank" if a["format"] == "pdf" else None)
        assert a["links"] == 1
        assert a["described"] == [a["size"], a["note"]]
        assert a["hitsLink"], f"{a['format']}: the row is not the link's target"
        assert a["contained"], f"{a['format']}: text spills out of its row"
        assert a["rect"][3] - a["rect"][1] >= 44
    tp.assert_clean()


def test_sizes_round_to_whole_megabytes_from_one_megabyte(open_talk):
    sizes = [
        {**DOWNLOADS[0], "bytes": 999_999},  # rounds to 1.0 MB: whole megabytes
        {**DOWNLOADS[1], "bytes": 949_999},
        {**DOWNLOADS[2], "bytes": 40_000},
    ]
    tp = open_with(open_talk, sizes)
    tp.page.click("#btn-download")
    assert [a["size"] for a in tp.page.evaluate(LINKS_JS)] == ["1 MB", "0.9 MB", "0.1 MB"]
    tp.assert_clean()


def test_links_use_the_manifest_media_base(open_talk, talk: Talk):
    # Without the localhost ?media= override, the files are on media_base.
    tp = open_talk(media="cdn")
    serve_downloads(tp, DOWNLOADS)
    tp.goto(media=False)
    links = tp.page.evaluate(LINKS_JS)
    assert [a["href"] for a in links] == [talk.manifest["media_base"] + d["file"] for d in DOWNLOADS]
    tp.assert_clean()


def test_malformed_entries_are_left_out(open_talk):
    mixed = [
        *MALFORMED[:8],
        {"format": "key", "file": "00112233aabbccdd/talk.key", "bytes": 523_000},
        *MALFORMED[8:],
        {"format": "key", "file": "aabbccdd00112233/again.key", "bytes": 2_000_000},  # a repeated format
        {"format": "pdf", "file": "0123456789abcdef/talk.pdf", "bytes": 1_499_999},
    ]
    tp = open_with(open_talk, mixed)
    links = tp.page.evaluate(LINKS_JS)
    assert [(a["format"], a["label"], a["size"]) for a in links] == [
        ("key", "Keynote", "0.5 MB"),
        ("pdf", "PDF", "1 MB"),
    ]
    assert [a["href"] for a in links] == [
        tp.media_url("00112233aabbccdd/talk.key"),
        tp.media_url("0123456789abcdef/talk.pdf"),
    ]
    note = tp.page.evaluate(PANEL_JS)["note"]
    assert note["text"] == " The download button offers the slides as a Keynote or PDF file."
    tp.assert_clean()


def test_panel_closes_on_button_escape_and_outside_clicks(open_talk, talk: Talk):
    tp = open_with(open_talk)
    page = tp.page
    start = position(tp)

    # A second press of the button.
    page.click("#btn-download")
    assert is_open(tp)
    page.click("#btn-download")
    assert not is_open(tp)

    # Escape, from a link inside the panel: focus returns to the button.
    page.click("#btn-download")
    page.focus("#downloads a[data-format='pptx']")
    tp.press("Escape")
    assert not is_open(tp)
    assert page.evaluate(ACTIVE_JS) == "btn-download"

    # A click on the stage's right half only closes the panel.
    page.click("#btn-download")
    x, y, w, h = tp.stage_rect()
    page.mouse.click(x + 0.75 * w, y + 0.3 * h)
    assert not is_open(tp)
    page.wait_for_timeout(400)
    assert position(tp) == start, "the click that closed the panel also changed slide"
    # With the panel closed, the same click moves on.
    page.mouse.click(x + 0.75 * w, y + 0.3 * h)
    tp.wait_state(*step_position(talk, 1))

    # A click on another control closes the panel and acts.
    page.click("#btn-download")
    page.click("#btn-next")
    assert not is_open(tp)
    tp.wait_state(*step_position(talk, 2))

    # A click elsewhere on the page.
    page.click("#btn-download")
    page.click("#about-title")
    assert not is_open(tp)
    assert position(tp) == step_position(talk, 2)
    tp.assert_clean()


def test_open_panel_takes_only_escape(open_talk, talk: Talk):
    tp = open_with(open_talk)
    page = tp.page
    page.evaluate(
        "() => { window.__keys = [];"
        " window.addEventListener('keydown', (e) => window.__keys.push([e.key, e.defaultPrevented])); }"
    )
    escapes = "() => window.__keys.filter(([key]) => key === 'Escape')"

    page.click("#btn-download")
    assert is_open(tp)
    # Player keys keep working while it is open.
    tp.press("ArrowRight")
    tp.wait_state(*step_position(talk, 1))
    for _ in range(2):
        was = tp.state()["paused"]
        tp.press("p")
        wait_until(page, "(want) => window.__talk.state().paused === want", not was)
    assert is_open(tp)

    # Escape closes the panel, and is consumed: nothing else happens.
    before = tp.state()
    tp.press("Escape")
    assert not is_open(tp)
    assert page.evaluate(ACTIVE_JS) == "btn-download"
    page.wait_for_timeout(300)
    assert tp.state() == before
    assert not page.evaluate(FULLSCREEN_JS)
    assert page.evaluate(escapes) == [["Escape", True]]

    # With the panel closed, the player leaves Escape alone.
    tp.press("Escape")
    assert page.evaluate(escapes) == [["Escape", True], ["Escape", False]]
    assert tp.state() == before

    # Enter and Space activate the focused button rather than change slide.
    page.focus("#btn-download")
    tp.press("Enter")
    assert is_open(tp)
    tp.press("Enter")
    assert not is_open(tp)
    tp.press("Space")
    assert is_open(tp)
    tp.press("Space")
    assert not is_open(tp)
    page.wait_for_timeout(300)
    assert position(tp) == step_position(talk, 1)
    tp.assert_clean()


def test_following_a_link_closes_the_panel(open_talk, talk: Talk):
    tp = open_with(open_talk)
    page = tp.page
    seen = follow_files(tp)
    page.evaluate("() => { window.__samePage = true; }")

    # The PDF opens in a new tab, which browsers show rather than save; the talk stays.
    page.click("#btn-download")
    with page.context.expect_page() as tab:  # in Chromium a noopener tab is not the page's "popup"
        page.click("#downloads a[data-format='pdf']")
    wait_for_len(tp, seen, 1)
    tab.value.close()
    assert seen == [tp.media_url(DOWNLOADS[0]["file"])]
    assert not is_open(tp)
    assert page.evaluate(ACTIVE_JS) == "btn-download"

    # Enter on a focused link follows it as well, and does not change slide.
    page.click("#btn-download")
    page.focus("#downloads a[data-format='key']")
    tp.press("Enter")
    wait_for_len(tp, seen, 2)
    assert seen[1] == tp.media_url(DOWNLOADS[2]["file"])
    assert not is_open(tp)
    assert page.evaluate(ACTIVE_JS) == "btn-download"
    page.wait_for_timeout(300)
    assert position(tp) == step_position(talk, 0)
    assert page.evaluate("() => window.__samePage === true"), "the talk page was replaced"
    tp.assert_clean()


def test_full_screen_closes_the_panel(open_talk, talk: Talk):
    tp = open_with(open_talk)
    page = tp.page
    page.click("#btn-download")
    page.focus("#downloads a[data-format='pdf']")
    tp.press("f")
    wait_until(page, FULLSCREEN_JS, timeout=5)
    assert not is_open(tp)
    tp.press("f")
    wait_until(page, f"() => !({FULLSCREEN_JS})()", timeout=5)
    # Focus was on a link in the closed panel; it comes back to the button.
    wait_until(page, "() => document.activeElement && document.activeElement.id === 'btn-download'", timeout=3)
    assert not is_open(tp)
    tp.assert_clean()


@pytest.mark.parametrize("width", [390, 320])
def test_phone_panel_stays_inside_the_viewport(open_talk, talk: Talk, width: int):
    tp = open_with(open_talk, viewport={"width": width, "height": 844}, **PHONE)
    page = tp.page
    before = page.evaluate(PANEL_JS)
    page.tap("#btn-download")
    s = page.evaluate(PANEL_JS)
    assert s["panelShown"] and s["expanded"] == "true", s
    assert s["stage"] == before["stage"], "opening the panel moved or resized the stage"
    p = s["panel"]
    assert 0 <= p["left"] and p["right"] <= s["vw"], (p, s["vw"])
    assert p["top"] >= s["header"]["bottom"], "the panel reaches under the fixed header"
    assert p["bottom"] <= s["tools"]["top"] + 0.5, "the panel is not above the toolbar"
    assert s["scrollW"] <= s["vw"], "the panel widened the page"
    if width == 390:
        assert abs(p["right"] - s["button"]["right"]) <= 1, "the panel is not right-aligned to the button"
    for a in page.evaluate(LINKS_JS):
        left, top, right, bottom = a["rect"]
        assert 0 <= left and right <= s["vw"] and bottom - top >= 44, a
        assert a["hitsLink"] and a["contained"], a

    # A narrower window while it is open (the panel narrows and its rows grow), then back.
    # Below 320 px the site header itself is a pixel wider than the page, so
    # here the panel's own box is checked against the viewport, not scrollWidth.
    for w in (width - 30, width):
        page.set_viewport_size({"width": w, "height": 844})
        page.wait_for_timeout(200)
        s = page.evaluate(PANEL_JS)
        p = s["panel"]
        assert s["panelShown"] and 0 <= p["left"] and p["right"] <= s["vw"], (w, p, s["vw"])
        assert p["bottom"] <= s["tools"]["top"] + 0.5, (w, p, s["tools"])
        for a in page.evaluate(LINKS_JS):
            assert a["contained"] and a["hitsLink"], (w, a)

    # Readable in both themes (WCAG 1.4.3 for all of its text).
    for theme in ("light", "dark"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.wait_for_timeout(400)  # the design system's link colour transition (160 ms)
        c = page.evaluate(COLOURS_JS)
        for part in ("title", "label", "size", "note"):
            ratio = contrast(c[part], c["background"])
            assert ratio >= 4.5, f"{theme} {part}: {c[part]} on {c['background']} is {ratio:.2f}:1"
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")

    # A tap on the stage's right half, clear of the panel (which covers most
    # of a 320 px stage), closes the panel without changing slide.
    start = position(tp)
    x, y, w, h = tp.stage_rect()
    clear = [
        (px, py)
        for px in (x + f * w for f in (0.6, 0.75, 0.9, 0.97))
        for py in (y + f * h for f in (0.1, 0.4, 0.7))
        if not (p["left"] <= px <= p["right"] and p["top"] <= py <= p["bottom"])
    ]
    assert clear, "the panel covers the whole right half of the stage"
    page.touchscreen.tap(*clear[0])
    assert not is_open(tp)
    page.wait_for_timeout(400)
    assert position(tp) == start
    tp.assert_clean()


def test_phone_menu_drawer_closes_the_panel_and_keeps_its_keys(open_talk, talk: Talk):
    tp = open_with(open_talk, viewport={"width": 390, "height": 844}, **PHONE)
    page = tp.page
    start = position(tp)
    page.tap("#btn-download")
    assert is_open(tp)
    page.tap(".s-header__menu-toggle")
    assert not is_open(tp)
    wait_until(page, "() => document.querySelector('.s-header__menu-toggle').getAttribute('aria-expanded') === 'true'")
    # The open drawer still owns Escape: it closes and focus returns to Menu.
    tp.press("Escape")
    wait_until(page, "() => document.querySelector('.s-header__menu-toggle').getAttribute('aria-expanded') === 'false'")
    assert page.evaluate("() => document.activeElement === document.querySelector('.s-header__menu-toggle')")
    assert not is_open(tp)
    assert position(tp) == start
    tp.assert_clean()
