"""Video identity (dedupe by decoded stream, not file bytes) and encoding."""

import hashlib

import pytest
import synthetic_export as sx
from conftest import needs_tools

from talks_build.keynote import load_deck
from talks_build.video import encode, first_frame, probe, stream_hash


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_tools
@pytest.mark.slow
def test_stream_hash_ignores_container_timestamps(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    c = tmp_path / "c.mp4"
    sx._movie(a)
    sx._movie(b, src=a, ts_offset=3.0)  # Keynote-style re-mux: same packets
    sx._movie(c, colour_shift=120)  # different picture
    assert _sha(a) != _sha(b)
    assert stream_hash(a) == stream_hash(b)
    assert stream_hash(a) != stream_hash(c)


@needs_tools
@pytest.mark.slow
def test_encode_is_h264_even_and_deterministic(tmp_path):
    src = tmp_path / "src.mp4"
    sx._movie(src)
    out1, out2 = tmp_path / "o1.mp4", tmp_path / "o2.mp4"
    encode(src, out1, 160, 90)
    encode(src, out2, 160, 90)
    p = probe(out1)
    assert (p.codec, p.width, p.height) == ("h264", 160, 90)
    assert _sha(out1) == _sha(out2)
    assert first_frame(out1).size == (160, 90)
    with pytest.raises(ValueError):
        encode(src, tmp_path / "odd.mp4", 161, 90)


@needs_tools
@pytest.mark.slow
def test_synthetic_export_dedupes_copies(synthetic_export):
    deck = load_deck(synthetic_export)
    uses = [m for s in deck.slides for m in s.movies() if m.asset_id == sx.CLIP]
    assert len({m.file for m in uses}) == 2
    assert len({_sha(m.file) for m in uses}) == 2
    assert len({stream_hash(m.file) for m in uses}) == 1


@needs_tools
@pytest.mark.export
@pytest.mark.slow
def test_real_export_has_fifteen_unique_movies(real_export):
    deck = load_deck(real_export)
    files = {m.file for s in deck.slides for m in s.movies()}
    assert len(files) == 28
    assert len({stream_hash(f) for f in files}) == 15
