"""A returning browser loads changed runtime bytes despite an immutable cache."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

import serve

REPO = Path(__file__).resolve().parent.parent
ASSET_URLS = "[document.querySelector('script').src, document.querySelector('link[rel=stylesheet]').href]"


class ImmutableAssetsHandler(serve.TalksHandler):
    """Keep the real HTTP cache enabled; only the HTML must be fetched again."""

    def end_headers(self) -> None:
        runtime = Path(urlsplit(self.path).path).suffix in (".js", ".css")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if runtime else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        SimpleHTTPRequestHandler.end_headers(self)


def test_normal_reload_loads_changed_fingerprints_from_a_returning_cache(browser, tmp_path, monkeypatch):
    # The routed open_talk fixture disables the HTTP cache. Use an un-routed
    # context and a real server, so this reproduces the returning-user problem.
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    from talks_build.site import refresh_assets

    site = tmp_path / "site"
    (site / "player").mkdir(parents=True)
    script = site / "player/app.js"
    style = site / "player/app.css"
    script.write_text("window.runtimeVersion = 1;\n", encoding="utf-8")
    style.write_text("body { color: rgb(0, 0, 0); }\n", encoding="utf-8")
    (site / "index.html").write_text(
        '<!doctype html><title>Returning cache</title><link rel="icon" href="data:,">'
        '<link rel="stylesheet" href="player/app.css"><script src="player/app.js" defer></script>'
        '<body>Runtime cache</body>',
        encoding="utf-8",
    )
    assert refresh_assets(site) == [site / "index.html"]
    requests = []
    handler = type(
        "BoundImmutableAssetsHandler",
        (ImmutableAssetsHandler,),
        {"site_dir": site, "media_dirs": {}, "request_log": requests},
    )
    server = serve.Server(("127.0.0.1", 0), partial(handler, directory=str(site)))
    thread = serve.start_in_thread(server)
    context = browser.new_context()
    try:
        page = context.new_page()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        page.goto(f"{base}/talks/")
        assert page.evaluate("window.runtimeVersion") == 1
        assert page.evaluate("getComputedStyle(document.body).color") == "rgb(0, 0, 0)"
        old = page.evaluate(ASSET_URLS)
        for url in old:
            assert requests.count(urlsplit(url).path + "?" + urlsplit(url).query) == 1

        script.write_text("window.runtimeVersion = 2;\n", encoding="utf-8")
        style.write_text("body { color: rgb(0, 0, 255); }\n", encoding="utf-8")
        # The old immutable URL still returns the old bytes from this browser's
        # cache even though the server's file has changed. No interception.
        request_count = len(requests)
        cached = page.evaluate(
            """async urls => {
              const script = document.createElement('script');
              script.src = urls[0];
              await new Promise(resolve => { script.onload = resolve; document.head.append(script); });
              return window.runtimeVersion;
            }""", old
        )
        assert cached == 1
        assert len(requests) == request_count

        assert refresh_assets(site) == [site / "index.html"]
        page.reload()  # normal reload in the same browser context, no cache bypass
        assert page.evaluate("window.runtimeVersion") == 2
        assert page.evaluate("getComputedStyle(document.body).color") == "rgb(0, 0, 255)"
        new = page.evaluate(ASSET_URLS)
        assert all(before != after for before, after in zip(old, new, strict=True))
        for url in new:
            assert requests.count(urlsplit(url).path + "?" + urlsplit(url).query) == 1
        assert refresh_assets(site, check=True) == []
    finally:
        context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
