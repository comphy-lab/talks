"""(i) The talks index lists the talk; the talk page also works with production media URLs."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Route

from talk_helpers import Talk, wait_until

MEDIA_HOST = "https://media.comphy-lab.org"


def test_index_lists_the_talk(open_talk, talk: Talk, engine: str):
    tp = open_talk()
    tp.page.goto(f"{tp.base}/talks/")
    cards = wait_until(
        tp.page,
        "() => { const items = [...document.querySelectorAll('#talk-list > li')];"
        " return items.length && items.map((li) => { const a = li.querySelector('a');"
        " return { title: a.textContent, href: a.href, text: li.textContent }; }); }",
    )
    m = talk.manifest
    card = next((c for c in cards if c["title"] == m["title"]), None)
    assert card, cards
    assert card["href"] == f"{tp.base}/talks/{talk.slug}/"
    assert m["subtitle"] in card["text"] and m["speaker"] in card["text"]
    assert re.search(r"Updated \d{1,2} \w+ \d{4}", card["text"]), card["text"]
    assert tp.page.evaluate("() => document.getElementById('talk-list-status').textContent") == ""
    assert tp.page.title() == "Talks · CoMPhy Lab"
    tp.assert_clean()

    # Follow the card: the page now uses the production media_base, answered locally.
    tp2 = open_talk(media="cdn")
    tp2.page.goto(f"{tp.base}/talks/")
    wait_until(tp2.page, "() => document.querySelector('#talk-list a')")
    tp2.page.click(f"#talk-list a[href='./{talk.slug}/']")
    tp2.page.wait_for_url(f"{tp.base}/talks/{talk.slug}/**")
    wait_until(tp2.page, "() => window.__talk && window.__talk.state()")
    tp2.wait_state(1, 0)
    src = tp2.stills()["image"]["src"]
    assert src == f"{m['media_base']}{talk.steps[0].state['image']}", src
    assert m["media_base"] == f"{MEDIA_HOST}/talks/{talk.slug}/"
    tp2.assert_clean()


def test_media_override_ignored_off_localhost(open_talk, talk: Talk, pytestconfig, engine: str):
    """`?media=` is a localhost-only test hook: on any other host media_base stands."""
    site: Path = pytestconfig.getoption("--site").resolve()
    tp = open_talk(media="cdn")

    def serve_site(route: Route) -> None:
        path = route.request.url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0].split("#", 1)[0]
        assert path.startswith("talks/")
        local = site / path[len("talks/") :]
        if local.is_dir():
            local = local / "index.html"
        if not local.resolve().is_relative_to(site) or not local.is_file():
            route.fulfill(status=404, body="not found")
            return
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
                 ".woff2": "font/woff2"}  # fmt: skip
        route.fulfill(
            status=200,
            body=local.read_bytes(),
            headers={"Content-Type": types.get(local.suffix, "application/octet-stream")},
        )

    tp.page.context.route("http://talks.test/**", serve_site)
    tp.page.goto(f"http://talks.test/talks/{talk.slug}/?media=/media/{talk.slug}/{talk.hash_for(4, 1)}")
    wait_until(tp.page, "() => window.__talk && window.__talk.state()")
    tp.wait_state(4, 1)
    src = tp.stills()["image"]["src"]
    assert src.startswith(f"{MEDIA_HOST}/talks/{talk.slug}/"), src
    vids = tp.wait_videos_playing(min_time=0.1)
    assert vids and all(v["src"].startswith(f"{MEDIA_HOST}/talks/{talk.slug}/") for v in vids), vids
    tp.assert_clean()
