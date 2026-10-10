"""State model: click states, builds, movies, spans."""

import json
from pathlib import Path

import pytest
import synthetic_export as sx

from talks_build.keynote import (
    Event,
    apply_builds,
    click_event_indices,
    leaves_of,
    load_deck,
    parse_layer,
)


def _event(i, auto, flattened=False):
    return Event(index=i, auto=auto, effects=[], base=parse_layer(sx._root([])), accessibility=[], flattened=flattened)


@pytest.mark.parametrize(
    "flags,expected",
    [
        ("C", [0]),
        ("CC", [0, 1]),
        ("ACC", [1, 2]),  # an automatic first build folds into the first state
        ("CAC", [0, 2]),  # an automatic build after a click folds into that click
        ("CCCCC", [0, 1, 2, 3, 4]),
        ("AA", [1]),  # no click at all: one state, the final one
    ],
)
def test_click_states(flags, expected):
    events = [_event(i, f == "A") for i, f in enumerate(flags)]
    assert click_event_indices(events) == expected


def _write_deck(root: Path, slides: dict[str, tuple[list, list]]) -> Path:
    """JSON-only export (load_deck does not open the PDFs)."""
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "header.json").write_text(
        json.dumps({"slideList": list(slides), "slideWidth": 1920, "slideHeight": 1080})
    )
    for uuid, (pages, events) in slides.items():
        sx._slide(assets, uuid, pages, events, videos={sx.CLIP: (320, 180)})
    return root


def _deck_with_builds(tmp_path):
    u = sx.SLIDES["S2"]
    t = [sx._tex(u, i) for i in range(5)]
    pages = [(1920, 1080, ""), (320, 180, ""), (300, 60, ""), (400, 40, ""), (1920, 1080, "")]
    base0 = sx._root(
        [
            sx._object(t[0], (0, 0, 1920, 1080)),
            sx._object(t[1], sx.MOVIE_RECT, sx.MOVIE_OBJ),
            sx._object(t[2], (700, 600, 300, 60), sx.LABEL_OBJ),
            sx._object(t[3], (200, 700, 400, 40), sx.CITE_OBJ, hidden=True),
        ]
    )
    events = [
        {
            "automaticPlay": True,
            "baseLayer": base0,
            "effects": [sx._movie_effect(sx.MOVIE_OBJ, sx.CLIP, sx.MOVIE_RECT)],
        },
        {
            "automaticPlay": False,
            "baseLayer": base0,
            "effects": [
                {"type": "buildIn", "name": "apple:dissolve", "objectID": sx.CITE_OBJ, "duration": 0.5},
                {"type": "buildOut", "name": "apple:appear", "objectID": sx.LABEL_OBJ, "duration": 0.001},
            ],
        },
        sx._transition(t[4]),
    ]
    u3 = sx.SLIDES["S3"]
    t3 = [sx._tex(u3, i) for i in range(3)]
    base3 = sx._root([sx._object(t3[0], (0, 0, 1920, 1080)), sx._object(t3[1], sx.MOVIE_RECT, sx.MOVIE_OBJ3)])
    events3 = [
        {
            "automaticPlay": True,
            "baseLayer": base3,
            "effects": [sx._movie_effect(sx.MOVIE_OBJ3, sx.CLIP, sx.MOVIE_RECT)],
        },
        sx._transition(t3[2]),
    ]
    root = _write_deck(
        tmp_path,
        {u: (pages, events), u3: ([(1920, 1080, ""), (320, 180, ""), (1920, 1080, "")], events3)},
    )
    return load_deck(root)


def test_states_movies_and_transitions(tmp_path):
    deck = _deck_with_builds(tmp_path)
    s2, s3 = deck.slides
    # ACC: two click states; the automatic movie start folds into the first
    assert [st.event_index for st in s2.states] == [1, 2]
    assert [m.asset_id for m in s2.states[0].movies] == [sx.CLIP]
    assert s2.states[1].transition == "dissolve"
    assert s2.states[1].is_final and not s2.states[0].is_final
    # the final state's per-object tree is replayed from event 1 + its builds
    leaves = {lf.object_id: lf.visible for lf in s2.states[1].leaves()}
    assert leaves[sx.CITE_OBJ] is True
    assert leaves[sx.LABEL_OBJ] is False
    assert s3.states[0].movies[0].rect.snapped() == s2.states[0].movies[0].rect.snapped() == (200, 300, 640, 360)
    assert len(deck.states) == 3


def test_apply_builds_does_not_mutate():
    tree = parse_layer(sx._root([sx._object("T" * 32, (0, 0, 10, 10), "OBJ", hidden=True)]))
    from talks_build.keynote import Effect

    out = apply_builds(tree, [Effect("buildIn", "apple:appear", "OBJ", 0.0, None, None)])
    assert [lf.visible for lf in leaves_of(tree)] == [False]
    assert [lf.visible for lf in leaves_of(out)] == [True]


# ---------------------------------------------------------------------------
# The real export (set TALKS_EXPORT)


@pytest.mark.export
def test_real_export_counts(real_export):
    deck = load_deck(real_export)
    assert len(deck.slides) == 114
    assert len(deck.states) == 123
    assert sum(len(s.movies()) for s in deck.slides) == 28


@pytest.mark.export
def test_real_export_replay_matches_keynote(real_export):
    """Replaying an event's builds gives exactly Keynote's next per-object tree."""
    deck = load_deck(real_export)
    checked = 0
    for slide in deck.slides:
        for a, b in zip(slide.events, slide.events[1:], strict=False):
            if a.flattened or b.flattened:
                continue
            got = [(lf.path, lf.visible) for lf in leaves_of(apply_builds(a.base, a.effects))]
            want = [(lf.path, lf.visible) for lf in leaves_of(b.base)]
            assert got == want, f"slide {slide.n} event {a.index}"
            checked += 1
    assert checked >= 4


@pytest.mark.export
def test_real_export_spans(real_export):
    """Same source at the same rect on consecutive states: slides 21-24, 34-36, 37-38, 106-110."""
    deck = load_deck(real_export)
    states = deck.states

    def key(state, m):
        return (m.asset_id, m.rect.snapped())

    runs = []
    for prev, cur in zip(states, states[1:], strict=False):
        shared = {key(prev, m) for m in prev.movies} & {key(cur, m) for m in cur.movies}
        if shared:
            runs.append((prev.slide.n, cur.slide.n))
    pairs = {p for p in runs if p[0] != p[1]}
    assert {
        (21, 22),
        (22, 23),
        (23, 24),
        (34, 35),
        (35, 36),
        (37, 38),
        (106, 107),
        (107, 108),
        (108, 109),
        (109, 110),
    } <= pairs
