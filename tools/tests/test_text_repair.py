"""Slide text as a viewer sees it: repaired maths glyphs, no transparent text, marks in place."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from pdfmini import maths_pdf

from talks_build.geometry import Rect
from talks_build.text import (
    Line,
    Word,
    attach_marks,
    blocks_text,
    clean_text,
    drop_invisible,
    pdf_pages_blocks,
    pdf_pages_runs,
)
from talks_build.tounicode import build_cmap, glyph_text, parse_cmap, repaired_copy

# Reading real PDFs needs poppler, and the repair needs pypdf: "slow" tests, as
# for the other tests that run external tools (CI's quick job skips them).
_poppler = pytest.mark.skipif(
    not (shutil.which("pdftotext") and shutil.which("pdftohtml")), reason="needs poppler (pdftotext, pdftohtml)"
)


def needs_poppler(fn):
    return pytest.mark.slow(_poppler(fn))


def test_glyph_names_map_to_the_symbols_they_draw():
    assert glyph_text("beta") == "β" and glyph_text("nu") == "ν" and glyph_text("alpha") == "α"
    assert glyph_text("infinity") == "∞" and glyph_text("greatermuch") == "≫"
    assert glyph_text("arrowdblleft") == "⇐" and glyph_text("arrowdblright") == "⇒"
    assert glyph_text("openbullet") == "◦" and glyph_text("proportional") == "∝"
    assert glyph_text("ff") == "ff" and glyph_text("a19") == "✓" and glyph_text("a23") == "✗"
    assert glyph_text("parenleftbig") == "(" and glyph_text("summationdisplay") == "∑"
    assert glyph_text("parenleftex") == ""  # a piece of a built-up delimiter
    assert glyph_text("eacute") == "é" and glyph_text("uni03B2") == "β" and glyph_text("three") == "3"
    assert glyph_text("no-such-glyph") is None


def test_cmap_round_trip():
    mapping = {0x21: "β", 0x22: "∞", 0x23: "ff"}
    assert parse_cmap(build_cmap(mapping)) == mapping


@needs_poppler
def test_wrong_tounicode_is_rebuilt_from_glyph_names(tmp_path):
    pytest.importorskip("pypdf")
    src = tmp_path / "deck.pdf"
    src.write_bytes(maths_pdf())
    before = subprocess.run(["pdftotext", "-enc", "UTF-8", str(src), "-"], capture_output=True, text=True).stdout
    assert "ω↑✁" in before  # what the export's own map says

    fixed, summary = repaired_copy(src, tmp_path / "cache")
    after = subprocess.run(["pdftotext", "-enc", "UTF-8", str(fixed), "-"], capture_output=True, text=True).stdout
    assert "β∞✓" in after and "ω" not in after
    assert "Visible words" in after  # other fonts untouched
    assert [s["font"] for s in summary] == ["AAAAAB+CMMI12"] and summary[0]["wrong"] == 3
    # Cached by content: a second call returns the same file without rewriting it.
    mtime = fixed.stat().st_mtime_ns
    again, _ = repaired_copy(src, tmp_path / "cache")
    assert again == fixed and again.stat().st_mtime_ns == mtime


@needs_poppler
def test_transparent_text_is_left_out(tmp_path):
    src = tmp_path / "deck.pdf"
    src.write_bytes(maths_pdf())
    (blocks,) = pdf_pages_blocks(src)
    (runs,) = pdf_pages_runs(src)
    assert sorted(r.visible for r in runs) == [False, True, True, True]
    kept, dropped = drop_invisible(blocks, runs)
    text = blocks_text(kept)
    assert dropped == 2 and "Hidden" not in text
    assert "Visible words" in text and "Shown again" in text


def _w(text: str, x: float, y: float, w: float, h: float) -> Word:
    return Word(text, Rect(x, y, w, h))


def test_raised_mark_rejoins_its_line():
    # pdftotext puts the degree sign of "49.3°" in a block of its own, before the title.
    degree = [Line((_w("◦", 412, 29, 28, 40),))]
    title = [Line((_w("β", 124, 41, 44, 80), _w("<", 195, 41, 61, 80), _w("49.3", 278, 31, 134, 95),
                   _w("⇐⇒", 485, 39, 146, 138)))]  # fmt: skip
    text = clean_text(blocks_text(attach_marks([degree, title])))
    assert text == "β < 49.3° ⟺"
    # A label is never moved, even when it sits inside another line's box.
    label = [Line((_w("Oh", 300, 50, 40, 40),))]
    assert attach_marks([label, title]) == [label, title]


def test_clean_text_normalises_tex_constructions():
    assert clean_text("a ̸= b") == "a ≠ b"
    assert clean_text("x =⇒ y, p ⇐⇒ q, f −→ g") == "x ⟹ y, p ⟺ q, f ⟶ g"
    assert clean_text("bad � glyph") == "bad glyph"
