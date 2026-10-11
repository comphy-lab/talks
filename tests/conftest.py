"""Fixtures for the talks browser tests.

Every test that takes the ``engine`` fixture runs once per browser engine
(Chromium and WebKit by default; choose with ``--engine``). Pages are served
by :mod:`serve` with the production layout, and every request that leaves the
local server is intercepted:

- ``www.youtube.com/iframe_api`` and ``www.youtube-nocookie.com/embed/...``
  get local stand-ins (``stubs/``), unless ``--live-youtube`` is given for a
  test marked ``live``;
- ``media.comphy-lab.org`` is answered from the local build only for tests
  that ask for it (``media="cdn"``); otherwise it is recorded as unexpected;
- anything else is aborted and recorded as unexpected.
"""

from __future__ import annotations

import base64
import re
import urllib.parse
from pathlib import Path

import pytest
from playwright.sync_api import Route, sync_playwright

import serve
from talk_helpers import INIT_JS, Talk, TalkPage

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STUBS = HERE / "stubs"
ENGINES = ("chromium", "webkit")
MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("talks")
    group.addoption("--engine", action="append", choices=ENGINES, help="browser engine (repeatable; default: both)")
    group.addoption("--slug", default="singular-bursting-bubbles", help="talk to test")
    group.addoption("--site", type=Path, default=REPO / "site", help="site directory (default: <repo>/site)")
    group.addoption("--build", type=Path, default=REPO / "build", help="build root (default: <repo>/build)")
    group.addoption("--live-youtube", action="store_true", help="also run tests marked live (needs network)")
    group.addoption("--headed", action="store_true", help="show the browser windows")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live-youtube"):
        return
    skip = pytest.mark.skip(reason="needs --live-youtube (network access to YouTube)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "engine" in metafunc.fixturenames:
        engines = metafunc.config.getoption("--engine") or list(ENGINES)
        metafunc.parametrize("engine", engines, scope="session")


# --------------------------------------------------------------- fixtures


@pytest.fixture(scope="session")
def talk(pytestconfig: pytest.Config) -> Talk:
    slug = pytestconfig.getoption("--slug")
    site = pytestconfig.getoption("--site").resolve()
    media = (pytestconfig.getoption("--build") / slug / "media").resolve()
    manifest = site / slug / "manifest.json"
    if not manifest.is_file() or not media.is_dir():
        pytest.exit(
            f"no build for {slug}: run the pipeline first (see tests/README.md); looked for "
            f"site/{slug}/manifest.json and build/{slug}/media/",
            returncode=4,
        )
    return Talk.load(site, media, slug)


@pytest.fixture(scope="session")
def server(pytestconfig: pytest.Config, talk: Talk):
    site = pytestconfig.getoption("--site").resolve()
    srv = serve.make_server(site, {talk.slug: talk.media_dir})
    serve.start_in_thread(srv)
    host, port = srv.server_address[:2]
    yield f"http://{host}:{port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance, engine: str, pytestconfig: pytest.Config):
    launcher = getattr(playwright_instance, engine)
    b = launcher.launch(headless=not pytestconfig.getoption("--headed"))
    yield b
    b.close()


def _fulfil_file(route: Route, path: Path, content_type: str, extra: dict | None = None) -> None:
    """Answer a routed request from a local file, honouring a single byte range."""
    data = path.read_bytes()
    headers = {"Content-Type": content_type, "Accept-Ranges": "bytes", **(extra or {})}
    rng = route.request.headers.get("range")
    m = RANGE_RE.match(rng.strip()) if rng else None
    if m and (m.group(1) or m.group(2)):
        size = len(data)
        if m.group(1) == "":
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        route.fulfill(status=206, body=data[start : end + 1], headers=headers)
    else:
        route.fulfill(status=200, body=data, headers=headers)


@pytest.fixture
def open_talk(browser, server: str, talk: Talk, engine: str):
    """Factory: ``open_talk(youtube="stub"|"abort"|"partial"|"live", media="local"|"cdn", **context_options)``.

    ``partial`` serves the IFrame API stand-in but blocks the embed host.

    Returns a :class:`TalkPage` whose page has not navigated yet; call
    ``tp.goto(hash)``. Contexts are closed after the test.
    """
    contexts = []

    def factory(youtube: str = "stub", media: str = "local", **context_options) -> TalkPage:
        options = {"viewport": {"width": 1600, "height": 1000}, **context_options}
        ctx = browser.new_context(**options)
        contexts.append(ctx)
        ctx.add_init_script(INIT_JS)
        page = ctx.new_page()
        tp = TalkPage(page=page, base=server, talk=talk, engine=engine)
        ctx.expose_binding("__reportCsp", lambda source, v: tp.csp_events.append(v))

        def handle(route: Route) -> None:
            url = route.request.url
            parts = urllib.parse.urlsplit(url)
            host, path = parts.hostname or "", parts.path
            if host == "i.ytimg.com" and re.fullmatch(r"/vi/[A-Za-z0-9_-]{11}/hqdefault\.jpg", path):
                # Deterministic thumbnail for the link-out UI; no external media.
                thumbnail = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1sAAAAASUVORK5CYII="
                )
                route.fulfill(content_type="image/png", body=thumbnail)
                return
            if host == "media.comphy-lab.org":
                name = path.rsplit("/", 1)[-1]
                expected = f"/talks/{talk.slug}/{name}"
                if (
                    media == "cdn"
                    and path == expected
                    and MEDIA_NAME_RE.match(name)
                    and talk.media_path(name).is_file()
                ):
                    ctype = "video/mp4" if name.endswith(".mp4") else "image/webp"
                    _fulfil_file(route, talk.media_path(name), ctype)
                    return
                tp.external.append(url)
                route.abort()
                return
            if youtube in ("stub", "partial") and host == "www.youtube.com" and path == "/iframe_api":
                _fulfil_file(route, STUBS / "youtube_iframe_api.js", "text/javascript; charset=utf-8")
                return
            if youtube == "stub" and host == "www.youtube-nocookie.com" and path.startswith("/embed/"):
                _fulfil_file(route, STUBS / "youtube_embed.html", "text/html; charset=utf-8")
                return
            if youtube == "abort" and host in ("www.youtube.com", "i.ytimg.com", "www.youtube-nocookie.com"):
                route.abort()
                return
            if youtube == "partial" and host in ("i.ytimg.com", "www.youtube-nocookie.com"):
                # The API script loads but the embed host is blocked (a privacy filter).
                route.abort()
                return
            tp.external.append(url)
            route.abort()

        if youtube == "live":
            # Let YouTube's own frame fetch what it needs; still police our media host.
            ctx.route(lambda url: urllib.parse.urlsplit(url).hostname == "media.comphy-lab.org", handle)
        else:
            ctx.route(lambda url: not url.startswith(server + "/") and not url.startswith("data:"), handle)

        page.on("console", lambda m: tp.console.append((m.type, m.text, (m.location or {}).get("url", ""))))
        page.on("pageerror", lambda e: tp.page_errors.append(str(e)))
        page.on(
            "response",
            lambda r: (
                tp.bad_responses.append(f"{r.status} {r.url}") if r.status >= 400 and r.url.startswith(server) else None
            ),
        )
        return tp

    yield factory
    for ctx in contexts:
        ctx.close()
