"""Manifest schema and section 3.3 invariants."""

import copy
import json

import pytest

from talks_build.cli import main
from talks_build.manifest import placement_key, validate

H = "0123456789abcdef"


def good():
    return {
        "schema": 1,
        "slug": "demo-talk",
        "title": "Demo",
        "subtitle": "",
        "speaker": "Someone",
        "updated": "2026-10-10",
        "canvas": {"width": 1920, "height": 1080},
        "media_base": "https://media.comphy-lab.org/talks/demo-talk/",
        "videos": {
            "v01": {
                "src": f"{H}.mp4",
                "poster": f"{H}.webp",
                "width": 640,
                "height": 360,
                "duration": 2.5,
                "credit": None,
            }
        },
        "youtube": {"y01": {"id": "a9hUsVq9q7U", "start": 7.191, "end": 63.397, "title": "t", "credit": "c"}},
        "slides": [
            {
                "id": "6BDE34AF-C6F3-4830-829F-9C89C9310573",
                "n": 1,
                "title": "One",
                "text": "One",
                "states": [
                    {
                        "image": f"{H}.webp",
                        "overlay": None,
                        "transition": "none",
                        "media": [
                            {"kind": "video", "ref": "v01", "rect": [10, 20, 300, 200]},
                            {"kind": "youtube", "ref": "y01", "rect": [400, 20, 640, 360]},
                        ],
                    }
                ],
            }
        ],
        "changelog": [{"date": "2026-10-10", "note": "First."}],
    }


def test_good_manifest_is_valid():
    assert validate(good()) == []


def mutate(fn):
    m = copy.deepcopy(good())
    fn(m)
    return validate(m)


@pytest.mark.parametrize(
    "fn,needle",
    [
        (lambda m: m.update(canvas={"width": 1280, "height": 720}), "canvas"),
        (lambda m: m["slides"][0]["states"][0]["media"][0].update(ref="v02"), "does not resolve"),
        (lambda m: m["slides"][0]["states"][0]["media"][1].update(ref="y09"), "does not resolve"),
        (lambda m: m["slides"][0].update(n=2), "expected 1"),
        (lambda m: m["slides"][0].update(states=[]), "schema"),
        (lambda m: m["slides"][0]["states"][0].update(image="slide1.webp"), "schema"),
        (lambda m: m["videos"]["v01"].update(src="0123456789ABCDEF.mp4"), "schema"),
        (lambda m: m.update(media_base="https://media.comphy-lab.org/talks/other/"), "media_base"),
        (
            lambda m: m["slides"][0]["states"][0]["media"].append(
                {"kind": "video", "ref": "v01", "rect": [10.2, 19.6, 300.4, 199.5]}
            ),
            "duplicate placement key",
        ),  # fmt: skip
        (lambda m: m["slides"][0]["states"][0].update(transition="wipe"), "schema"),
        (lambda m: m["videos"]["v01"].update(width=641), "schema"),
        (lambda m: m.update(extra=1), "schema"),
        (lambda m: m["youtube"]["y01"].update(end=1.0), "end must be after start"),
    ],
)
def test_invariant_violations(fn, needle):
    errors = mutate(fn)
    assert errors and any(needle in e for e in errors), errors


def test_placement_key_rounds_to_pixels():
    a = {"kind": "video", "ref": "v01", "rect": [1347.6, 0.4, 567, 1080]}
    b = {"kind": "video", "ref": "v01", "rect": [1348, 0, 567.2, 1079.9]}
    assert placement_key(a) == placement_key(b) == "video:v01:1348,0,567,1080"


def test_validate_cli(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(good()))
    assert main(["validate", str(path)]) == 0
    bad = good()
    bad["slides"][0]["states"][0]["media"][0]["ref"] = "v07"
    path.write_text(json.dumps(bad))
    assert main(["validate", str(path)]) == 1
    assert "does not resolve" in capsys.readouterr().err


def test_validate_checks_media_dir(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    errors = validate(good(), media)
    assert any("missing" in e for e in errors)
    for name in (f"{H}.mp4", f"{H}.webp"):
        (media / name).write_bytes(b"x")
    assert validate(good(), media) == []
