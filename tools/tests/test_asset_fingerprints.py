"""Content-addressed runtime URLs and the no-export freshness gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from talks_build.cli import main
from talks_build.site import fingerprint_assets, refresh_assets, write_page


def _site(tmp_path):
    site = tmp_path / "site"
    (site / "player").mkdir(parents=True)
    (site / "player/app.js").write_text("window.version = 1;\n")
    (site / "player/app.css").write_text("body { color: black; }\n")
    return site


def test_fingerprints_follow_bytes_and_preserve_escaped_queries(tmp_path):
    site = _site(tmp_path)
    page = site / "index.html"
    original = (
        '<script src="player/app.js?lang=en&amp;empty=#start" defer></script>'
        '<link rel="stylesheet" href=\'player/app.css\'>'
    )
    one = fingerprint_assets(original, page, site)
    old_js = hashlib.sha256((site / "player/app.js").read_bytes()).hexdigest()[:16]
    css = hashlib.sha256((site / "player/app.css").read_bytes()).hexdigest()[:16]
    assert f'lang=en&amp;empty=&amp;v={old_js}#start' in one
    assert f"href='player/app.css?v={css}'" in one
    assert fingerprint_assets(one, page, site) == one
    (site / "player/app.js").write_text("window.version = 2;\n")
    two = fingerprint_assets(one, page, site)
    assert old_js not in two and f"app.css?v={css}" in two


def test_external_and_nonruntime_urls_are_untouched(tmp_path):
    site = _site(tmp_path)
    markup = (
        '<script src="https://example.org/app.js"></script><script src="//example.org/app.js"></script>'
        '<script src="data:text/javascript,app.js"></script><script src="javascript:app.js"></script>'
        '<link rel="icon" href="logo.png"><a href="player/app.js">source</a>'
    )
    assert fingerprint_assets(markup, site / "index.html", site) == markup


def test_query_encodings_bare_flags_duplicates_and_fragments_stay_intact(tmp_path):
    site = _site(tmp_path)
    markup = '<script src="player/app.js?flag&amp;raw=%ff&amp;x=1&amp;x=2&amp;%76=old#part"></script>'
    output = fingerprint_assets(markup, site / "index.html", site)
    digest = hashlib.sha256((site / "player/app.js").read_bytes()).hexdigest()[:16]
    assert f'app.js?flag&amp;raw=%ff&amp;x=1&amp;x=2&amp;v={digest}#part' in output
    assert fingerprint_assets(output, site / "index.html", site) == output


def test_only_real_asset_attributes_change_and_markup_is_preserved(tmp_path):
    site = _site(tmp_path)
    comment = '<!-- <script src="player/missing.js"></script> -->'
    inline = '<script>const example = \'<link href="player/missing.css">\';</script>'
    markup = (
        comment + "\n" + inline + "\n"
        '<script data-note=\'src="player/missing.js" >\' src="player/app.js" defer nonce="keep&amp;me"></script>'
        '<link rel="stylesheet" href=player/app.css data-note="&quot;quote&quot; >">'
    )
    output = fingerprint_assets(markup, site / "index.html", site)
    assert comment in output and inline in output
    assert 'data-note=\'src="player/missing.js" >\'' in output
    assert 'defer nonce="keep&amp;me"' in output
    assert 'data-note="&quot;quote&quot; >"' in output
    assert '<link rel="stylesheet" href="player/app.css?v=' in output
    assert fingerprint_assets(output, site / "index.html", site) == output


@pytest.mark.parametrize("url", ["../outside.js", "%2e%2e/outside.js", "player/missing.js", "player/escape.js"])
def test_missing_paths_traversal_and_symlink_escape_fail_closed(tmp_path, url):
    site = _site(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_text("private")
    (site / "player/escape.js").symlink_to(outside)
    with pytest.raises(ValueError):
        fingerprint_assets(f'<script src="{url}"></script>', site / "index.html", site)


def test_refresh_gate_is_read_only_and_needs_no_export(tmp_path, capsys):
    site = _site(tmp_path)
    (site / "index.html").write_text('<script src="player/app.js"></script>')
    (site / "player/talk.template.html").write_text(
        '<title>{{TITLE}}</title><meta content="{{DESCRIPTION}}">{{SLUG}}<script src="../player/app.js"></script>'
    )
    talk = SimpleNamespace(slug="demo", title="Demo", subtitle="", speaker="Test", venue_note="")
    page = write_page(site, talk)
    assert "?v=" in page.read_text()
    before = (site / "index.html").read_bytes()
    assert main(["refresh-assets", "--site", str(site), "--check"]) == 1
    assert (site / "index.html").read_bytes() == before
    assert "index.html" in capsys.readouterr().err
    assert main(["refresh-assets", "--site", str(site)]) == 0
    assert main(["refresh-assets", "--site", str(site), "--check"]) == 0
    (site / "player/app.js").write_text("window.version = 3;\n")
    stale = refresh_assets(site, check=True)
    assert set(stale) == {site / "index.html", site / "player/talk.template.html", page}
    assert main(["refresh-assets", "--site", str(site)]) == 0
    assert not refresh_assets(site, check=True)


def test_html_symlink_escape_fails_before_any_refresh_writes(tmp_path):
    site = _site(tmp_path)
    page = site / "index.html"
    page.write_text('<script src="player/app.js"></script>')
    outside = tmp_path / "outside.html"
    outside.write_text("Private outside page")
    (site / "z.html").symlink_to(outside)
    original = page.read_bytes()
    with pytest.raises(ValueError, match="HTML symlink escapes"):
        refresh_assets(site)
    assert page.read_bytes() == original
    assert outside.read_text() == "Private outside page"


def test_root_relative_owned_asset_is_fingerprinted(tmp_path):
    site = _site(tmp_path)
    result = fingerprint_assets('<script src="/talks/player/app.js"></script>', site / "index.html", site)
    assert 'src="/talks/player/app.js?v=' in result


def test_encoded_owned_extension_is_fingerprinted(tmp_path):
    site = _site(tmp_path)
    result = fingerprint_assets('<script src="player/app%2ejs"></script>', site / "index.html", site)
    assert 'src="player/app%2ejs?v=' in result
