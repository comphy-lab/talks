"""Slide links (SPEC 1.2): talk.yaml parsing, manifest schema and invariants, download links."""

from __future__ import annotations

import copy

import pytest
import yaml

from talks_build.manifest import validate as check_invariants
from talks_build.talkfile import LinkSpec, TalkError, load_talk

A = "11111111-1111-1111-1111-111111111111"
URL = "https://comphy-lab.org/agents-in-research/"


def _talk_yaml(tmp_path, links):
    data = {"slug": "t", "title": "t", "speaker": "s", "updated": "2026-11-19",
            "media_base": "https://media.comphy-lab.org/talks/t/", "links": links}
    p = tmp_path / "talk.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_talk_yaml_links_parse(tmp_path):
    talk = load_talk(_talk_yaml(tmp_path, [{"slide": A, "rect": [1470, 500, 270, 270], "url": URL, "label": " Setup guide "}]))
    assert talk.links == [LinkSpec(slide=A, rect=(1470, 500, 270, 270), url=URL, label="Setup guide")]


@pytest.mark.parametrize(
    "bad",
    [
        {"slide": "x", "rect": [0, 0, 10, 10], "url": URL, "label": "a"},              # not a slide UUID
        {"slide": A, "rect": [0, 0, 10], "url": URL, "label": "a"},                    # three numbers
        {"slide": A, "rect": [0, 0, 10.5, 10], "url": URL, "label": "a"},              # not whole pixels
        {"slide": A, "rect": [0, 0, 0, 10], "url": URL, "label": "a"},                 # empty
        {"slide": A, "rect": [1900, 0, 30, 10], "url": URL, "label": "a"},             # off the canvas
        {"slide": A, "rect": [0, 0, 10, 10], "url": "http://example.org/", "label": "a"},  # not https
        {"slide": A, "rect": [0, 0, 10, 10], "url": "javascript:alert(1)", "label": "a"},
        {"slide": A, "rect": [0, 0, 10, 10], "url": URL, "label": "  "},               # no label
        {"slide": A, "rect": [0, 0, 10, 10], "url": URL},                              # missing field
        {"slide": A, "rect": [0, 0, 10, 10], "url": URL, "label": "a", "extra": 1},    # unknown field
    ],
)
def test_talk_yaml_links_reject(tmp_path, bad):
    with pytest.raises(TalkError):
        load_talk(_talk_yaml(tmp_path, [bad]))


def _manifest(links):
    slide = {"id": A, "n": 1, "title": "a", "text": "",
             "states": [{"image": "0123456789abcdef.webp", "overlay": None, "transition": "none", "media": []}]}
    if links is not None:
        slide["links"] = links
    return {
        "schema": 2, "slug": "t", "title": "t", "subtitle": "", "speaker": "s", "updated": "2026-11-19",
        "canvas": {"width": 1920, "height": 1080}, "media_base": "https://media.comphy-lab.org/talks/t/",
        "videos": {}, "youtube": {}, "slides": [slide], "changelog": [],
    }


def test_manifest_accepts_links_and_their_absence():
    assert check_invariants(_manifest(None)) == []
    assert check_invariants(_manifest([{"rect": [1470, 500, 270, 270], "url": URL, "label": "Setup guide"}])) == []


def test_manifest_rejects_bad_links():
    good = {"rect": [1470, 500, 270, 270], "url": URL, "label": "Setup guide"}
    for change in ({"url": "http://x.org/"}, {"label": ""}, {"rect": [0, 0, 10]}, {"rect": [1900, 0, 30, 10]},
                   {"rect": [0, 0, 0, 10]}):
        bad = copy.deepcopy(good) | change
        assert check_invariants(_manifest([bad])), change
    assert check_invariants(_manifest([]))  # an empty list is not allowed: leave the key out


@pytest.mark.parametrize(
    "url",
    ["https://", "https:///path", "https://localhost/", "https://user@example.org/", "https://exa mple.org/",
     "https://[::1/", "https://example.org/a b", "https://example.org:99999/", "https://example.org:port/",
     "https://exa_mple.org/", "https://-bad.example.org/", "https://example.org/\u00fc", "https://b\u00fccher.example/"],
)
def test_malformed_https_urls_rejected(tmp_path, url):
    with pytest.raises(TalkError):
        load_talk(_talk_yaml(tmp_path, [{"slide": A, "rect": [0, 0, 10, 10], "url": url, "label": "a"}]))
    assert check_invariants(_manifest([{"rect": [0, 0, 10, 10], "url": url, "label": "a"}]))


def test_manifest_rejects_blank_label():
    assert any("label" in e for e in check_invariants(_manifest([{"rect": [0, 0, 10, 10], "url": URL, "label": "  "}])))


def test_label_with_control_character_rejected(tmp_path):
    bad = {"slide": A, "rect": [0, 0, 10, 10], "url": URL, "label": "Setup\x0bguide"}
    with pytest.raises(TalkError):
        load_talk(_talk_yaml(tmp_path, [bad]))
    assert check_invariants(_manifest([{"rect": [0, 0, 10, 10], "url": URL, "label": "Setup\x0bguide"}]))


@pytest.mark.parametrize("value", [0, "", False, {}, None])
def test_links_must_be_a_list_when_given(tmp_path, value):
    with pytest.raises(TalkError):
        load_talk(_talk_yaml(tmp_path, value))


def test_omitted_links_mean_none(tmp_path):
    p = _talk_yaml(tmp_path, [])
    text = p.read_text().replace("links: []\n", "")
    p.write_text(text)
    assert load_talk(p).links == []


@pytest.mark.parametrize("url", ["https://example.org/", "https://sub.example.co.uk/a/b?x=1#frag",
                                 "https://xn--bcher-kva.example/", "https://example.org:8443/%C3%BC"])
def test_valid_https_urls_accepted(tmp_path, url):
    load_talk(_talk_yaml(tmp_path, [{"slide": A, "rect": [0, 0, 10, 10], "url": url, "label": "a"}]))
    assert check_invariants(_manifest([{"rect": [0, 0, 10, 10], "url": url, "label": "a"}])) == []


@pytest.mark.parametrize("label", ["Setup\ufffeguide", "Setup\uffffguide"])
def test_label_with_xml_forbidden_character_rejected(tmp_path, label):
    with pytest.raises(TalkError):
        load_talk(_talk_yaml(tmp_path, [{"slide": A, "rect": [0, 0, 10, 10], "url": URL, "label": label}]))
