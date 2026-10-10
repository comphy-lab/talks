"""(k) The comphy-lab.org header and footer on the talks index and the talk page.

Both pages carry the main site's header (logo, the same items in the same
order with Talks after Project docs and marked current, the Google Scholar,
GitHub and Bluesky icons, one theme toggle) and its footer v2. The header is
fixed, as on comphy-lab.org, and never covers the stage. Below 1120 px (as on comphy-lab.org) the
items move into a Menu drawer (aria-expanded, focus kept inside, Escape and
a click outside close it), which scrolls on short screens. The header handles
no key the talk player uses; while the drawer is open the player takes none.
"""

from __future__ import annotations

import datetime

import pytest

from talk_helpers import Talk, TalkPage, wait_until

DESKTOP = {"viewport": {"width": 1440, "height": 900}}
PHONE = {"viewport": {"width": 390, "height": 844}, "has_touch": True, "is_mobile": True, "device_scale_factor": 3}
NO_ELEMENT_FULLSCREEN = """
delete Element.prototype.requestFullscreen; delete Element.prototype.webkitRequestFullscreen;
delete Document.prototype.exitFullscreen; delete Document.prototype.webkitExitFullscreen;
"""

SCHOLAR = "https://scholar.google.com/citations?user=tHb_qZoAAAAJ&hl=en"
# (kind, accessible name, href) in comphy-lab.org's order, Talks after Project docs; no command palette.
NAV = [
    ("button", "Switch to dark theme", None),
    ("a", "Google Scholar Profile", SCHOLAR),
    ("a", "GitHub Organization", "https://github.com/comphy-lab"),
    ("a", "Team", "/team/"),
    ("a", "Research", "/research"),
    ("a", "Project docs", "/projects/"),
    ("a", "Talks", "/talks/"),
    ("a", "Teaching", "/teaching"),
    ("a", "Join Us", "/join"),
    ("a", "Blog", "https://blogs.comphy-lab.org/"),
    ("a", "Bluesky Profile", "https://bsky.app/profile/comphy-lab.org"),
]
# The player's keys, less Space and Enter (they press the drawer's focused button) and Escape (it closes
# the drawer).
PLAYER_KEYS = ("ArrowRight", "ArrowDown", "PageDown", "ArrowLeft", "PageUp", "Backspace", "Home", "End", "p", "k", "f")
# Talks is the current section: the page itself on the index, a page inside it on a talk page.
CURRENT = {"index": "page", "talk": "true"}
FOOTER = {
    "Explore": [
        ("Research", "/research"),
        ("Project docs", "/projects/"),
        ("News & talks", "/news/"),
        ("Talks", "/talks/"),
        ("Team", "/team/"),
        ("Teaching", "/teaching"),
    ],
    "Work with us": [
        ("Open positions", "/join"),
        ("Currently working on", "/research#currently"),
        ("Email the PI", "mailto:vatsal.sanjay@durham.ac.uk"),
        ("Durham staff page", "https://www.durham.ac.uk/staff/vatsal-sanjay/"),
    ],
    "Code & data": [
        ("GitHub · CoMPhy", "https://github.com/comphy-lab"),
        ("Basilisk sandbox", "http://basilisk.fr/sandbox/vatsal/"),
        ("Blog", "https://blogs.comphy-lab.org/"),
        ("Google Scholar", SCHOLAR),
    ],
}

SHELL_JS = """
() => {
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  const header = document.querySelector('.s-header');
  const items = [...header.querySelectorAll('.s-header__nav-list > li')].map((li) => {
    const el = li.querySelector('a, button');
    return { kind: el.tagName.toLowerCase(), name: (el.getAttribute('aria-label') || el.textContent).trim(),
             href: el.getAttribute('href'), current: el.getAttribute('aria-current'), rect: rect(el),
             svg: Boolean(el.querySelector('svg')) };
  });
  const logo = header.querySelector('.s-header__logo img');
  const footer = document.querySelector('footer.c-footer');
  const cols = footer ? [...footer.querySelectorAll('.c-footer__col')].map((col) => ({
    title: col.querySelector('h2').textContent.trim(), label: col.getAttribute('aria-label'),
    links: [...col.querySelectorAll('a')].map((a) => [a.textContent.trim(), a.getAttribute('href')]) })) : [];
  const cs = getComputedStyle(header);
  return {
    header: rect(header), position: cs.position, zIndex: cs.zIndex, vw: innerWidth, vh: innerHeight,
    logo: { alt: logo.alt, href: logo.closest('a').getAttribute('href'), loaded: logo.complete && logo.naturalWidth > 0,
            rect: rect(logo) },
    items, toggles: document.querySelectorAll('[data-theme-toggle]').length,
    togglesInHeader: header.querySelectorAll('[data-theme-toggle]').length,
    menuToggle: getComputedStyle(header.querySelector('.s-header__menu-toggle')).display,
    footer: Boolean(footer), cols,
    year: footer ? footer.querySelector('[data-year]').textContent : null,
    legal: footer ? [...footer.querySelectorAll('.c-footer__legal a')]
      .map((a) => [a.textContent.trim(), a.getAttribute('href')]) : [],
    sw: document.documentElement.scrollWidth,
  };
}
"""

LOGOS_LOADED_JS = """
() => [...document.querySelectorAll('.c-footer img')].filter((img) => img.getClientRects().length)
  .every((img) => img.complete && img.naturalWidth > 0)
"""

MENU_JS = """
() => {
  const toggle = document.querySelector('.s-header__menu-toggle');
  const nav = document.getElementById(toggle.getAttribute('aria-controls'));
  const r = nav.getBoundingClientRect();
  const a = document.activeElement;
  return { expanded: toggle.getAttribute('aria-expanded'), visibility: getComputedStyle(nav).visibility,
           rect: [r.left, r.top, r.width, r.height], vw: innerWidth, vh: innerHeight,
           focusInNav: Boolean(a && nav.contains(a)), focusOnToggle: a === toggle,
           focus: a ? (a.getAttribute('aria-label') || a.textContent || '').trim() : null };
}
"""

# The stage frame fills the window: element full screen, or the fallback that fills the viewport.
FRAME_FILLS_JS = """
() => { const r = document.getElementById('stage-frame').getBoundingClientRect();
  return Math.abs(r.left) < 1 && Math.abs(r.top) < 1 && Math.abs(r.width - innerWidth) < 1
    && Math.abs(r.height - innerHeight) < 1; }
"""

MENU_CLOSED_JS = "() => document.querySelector('.s-header__menu-toggle').getAttribute('aria-expanded') === 'false'"

# The drawer has slid fully in (its 0.3 s transition).
MENU_OPEN_JS = """
() => {
  const toggle = document.querySelector('.s-header__menu-toggle');
  const nav = document.getElementById(toggle.getAttribute('aria-controls'));
  const r = nav.getBoundingClientRect();
  const a = document.activeElement;
  return toggle.getAttribute('aria-expanded') === 'true' && Math.abs(r.right - innerWidth) < 1
    ? { visibility: getComputedStyle(nav).visibility, rect: [r.left, r.top, r.width, r.height], vh: innerHeight,
        focusInNav: Boolean(a && nav.contains(a)) }
    : null;
}
"""

HIT_JS = """
(sel) => [...document.querySelectorAll(sel)].map((el) => {
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return { name: (el.getAttribute('aria-label') || el.textContent).trim(), rect: [r.left, r.top, r.width, r.height],
           hit: Boolean(hit && (hit === el || el.contains(hit))) };
})
"""


def open_page(tp: TalkPage, which: str, hash_: str = "") -> None:
    if which == "talk":
        tp.goto(hash_)
    else:
        tp.page.goto(f"{tp.base}/talks/")
        wait_until(tp.page, "() => document.querySelectorAll('#talk-list > li').length")
    # The header metrics depend on the web fonts.
    wait_until(tp.page, "() => document.fonts.status === 'loaded'")


def check_shell(tp: TalkPage, wide: bool, which: str) -> dict:
    s = tp.page.evaluate(SHELL_JS)
    assert s["position"] == "fixed" and s["header"][:2] == [0, 0] and s["header"][2] == s["vw"], s["header"]
    assert s["logo"]["alt"] == "CoMPhy Lab" and s["logo"]["href"] == "/" and s["logo"]["loaded"], s["logo"]
    got = [(i["kind"], i["name"], i["href"]) for i in s["items"]]
    assert got == NAV, got
    current = [i["name"] for i in s["items"] if i["current"]]
    assert current == ["Talks"] and s["items"][6]["current"] == CURRENT[which], (current, s["items"][6])
    assert all(i["svg"] for i in s["items"] if i["href"] is None or i["name"].endswith(("Profile", "Organization")))
    assert s["toggles"] == 1 and s["togglesInHeader"] == 1, "exactly one theme control, in the header"
    assert s["sw"] <= s["vw"], f"horizontal overflow {s['sw']} > {s['vw']}"
    if wide:
        assert s["menuToggle"] == "none"
        hx, hy, hw, hh = s["header"]
        for i in s["items"]:
            x, y, w, h = i["rect"]
            assert x >= 0 and x + w <= s["vw"] and y >= hy and y + h <= hy + hh, f"{i['name']} outside the bar: {i}"
            assert h == 40, f"{i['name']} wraps or is squeezed: {i['rect']}"
    else:
        assert s["menuToggle"] != "none"
    assert s["footer"]
    assert {c["title"]: c["links"] for c in s["cols"]} == {k: [list(v) for v in vals] for k, vals in FOOTER.items()}
    assert [c["label"] for c in s["cols"]] == ["Explore", "Work with us", "Code and data"]
    assert s["year"] == str(datetime.date.today().year)
    assert [t for t, _ in s["legal"]] == ["V. Sanjay", "Worthy", "Edit on GitHub", "Back to top ↑"], s["legal"]
    tp.page.locator(".c-footer").scroll_into_view_if_needed()
    wait_until(tp.page, LOGOS_LOADED_JS, timeout=5)
    tp.page.evaluate("() => window.scrollTo(0, 0)")
    return s


@pytest.mark.parametrize("which", ["index", "talk"])
def test_header_and_footer_desktop(open_talk, talk: Talk, engine: str, which: str):
    tp = open_talk(**DESKTOP)
    open_page(tp, which)
    check_shell(tp, wide=True, which=which)
    if which == "talk":
        # The fixed header never covers the stage, and stage plus controls fit the window.
        tp.wait_state(1, 0)
        layout = tp.page.evaluate(
            "() => { const r = (s) => document.querySelector(s).getBoundingClientRect();"
            " const f = r('#stage-frame'), c = r('.controls'), h = r('.s-header');"
            " const hit = document.elementFromPoint(f.left + f.width / 2, f.top + 2);"
            " return { frameTop: f.top, frameBottom: f.bottom, controlsBottom: c.bottom, headerBottom: h.bottom,"
            " vh: innerHeight, hitStage: Boolean(hit && hit.closest('#stage-frame')) }; }"
        )
        assert layout["frameTop"] >= layout["headerBottom"], layout
        assert layout["controlsBottom"] <= layout["vh"], layout
        assert layout["hitStage"], layout
    # Following a header link leaves for the main site's page (root-relative, same origin).
    tp.page.route(
        f"{tp.base}/team/",
        lambda route: route.fulfill(
            status=200, body="<!doctype html><title>Team</title>", headers={"Content-Type": "text/html"}
        ),
    )
    tp.assert_clean()
    tp.page.locator(".s-header__nav-list a[href='/team/']").click()
    tp.page.wait_for_url(f"{tp.base}/team/")


def test_theme_toggle_is_single_and_shared(open_talk, talk: Talk, engine: str):
    tp = open_talk(**DESKTOP)
    open_page(tp, "talk", talk.hash_for(3))
    tp.wait_state(3, 0)
    assert tp.page.evaluate("() => document.querySelectorAll('.controls [data-theme-toggle]').length") == 0
    theme = "() => [document.documentElement.dataset.theme, localStorage.getItem('theme')]"
    assert tp.page.evaluate(theme) == ["light", None]
    button = tp.page.locator(".s-header [data-theme-toggle]")
    button.click()
    assert tp.page.evaluate(theme) == ["dark", "dark"]
    assert button.get_attribute("aria-label") == "Switch to light theme"
    assert tp.state()["slide"] == 3, "the theme toggle changed slide"
    tp.page.reload()
    wait_until(tp.page, "() => window.__talk && window.__talk.state()")
    assert tp.page.evaluate(theme) == ["dark", "dark"]
    # The index shares the key (as comphy-lab.org does).
    open_page(tp, "index")
    assert tp.page.evaluate(theme) == ["dark", "dark"]
    assert tp.page.evaluate("() => document.querySelectorAll('[data-theme-toggle]').length") == 1
    tp.page.locator(".s-header [data-theme-toggle]").click()
    assert tp.page.evaluate(theme) == ["light", "light"]
    open_page(tp, "talk")
    assert tp.page.evaluate(theme) == ["light", "light"]
    tp.assert_clean()


def test_header_keeps_the_player_keys(open_talk, talk: Talk, engine: str):
    """With focus on any header control, the player's keys still work; the header handles none of them."""
    tp = open_talk(**DESKTOP)
    open_page(tp, "talk", talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    tp.wait_videos_playing(min_time=0.1)
    pos = talk.step(4, 1).index
    for selector in (
        ".s-header [data-theme-toggle]",
        ".s-header__nav-list a[href='/team/']",
        ".s-header__nav-list a[aria-label='Bluesky Profile']",
        ".s-header__logo a",
    ):
        tp.page.locator(selector).focus()
        for key, delta in (("ArrowRight", 1), ("PageDown", 1), ("ArrowLeft", -1), ("PageUp", -1)):
            tp.press(key)
            pos += delta
            s = talk.steps[pos]
            tp.wait_state(s.n, s.k, timeout=3)
        for key in ("p", "k"):
            was = tp.state()["paused"]
            tp.press(key)
            wait_until(tp.page, "(was) => window.__talk.state().paused !== was", was, timeout=3)
        assert tp.page.url.startswith(f"{tp.base}/talks/{talk.slug}/"), "a key followed a header link"
        assert tp.page.evaluate("() => document.documentElement.dataset.theme") == "light"
    # F from a header link: the stage fills the screen and the header is out of reach; F again comes back.
    tp.page.locator(".s-header__nav-list a[href='/team/']").focus()
    tp.press("f")
    wait_until(tp.page, FRAME_FILLS_JS, timeout=5)
    # WebKit reports fullscreenchange a little after the resize.
    wait_until(tp.page, "() => document.querySelector('.s-header').inert", timeout=3)
    assert tp.page.evaluate("() => Boolean(document.elementFromPoint(20, 20).closest('#stage-frame'))")
    tp.press("f")
    wait_until(tp.page, "() => !document.querySelector('.s-header').inert", timeout=5)
    wait_until(
        tp.page, "() => document.activeElement && document.activeElement.getAttribute('href') === '/team/'", timeout=3
    )
    tp.assert_clean()


def test_back_to_top_keeps_the_deep_link(open_talk, talk: Talk, engine: str):
    tp = open_talk(**DESKTOP)
    open_page(tp, "talk", talk.hash_for(5))
    tp.wait_state(5, 0)
    tp.page.locator(".c-footer__back").scroll_into_view_if_needed()
    assert tp.page.evaluate("() => scrollY") > 300
    tp.page.locator(".c-footer__back").click()
    wait_until(tp.page, "() => scrollY === 0", timeout=5)
    assert tp.page.evaluate("() => location.hash") == talk.hash_for(5)
    assert tp.page.evaluate("() => document.activeElement === document.querySelector('.s-header__logo a')")
    assert tp.state()["slide"] == 5
    tp.assert_clean()


@pytest.mark.parametrize("width, drawer", [(1120, False), (1119, True)])
def test_bar_fits_or_becomes_the_menu(open_talk, talk: Talk, engine: str, width: int, drawer: bool):
    """The links never wrap or overflow: at 1120 px the full bar fits; below, the Menu drawer takes over, as on comphy-lab.org."""
    tp = open_talk(viewport={"width": width, "height": 768})
    open_page(tp, "index")
    check_shell(tp, wide=not drawer, which="index")
    tp.assert_clean()


@pytest.mark.parametrize("which", ["index", "talk"])
def test_phone_menu(open_talk, talk: Talk, engine: str, which: str):
    tp = open_talk(**PHONE)
    tp.page.add_init_script(NO_ELEMENT_FULLSCREEN)
    open_page(tp, which, talk.hash_for(10))
    check_shell(tp, wide=False, which=which)
    if which == "talk":
        tp.wait_state(10, 0)
        frame_top = tp.page.evaluate("() => document.getElementById('stage-frame').getBoundingClientRect().top")
        assert frame_top >= 80, "the header covers the stage"

    m = tp.page.evaluate(MENU_JS)
    assert m["expanded"] == "false" and m["visibility"] == "hidden" and m["rect"][0] >= m["vw"], m
    toggle = tp.page.locator(".s-header__menu-toggle")
    toggle.tap()
    m = wait_until(tp.page, MENU_OPEN_JS, timeout=3)
    assert m["visibility"] == "visible" and m["focusInNav"], m
    assert m["rect"][3] == m["vh"], m

    # Every item is on screen and not covered (theme toggle included: the only theme control).
    for item in tp.page.evaluate(HIT_JS, ".s-header__nav-close-btn, .s-header__nav-list a, .s-header__nav-list button"):
        x, y, w, h = item["rect"]
        assert x >= 0 and y >= 0 and x + w <= 390 and y + h <= 844 and item["hit"], item

    # Tab and Shift+Tab stay inside the open drawer, through all of it.
    count = tp.page.evaluate("() => document.querySelectorAll('#site-nav a[href], #site-nav button').length")
    seen = set()
    for key in ["Tab"] * (count + 2) + ["Shift+Tab"] * 3:
        tp.press(key)
        m = tp.page.evaluate(MENU_JS)
        assert m["focusInNav"], f"{key} left the open menu: {m}"
        seen.add(m["focus"])
    assert len(seen) == count, seen

    # The theme toggle inside the drawer works and leaves it open.
    tp.page.locator("#site-nav [data-theme-toggle]").tap()
    assert tp.page.evaluate("() => localStorage.getItem('theme')") == "dark"
    assert tp.page.evaluate(MENU_JS)["expanded"] == "true"

    # Escape closes it and gives focus back to the Menu button.
    tp.press("Escape")
    m = tp.page.evaluate(MENU_JS)
    assert m["expanded"] == "false" and m["focusOnToggle"], m
    wait_until(
        tp.page, "() => getComputedStyle(document.getElementById('site-nav')).visibility === 'hidden'", timeout=3
    )
    if which == "talk":
        assert (tp.state()["slide"], tp.state()["stateIndex"]) == (10, 0), "Escape changed slide"
        # The player's keys work again at once.
        tp.press("ArrowRight")
        s = talk.steps[talk.step(10, 0).index + 1]
        tp.wait_state(s.n, s.k, timeout=3)
        tp.press("ArrowLeft")
        tp.wait_state(10, 0, timeout=3)

    # Close button.
    toggle.tap()
    wait_until(
        tp.page, "() => document.querySelector('.s-header__menu-toggle').getAttribute('aria-expanded') === 'true'"
    )
    tp.page.wait_for_timeout(400)
    tp.page.locator(".s-header__nav-close-btn").tap()
    assert tp.page.evaluate(MENU_JS)["expanded"] == "false"

    # A tap outside only closes it: on the talk page it does not reach the stage.
    toggle.tap()
    tp.page.wait_for_timeout(400)
    if which == "talk":
        box = tp.page.locator("#stage-frame").bounding_box()
        tp.page.touchscreen.tap(box["x"] + 30, box["y"] + box["height"] / 2)
    else:
        tp.page.touchscreen.tap(40, 400)
    assert tp.page.evaluate(MENU_JS)["expanded"] == "false"
    if which == "talk":
        tp.page.wait_for_timeout(400)
        assert (tp.state()["slide"], tp.state()["stateIndex"]) == (10, 0), "the tap that closed the menu changed slide"

        # While the drawer is open the player takes no key: the slide hidden behind it stays, media
        # keeps playing, F does not go full screen, Backspace does not leave the talk (WebKit's
        # History Back), and the drawer stays open with focus inside.
        toggle.tap()
        wait_until(tp.page, MENU_OPEN_JS, timeout=3)
        paused = tp.state()["paused"]
        page_url = tp.page.url.split("#")[0]
        for key in PLAYER_KEYS:
            tp.press(key)
            tp.page.wait_for_timeout(100)
            assert tp.page.url.split("#")[0] == page_url, f"{key} left the page for {tp.page.url}"
            s = tp.state()
            assert (s["slide"], s["stateIndex"], s["paused"]) == (10, 0, paused), f"{key} reached the player: {s}"
            m = tp.page.evaluate(MENU_JS)
            assert m["expanded"] == "true" and m["focusInNav"], f"{key} closed the menu or took focus out: {m}"
        assert not tp.page.evaluate("() => document.documentElement.classList.contains('is-pseudo-fullscreen')")

        # The header turning inert, as the player's full screen makes it, closes the drawer.
        tp.page.evaluate("() => { document.querySelector('.s-header').inert = true; }")
        wait_until(tp.page, MENU_CLOSED_JS)
        tp.page.evaluate("() => { document.querySelector('.s-header').inert = false; }")

        # Full screen (the phone fallback) hides the header.
        tp.press("f")
        wait_until(tp.page, "() => document.documentElement.classList.contains('is-pseudo-fullscreen')")
        m = tp.page.evaluate(MENU_JS)
        assert m["expanded"] == "false", m
        hidden = tp.page.evaluate(
            "() => { const h = document.querySelector('.s-header');"
            " return h.inert && getComputedStyle(h).display === 'none'"
            " && Boolean(document.elementFromPoint(195, 40).closest('#stage-frame')); }"
        )
        assert hidden, "the header shows over the full-screen stage"
        tp.press("Escape")
        wait_until(tp.page, "() => !document.documentElement.classList.contains('is-pseudo-fullscreen')")
        assert tp.page.evaluate("() => getComputedStyle(document.querySelector('.s-header')).display") == "flex"
    tp.assert_clean()


NAV_SCROLL_JS = """
() => { const n = document.getElementById('site-nav');
  return { top: n.scrollTop, room: n.scrollHeight - n.clientHeight, overflowY: getComputedStyle(n).overflowY }; }
"""
NAV_AT_END_JS = """
() => { const n = document.getElementById('site-nav'); return n.scrollTop >= n.scrollHeight - n.clientHeight - 1; }
"""


def test_phone_landscape_menu_scrolls(open_talk, talk: Talk, engine: str):
    """A phone on its side: the drawer is taller than the screen, so it scrolls, by keyboard too, to its last item."""
    tp = open_talk(**{**PHONE, "viewport": {"width": 844, "height": 390}})
    open_page(tp, "talk", talk.hash_for(10))
    tp.wait_state(10, 0)
    tp.page.locator(".s-header__menu-toggle").tap()
    m = wait_until(tp.page, MENU_OPEN_JS, timeout=3)
    assert m["focusInNav"] and m["rect"][3] == m["vh"], m
    nav = tp.page.evaluate(NAV_SCROLL_JS)
    assert nav["overflowY"] == "auto" and nav["top"] == 0 and nav["room"] > 0, f"no overflow to scroll: {nav}"
    last = ".s-header__nav-list > li:last-child a"
    item = tp.page.evaluate(HIT_JS, last)[0]
    assert item["rect"][1] >= m["vh"], f"the last item is already on screen: {item}"

    # End scrolls the drawer (its key, not the player's) until its last item is on screen and not covered.
    tp.press("End")
    wait_until(tp.page, NAV_AT_END_JS, timeout=3)
    item = tp.page.evaluate(HIT_JS, last)[0]
    x, y, w, h = item["rect"]
    assert x >= 0 and y >= 0 and x + w <= 844 and y + h <= 390 and item["hit"], item
    assert (tp.state()["slide"], tp.state()["stateIndex"]) == (10, 0), "End reached the player"

    # Closed again, the keys are the player's.
    tp.press("Escape")
    wait_until(tp.page, MENU_CLOSED_JS)
    tp.press("ArrowRight")
    s = talk.steps[talk.step(10, 0).index + 1]
    tp.wait_state(s.n, s.k, timeout=3)
    tp.assert_clean()


def test_phone_landscape_stage_fits(open_talk, talk: Talk, engine: str):
    """A phone on its side: a lower header, and the stage and its controls still fit the screen."""
    tp = open_talk(**{**PHONE, "viewport": {"width": 844, "height": 390}})
    open_page(tp, "talk", talk.hash_for(4, 1))
    tp.wait_state(4, 1)
    layout = tp.page.evaluate(
        "() => { const r = (s) => document.querySelector(s).getBoundingClientRect();"
        " return { header: r('.s-header').height, frameTop: r('#stage-frame').top, controls: r('.controls').bottom,"
        " vh: innerHeight, sw: document.documentElement.scrollWidth, vw: innerWidth }; }"
    )
    assert layout["header"] == 60, layout
    assert layout["frameTop"] >= layout["header"] and layout["controls"] <= layout["vh"], layout
    assert layout["sw"] <= layout["vw"], layout
    tp.assert_clean()
