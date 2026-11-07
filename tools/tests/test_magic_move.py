"""Magic Move: talk.yaml parsing, marking the entering state, manifest invariants."""

from __future__ import annotations

import copy

import pytest
import yaml

from talks_build.magicmove import MagicMoveError, apply_magic_moves, moved_videos
from talks_build.manifest import validate as check_invariants
from talks_build.talkfile import TalkError, load_talk

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"


def _state(media, transition="none"):
    return {"image": "0123456789abcdef.webp", "overlay": None, "transition": transition, "media": media}


def _video(ref, rect):
    return {"kind": "video", "ref": ref, "rect": rect}


def _glide(st):
    st.update(glide_image="fedcba9876543210.webp", glide_image2x="fedcba9876543211.webp")
    return st


def _slides(after_rect=(125, 210, 440, 421)):
    return [
        {"id": A, "n": 1, "title": "a", "text": "", "states": [_glide(_state([_video("v01", [550, 160, 820, 785])]))]},
        {"id": B, "n": 2, "title": "b", "text": "", "states": [_glide(_state([_video("v01", list(after_rect))]))]},
    ]


def test_marks_entering_state():
    slides = _slides()
    report = apply_magic_moves(slides, {A: 1.2})
    st = slides[1]["states"][0]
    assert st["transition"] == "magic-move" and st["transition_duration"] == 1.2
    assert report == [{"from_slide": 1, "to_slide": 2, "videos": ["v01"], "duration": 1.2}]


def test_refuses_when_nothing_moves():
    with pytest.raises(MagicMoveError):
        apply_magic_moves(_slides(after_rect=(550, 160, 820, 785)), {A: 1.2})


def test_refuses_unknown_or_last_slide():
    with pytest.raises(MagicMoveError):
        apply_magic_moves(_slides(), {"33333333-3333-3333-3333-333333333333": 1.0})
    with pytest.raises(MagicMoveError):
        apply_magic_moves(_slides(), {B: 1.0})


def test_moved_videos_needs_same_ref():
    assert moved_videos(_state([_video("v01", [0, 0, 10, 10])]), _state([_video("v02", [5, 5, 10, 10])])) == []


def _manifest(slides):
    return {
        "schema": 2, "slug": "t", "title": "t", "subtitle": "", "speaker": "s", "updated": "2026-11-07",
        "canvas": {"width": 1920, "height": 1080},
        "media_base": "https://media.comphy-lab.org/talks/t/",
        "videos": {"v01": {"src": "0123456789abcdef.mp4", "poster": "0123456789abcdef.webp",
                           "width": 10, "height": 10, "duration": 1.0, "credit": None}},
        "youtube": {}, "slides": slides, "changelog": [],
    }


def test_invariants_accept_and_reject():
    slides = _slides()
    apply_magic_moves(slides, {A: 1.2})
    assert check_invariants(_manifest(slides)) == []
    bad = copy.deepcopy(slides)
    del bad[1]["states"][0]["transition_duration"]
    assert any("transition_duration" in e for e in check_invariants(_manifest(bad)))
    stray = copy.deepcopy(_slides())
    stray[1]["states"][0]["transition_duration"] = 1.0
    assert any("only for magic-move" in e for e in check_invariants(_manifest(stray)))


def _talk_yaml(tmp_path, extra):
    data = {"slug": "t", "title": "t", "speaker": "s", "updated": "2026-11-07",
            "media_base": "https://media.comphy-lab.org/talks/t/", **extra}
    p = tmp_path / "talk.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_talk_yaml_magic_move(tmp_path):
    talk = load_talk(_talk_yaml(tmp_path, {"magic_move": [{"slide": A, "duration": 1.2}]}))
    assert talk.magic_move == {A: 1.2}
    for bad in ([{"slide": "x", "duration": 1}], [{"slide": A, "duration": 0}], [{"slide": A, "duration": 9}],
                [{"slide": A, "duration": 1}, {"slide": A, "duration": 1}]):
        with pytest.raises(TalkError):
            load_talk(_talk_yaml(tmp_path, {"magic_move": bad}))


def test_repeated_ref_unchanged_is_not_a_move():
    a, b = [0, 0, 100, 100], [500, 0, 100, 100]
    same = _state([_video("v01", a), _video("v01", b)])
    assert moved_videos(same, _state([_video("v01", a), _video("v01", b)])) == []
    assert moved_videos(same, _state([_video("v01", a), _video("v01", [900, 0, 100, 100])])) == ["v01"]


def test_translation_only_and_non_first_media_move():
    before = _state([_video("v09", [0, 0, 10, 10]), _video("v01", [550, 160, 820, 785])])
    after = _state([_video("v09", [0, 0, 10, 10]), _video("v01", [125, 210, 820, 785])])
    assert moved_videos(before, after) == ["v01"]


def test_half_pixel_rounding_matches_the_player():
    # Math.round / floor(v + 0.5): 0.5 and 1 share a key, 0.5 and 0 do not.
    before = _state([_video("v01", [0.5, 0, 100, 100])])
    assert moved_videos(before, _state([_video("v01", [1, 0, 100, 100])])) == []
    assert moved_videos(before, _state([_video("v01", [0, 0, 100, 100])])) == ["v01"]


def test_refuses_without_glide_stills():
    slides = _slides()
    for sl in slides:
        for st in sl["states"]:
            st.pop("glide_image"); st.pop("glide_image2x")
    with pytest.raises(MagicMoveError):
        apply_magic_moves(slides, {A: 1.2})
