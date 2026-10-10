"""Slide text from PDFs (poppler ``pdftotext``) and font lookup."""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .geometry import Rect
from .proc import ToolError, run

XHTML = "{http://www.w3.org/1999/xhtml}"

# File names Keynote leaks into text and accessibility labels.
FILENAME_RE = re.compile(
    r"(?<![\w/])[\w~+-][\w.~+-]*\.(?:pdf|png|jpe?g|gif|tiff?|bmp|heic|svg|eps|mov|mp4|m4v|avi|webm|key|gifv)\b",
    re.IGNORECASE,
)
OBJECT_REPLACEMENT = "￼"


@dataclass(frozen=True)
class Word:
    text: str
    box: Rect


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def box(self) -> Rect:
        r = self.words[0].box
        for w in self.words[1:]:
            r = r.union(w.box)
        return r


# A glyph subset slot without a Unicode mapping: some embedded PDF pages
# re-encode the "ff" ligature at 0x21, so "differentiate" extracts as
# "di!erentiate". Between two lower-case letters "!" is never punctuation.
LIGATURE_FF_RE = re.compile(r"(?<=[a-z])!(?=[a-z])")


# A combining long solidus that TeX draws *before* its base (\not=): move it
# after the base so NFC composes, for example, "=" + U+0338 into U+2260.
NEGATION_RE = re.compile("\u0338" + r"\s*(\S)")
# TeX's degree sign is a superscript \circ (CMSY "openbullet") after a number.
DEGREE_RE = re.compile(r"(?<=\d)\s?" + "\u25e6")


# TeX builds its long arrows from two glyphs (\iff is a left and a right
# double arrow); text extraction returns the pieces.
LONG_ARROWS = {
    "\u21d0\u21d2": "\u27fa",  # ⇐⇒ -> ⟺ (\iff, \Longleftrightarrow)
    "=\u21d2": "\u27f9",  # =⇒ -> ⟹ (\implies, \Longrightarrow)
    "\u21d0=": "\u27f8",  # ⇐= -> ⟸ (\impliedby, \Longleftarrow)
    "\u2190\u2192": "\u27f7",  # ←→ -> ⟷ (\longleftrightarrow)
    "\u2212\u2192": "\u27f6",  # −→ -> ⟶ (\longrightarrow)
    "\u2190\u2212": "\u27f5",  # ←− -> ⟵ (\longleftarrow)
}


def clean_text(text: str) -> str:
    """Collapse whitespace, drop file names, unknown glyphs and object-replacement marks."""
    text = text.replace("\ufffd", " ")
    for pieces, arrow in LONG_ARROWS.items():
        text = text.replace(pieces, arrow)
    text = NEGATION_RE.sub(lambda m: m.group(1) + "\u0338", text)
    text = DEGREE_RE.sub("\u00b0", text)
    text = unicodedata.normalize("NFC", text)
    text = LIGATURE_FF_RE.sub("ff", text)
    text = text.replace(OBJECT_REPLACEMENT, " ")
    text = FILENAME_RE.sub(" ", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meaningful(line: str) -> bool:
    letters = sum(1 for ch in line if ch.isalpha())
    return letters >= 3


def title_lines_from_blocks(blocks: list[list[Line]]) -> list[str]:
    """Title lines from ``pdftotext -bbox-layout`` blocks in reading order.

    The first meaningful line, continued by up to two following lines of the
    same block with the same type size (a title wrapped over lines).
    """
    for block in blocks:
        for i, line in enumerate(block):
            first = clean_text(line.text)
            if not _meaningful(first):
                continue
            parts = [first]
            height = line.box.h
            for nxt in block[i + 1 : i + 3]:
                if abs(nxt.box.h - height) > 0.12 * height:
                    break
                parts.append(clean_text(nxt.text))
            return [p for p in parts if p]
    return []


def slide_title(raw: str, blocks: list[list[Line]] | None) -> str | None:
    """First meaningful text line, joined with its wrapped continuation.

    The first line comes from reading-order text (``pdftotext`` joins
    subscripts to their base there); block geometry only decides whether the
    next lines continue it.
    """
    title = title_from_text(raw)
    if title is None or not blocks:
        return title
    lines = title_lines_from_blocks(blocks)
    if lines and lines[0].replace(" ", "") == title.replace(" ", ""):
        return " ".join([title] + lines[1:])
    return title


def pdf_pages_blocks(pdf: Path) -> list[list[list[Line]]]:
    """Per page, per block, the lines of ``pdftotext -bbox-layout`` output."""
    out = run(["pdftotext", "-enc", "UTF-8", "-bbox-layout", str(pdf), "-"], capture=True).stdout
    root = ET.fromstring(re.sub(rb"<!DOCTYPE[^>]*>", b"", out))
    pages: list[list[list[Line]]] = []
    for page_el in root.iter(f"{XHTML}page"):
        blocks: list[list[Line]] = []
        for block_el in page_el.iter(f"{XHTML}block"):
            lines = [ln for ln in (_line(el) for el in block_el.iter(f"{XHTML}line")) if ln is not None]
            if lines:
                blocks.append(lines)
        pages.append(blocks)
    return pages


def _line(line_el) -> Line | None:
    words = []
    for w in line_el.iter(f"{XHTML}word"):
        box = Rect.from_corners(float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")))
        words.append(Word((w.text or "").strip(), box))
    return Line(tuple(words)) if words else None


# Separation, as a fraction of the shorter word's height, below which two
# word boxes of a line are joined without a space (a subscript, or a symbol
# set in another font), as ``pdftotext``'s plain output does.
JOIN_GAP = 0.05


def attach_marks(blocks: list[list[Line]]) -> list[list[Line]]:
    """Put stray one-symbol blocks back into the line they belong to.

    ``pdftotext`` gives a raised symbol (a TeX degree sign, a prime) a block of
    its own when it sticks out above its line, so it would be read before the
    line. A block holding a single word of at most two non-alphanumeric
    characters whose centre lies within another line's box joins that line,
    in x order.
    """
    lines = [(bi, li, line) for bi, block in enumerate(blocks) for li, line in enumerate(block)]
    moves: dict[tuple[int, int], list[Word]] = {}
    gone: set[int] = set()
    for bi, block in enumerate(blocks):
        if len(block) != 1 or len(block[0].words) != 1:
            continue
        w = block[0].words[0]
        if not w.text or len(w.text) > 2 or any(ch.isalnum() for ch in w.text):
            continue
        cx, cy = w.box.x + w.box.w / 2, w.box.y + w.box.h / 2
        for obi, oli, line in lines:
            if obi == bi:
                continue
            b = line.box
            if b.x <= cx <= b.x2 and b.y <= cy <= b.y2:
                moves.setdefault((obi, oli), []).append(w)
                gone.add(bi)
                break
    if not moves:
        return blocks
    out = []
    for bi, block in enumerate(blocks):
        if bi in gone:
            continue
        new_block = []
        for li, line in enumerate(block):
            extra = moves.get((bi, li))
            if extra:
                line = Line(tuple(sorted(line.words + tuple(extra), key=lambda w: w.box.x)))
            new_block.append(line)
        out.append(new_block)
    return out


def blocks_text(blocks: list[list[Line]]) -> str:
    """Page text from ``pdftotext -bbox-layout`` blocks, one line per text line."""
    out = []
    for block in blocks:
        for line in block:
            parts = []
            for i, w in enumerate(line.words):
                if i:
                    prev = line.words[i - 1]
                    gap = w.box.x - prev.box.x2
                    if gap >= JOIN_GAP * min(prev.box.h, w.box.h):
                        parts.append(" ")
                parts.append(w.text)
            out.append("".join(parts))
    return "\n".join(out)


@dataclass(frozen=True)
class Run:
    """A text run from ``pdftohtml -xml`` (page points) and whether it is painted."""

    box: Rect
    visible: bool


# Fill opacity below which text is never seen (Beamer's covered overlays are 0).
INVISIBLE_OPACITY = 0.05


def pdf_pages_runs(pdf: Path) -> list[list[Run]]:
    """Per page, the text runs of ``pdftohtml -xml`` with their opacity.

    ``pdftotext`` extracts text painted at zero opacity too (Beamer pages keep
    later overlays in place, transparent); ``pdftohtml`` reports the opacity.
    """
    out = run(["pdftohtml", "-xml", "-i", "-stdout", "-zoom", "1", "-q", str(pdf)], capture=True).stdout
    root = ET.fromstring(re.sub(rb"<!DOCTYPE[^>]*>", b"", out))
    opacity: dict[str, float] = {}
    for spec in root.iter("fontspec"):
        try:
            opacity[spec.get("id", "")] = float(spec.get("opacity", "1"))
        except ValueError:
            opacity[spec.get("id", "")] = 1.0
    pages: list[list[Run]] = []
    for page_el in root.iter("page"):
        runs = []
        for t in page_el.iter("text"):
            try:
                box = Rect(float(t.get("left")), float(t.get("top")), float(t.get("width")), float(t.get("height")))
            except (TypeError, ValueError):
                continue
            runs.append(Run(box, opacity.get(t.get("font", ""), 1.0) >= INVISIBLE_OPACITY))
        pages.append(runs)
    return pages


def drop_invisible(blocks: list[list[Line]], runs: list[Run], slack: float = 1.5) -> tuple[list[list[Line]], int]:
    """Remove words that lie only inside transparent text runs; return (blocks, words dropped).

    A word is dropped when its centre falls inside a run painted at zero
    opacity and inside no visible run. Runs are rounded to whole points, so
    they are grown by ``slack``.
    """
    hidden = [r.box.inflate(slack) for r in runs if not r.visible]
    if not hidden:
        return blocks, 0
    shown = [r.box.inflate(slack) for r in runs if r.visible]

    def inside(x: float, y: float, boxes: list[Rect]) -> bool:
        return any(b.x <= x <= b.x2 and b.y <= y <= b.y2 for b in boxes)

    dropped = 0
    out_blocks: list[list[Line]] = []
    for block in blocks:
        lines = []
        for line in block:
            words = []
            for w in line.words:
                cx, cy = w.box.x + w.box.w / 2, w.box.y + w.box.h / 2
                if inside(cx, cy, hidden) and not inside(cx, cy, shown):
                    dropped += 1
                    continue
                words.append(w)
            if words:
                lines.append(Line(tuple(words)))
        if lines:
            out_blocks.append(lines)
    return out_blocks, dropped


def title_from_text(raw: str) -> str | None:
    """First meaningful line of a page's text (at least three letters)."""
    for line in raw.splitlines():
        line = clean_text(line)
        if _meaningful(line):
            return line
    return None


def pdf_pages_text(pdf: Path) -> list[str]:
    """Raw text of every page of ``pdf`` (reading order), one string per page."""
    out = run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture=True, text=True).stdout
    pages = out.split("\f")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    return pages


def page_lines(pdf: Path, page_index: int) -> list[Line]:
    """Text lines with word boxes (page points) from ``pdftotext -bbox-layout``."""
    out = run(
        [
            "pdftotext",
            "-enc",
            "UTF-8",
            "-bbox-layout",
            "-f",
            str(page_index + 1),
            "-l",
            str(page_index + 1),
            str(pdf),
            "-",
        ],
        capture=True,
    ).stdout
    # Strip the DOCTYPE so ElementTree does not try to resolve it.
    text = re.sub(rb"<!DOCTYPE[^>]*>", b"", out)
    root = ET.fromstring(text)
    return [ln for ln in (_line(el) for el in root.iter(f"{XHTML}line")) if ln is not None]


def find_words(lines: list[Line], needle: str) -> tuple[Line, int, int] | None:
    """Locate ``needle`` (whitespace-insensitive) as a run of whole words.

    Returns ``(line, first_word_index, last_word_index_exclusive)``.
    """
    target = needle.split()
    n = len(target)
    for line in lines:
        texts = [w.text for w in line.words]
        for i in range(0, len(texts) - n + 1):
            if texts[i : i + n] == target:
                return line, i, i + n
    return None


def page_fonts(pdf: Path, page_index: int) -> list[str]:
    """Font names used on one page (subset prefix removed)."""
    out = run(
        ["pdffonts", "-f", str(page_index + 1), "-l", str(page_index + 1), str(pdf)], capture=True, text=True
    ).stdout
    names = []
    for row in out.splitlines()[2:]:
        if not row.strip():
            continue
        name = row.split()[0]
        if "+" in name:
            name = name.split("+", 1)[1]
        if name not in names:
            names.append(name)
    return names


# Keynote PostScript names -> TeX Live cm-unicode files (CMU fonts).
CMU_FILES = {
    "CMUSerif-Roman": "cmunrm.otf",
    "CMUSerif-Bold": "cmunbx.otf",
    "CMUSerif-Italic": "cmunti.otf",
    "CMUSerif-BoldItalic": "cmunbi.otf",
    "CMUSansSerif": "cmunss.otf",
    "CMUSansSerif-Bold": "cmunsx.otf",
    "CMUSansSerif-Oblique": "cmunsi.otf",
}


def font_file(postscript_name: str) -> Path | None:
    """Find a local font file for a PostScript font name, or ``None``."""
    candidates: list[list[str]] = []
    if postscript_name in CMU_FILES:
        candidates.append(["kpsewhich", CMU_FILES[postscript_name]])
    candidates.append(["fc-match", "-f", "%{file}", f":postscriptname={postscript_name}"])
    for args in candidates:
        try:
            out = run(args, capture=True, text=True).stdout.strip()
        except ToolError:
            continue
        if not out:
            continue
        path = Path(out.splitlines()[0])
        if not path.is_file():
            continue
        if args[0] == "fc-match":
            # fc-match always answers; accept only a file whose name matches.
            stem = re.sub(r"[^a-z0-9]", "", path.stem.lower())
            want = re.sub(r"[^a-z0-9]", "", postscript_name.lower())
            family = re.sub(r"[^a-z0-9]", "", postscript_name.split("-")[0].lower())
            if want not in stem and family not in stem:
                continue
        return path
    return None


def replace_spaced(text: str, find: str, replace: str) -> str:
    """Replace ``find`` in ``text`` allowing any run of whitespace between words."""
    words = find.split()
    if not words:
        return text
    pattern = r"\s+".join(re.escape(w) for w in words)
    return re.sub(pattern, lambda _m: replace, text)
