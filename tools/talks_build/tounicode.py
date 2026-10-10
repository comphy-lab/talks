"""Repair wrong ``ToUnicode`` maps of re-encoded TeX fonts before text extraction.

Keynote's PDF export (Quartz) splits each embedded TeX font into a copy in
MacRoman and a ``Custom`` copy for the remaining glyphs, which it re-encodes
from code 33 (``!``) upwards. The ``Custom`` copy's ``/Encoding /Differences``
names the glyphs correctly (``/beta``, ``/nu``, ``/arrowright``, ``/infinity``,
``/ff``), but its ``ToUnicode`` CMap maps each code by its position in the
original TeX encoding: code 33 becomes ``omega`` in CMMI (OML) and an up arrow
in CMSY (OMS). ``pdftotext`` trusts ``ToUnicode``, so ``beta < 49.3`` came out
as ``omega < 49.3`` and ``We_j >> 1`` as ``We j -> 1``.

:func:`repair_pdf` writes a copy of a PDF in which every simple font whose
``ToUnicode`` contradicts its own glyph names gets a new ``ToUnicode`` built
from those names. Codes whose glyph name is unknown map to U+FFFD, which
:func:`talks_build.text.clean_text` drops: a missing symbol is better than a
wrong one. Fonts whose maps agree with their glyph names are left alone.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

UNKNOWN = "\ufffd"
REPAIR_VERSION = "2"

# Glyph names (Adobe Glyph List names plus the TeX font names) -> text. An
# empty string drops the glyph (pieces of extensible delimiters, accents
# drawn as separate glyphs).
# fmt: off
_NAMES: dict[str, str] = {
    # ASCII punctuation
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$", "percent": "%",
    "ampersand": "&", "quotesingle": "'", "quoteright": "’", "quoteleft": "‘", "parenleft": "(",
    "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",", "hyphen": "-", "period": ".", "slash": "/",
    "colon": ":", "semicolon": ";", "less": "<", "equal": "=", "greater": ">", "question": "?", "at": "@",
    "bracketleft": "[", "backslash": "\\", "bracketright": "]", "asciicircum": "^", "underscore": "_",
    "grave": "`", "braceleft": "{", "bar": "|", "braceright": "}", "asciitilde": "~",
    # Latin text extras
    "ff": "ff", "fi": "fi", "fl": "fl", "ffi": "ffi", "ffl": "ffl", "dotlessi": "ı", "dotlessj": "ȷ",
    "germandbls": "ß", "ae": "æ", "oe": "œ", "oslash": "ø", "AE": "Æ", "OE": "Œ",
    "Oslash": "Ø", "exclamdown": "¡", "questiondown": "¿", "endash": "–", "emdash": "—",
    "quotedblleft": "“", "quotedblright": "”", "quotedblbase": "„", "quotesinglbase": "‚",
    "guillemotleft": "«", "guillemotright": "»", "guilsinglleft": "‹", "guilsinglright": "›",
    "ellipsis": "…", "perthousand": "‰", "degree": "°", "copyright": "©",
    "registered": "®", "trademark": "™", "sterling": "£", "yen": "¥", "cent": "¢",
    "florin": "ƒ", "mu": "μ", "visiblespace": "␣", "dollaroldstyle": "$",
    # Accents drawn as separate glyphs: dropped (the letter remains)
    "acute": "", "caron": "", "breve": "", "macron": "", "ring": "", "cedilla": "", "dieresis": "",
    "circumflex": "", "tilde": "", "hungarumlaut": "", "ogonek": "", "dotaccent": "", "tie": "", "vector": "",
    # Greek (TeX: epsilon is the lunate form, epsilon1 the curly one; phi/phi1 likewise)
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "epsilon1": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "theta1": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "pi1": "ϖ", "omega1": "ϖ", "rho": "ρ", "rho1": "ϱ",
    "sigma": "σ", "sigma1": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "phi1": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Alpha": "Α", "Beta": "Β", "Gamma": "Γ", "Delta": "Δ", "Epsilon": "Ε",
    "Zeta": "Ζ", "Eta": "Η", "Theta": "Θ", "Iota": "Ι", "Kappa": "Κ",
    "Lambda": "Λ", "Mu": "Μ", "Nu": "Ν", "Xi": "Ξ", "Omicron": "Ο", "Pi": "Π",
    "Rho": "Ρ", "Sigma": "Σ", "Tau": "Τ", "Upsilon": "Υ", "Upsilon1": "ϒ",
    "Phi": "Φ", "Chi": "Χ", "Psi": "Ψ", "Omega": "Ω",
    # CMMI symbols
    "partialdiff": "∂", "lscript": "ℓ", "weierstrass": "℘", "star": "⋆",
    "harpoonleftup": "↼", "harpoonleftdown": "↽", "harpoonrightup": "⇀",
    "harpoonrightdown": "⇁", "hookleft": "", "hookright": "", "triangleright": "▹",
    "triangleleft": "◃", "flat": "♭", "natural": "♮", "sharp": "♯",
    "slurbelow": "⌣", "slurabove": "⌢",
    # CMSY symbols
    "minus": "−", "periodcentered": "·", "multiply": "×", "asteriskmath": "∗",
    "divide": "÷", "diamondmath": "⋄", "plusminus": "±", "minusplus": "∓",
    "circleplus": "⊕", "circleminus": "⊖", "circlemultiply": "⊗", "circledivide": "⊘",
    "circledot": "⊙", "circlecopyrt": "◯", "openbullet": "◦", "bullet": "•",
    "equivasymptotic": "≍", "equivalence": "≡", "reflexsubset": "⊆",
    "reflexsuperset": "⊇", "lessequal": "≤", "greaterequal": "≥", "precedesequal": "⪯",
    "followsequal": "⪰", "similar": "∼", "approxequal": "≈", "propersubset": "⊂",
    "propersuperset": "⊃", "lessmuch": "≪", "greatermuch": "≫", "precedes": "≺",
    "follows": "≻", "arrowleft": "←", "arrowright": "→", "arrowup": "↑",
    "arrowdown": "↓", "arrowboth": "↔", "arrownortheast": "↗", "arrowsoutheast": "↘",
    "similarequal": "≃", "arrowdblleft": "⇐", "arrowdblright": "⇒", "arrowdblup": "⇑",
    "arrowdbldown": "⇓", "arrowdblboth": "⇔", "arrownorthwest": "↖",
    "arrowsouthwest": "↙", "proportional": "∝", "prime": "′", "infinity": "∞",
    "element": "∈", "owner": "∋", "triangle": "△", "triangleinv": "▽",
    "negationslash": "\u0338", "mapsto": "", "universal": "∀", "existential": "∃",
    "logicalnot": "¬", "emptyset": "∅", "Rfractur": "ℜ", "Ifractur": "ℑ",
    "latticetop": "⊤", "perpendicular": "⊥", "aleph": "ℵ", "union": "∪",
    "intersection": "∩", "unionmulti": "⊎", "logicaland": "∧", "logicalor": "∨",
    "turnstileleft": "⊢", "turnstileright": "⊣", "floorleft": "⌊", "floorright": "⌋",
    "ceilingleft": "⌈", "ceilingright": "⌉", "angbracketleft": "⟨",
    "angbracketright": "⟩", "bardbl": "‖", "arrowbothv": "↕", "arrowdblbothv": "⇕",
    "wreathproduct": "≀", "radical": "√", "coproduct": "∐", "nabla": "∇",
    "integral": "∫", "unionsq": "⊔", "intersectionsq": "⊓", "subsetsqequal": "⊑",
    "supersetsqequal": "⊒", "section": "§", "dagger": "†", "daggerdbl": "‡",
    "paragraph": "¶", "club": "♣", "diamond": "♢", "heart": "♡", "spade": "♠",
    # CMEX: whole operators and delimiters (sizes handled in glyph_text)
    "summation": "∑", "product": "∏", "contintegral": "∮",
    "arrowvertex": "", "arrowtp": "↑", "arrowbt": "↓",
    "arrowdbltp": "⇑", "arrowdblbt": "⇓", "braceex": "", "vextendsingle": "", "vextenddouble": "",
}
# fmt: on

# ITC Zapf Dingbats (pifont's \ding), first row: a19 is \ding{51} (check
# mark) and a23 is \ding{55} (ballot X).
_ZAPF = {
    "a1": 0x2701, "a2": 0x2702, "a202": 0x2703, "a3": 0x2704, "a4": 0x260E, "a5": 0x2706, "a119": 0x2707,
    "a118": 0x2708, "a117": 0x2709, "a11": 0x261B, "a12": 0x261E, "a13": 0x270C, "a14": 0x270D,
    "a15": 0x270E, "a16": 0x270F, "a105": 0x2710, "a17": 0x2711, "a18": 0x2712, "a19": 0x2713,
    "a20": 0x2714, "a21": 0x2715, "a22": 0x2716, "a23": 0x2717, "a24": 0x2718,
}  # fmt: skip
_NAMES.update({name: chr(cp) for name, cp in _ZAPF.items()})

_DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_ACCENTS = {
    "acute": "ACUTE",
    "grave": "GRAVE",
    "circumflex": "CIRCUMFLEX",
    "dieresis": "DIAERESIS",
    "tilde": "TILDE",
    "ring": "RING ABOVE",
    "cedilla": "CEDILLA",
    "caron": "CARON",
    "breve": "BREVE",
    "macron": "MACRON",
    "ogonek": "OGONEK",
    "dotaccent": "DOT ABOVE",
    "hungarumlaut": "DOUBLE ACUTE",
    "slash": "STROKE",
}
# CMEX big delimiters and operators: <base><size>, e.g. parenleftbig, summationdisplay.
_SIZE_RE = re.compile(r"^(?P<base>[A-Za-z]+?)(?P<size>big|Big|bigg|Bigg|BigG|text|display|tp|bt|ex|mid|vertex)$")
_PIECE_SIZES = {"ex", "mid", "bt", "vertex"}
_CMEX_BASES = {
    "summation": "∑",
    "product": "∏",
    "coproduct": "∐",
    "integral": "∫",
    "contintegral": "∮",
    "union": "⋃",
    "intersection": "⋂",
    "unionmulti": "⨄",
    "logicaland": "⋀",
    "logicalor": "⋁",
    "circledot": "⨀",
    "circleplus": "⨁",
    "circlemultiply": "⨂",
    "unionsq": "⨆",
    "radical": "√",
}


def glyph_text(name: str) -> str | None:
    """Text for a glyph name, ``""`` to drop the glyph, or ``None`` if unknown."""
    if name in _NAMES:
        return _NAMES[name]
    if len(name) == 1 and name.isalpha():
        return name
    if name in _DIGITS:
        return str(_DIGITS.index(name))
    m = re.fullmatch(r"(zero|one|two|three|four|five|six|seven|eight|nine)oldstyle", name)
    if m:
        return str(_DIGITS.index(m.group(1)))
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})+", name)
    if m:
        return "".join(chr(int(name[i : i + 4], 16)) for i in range(3, len(name), 4))
    m = re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        return chr(int(m.group(1), 16))
    m = re.fullmatch(r"([A-Za-z])(" + "|".join(_ACCENTS) + ")", name)
    if m:
        case = "CAPITAL" if m.group(1).isupper() else "SMALL"
        try:
            return unicodedata.lookup(f"LATIN {case} LETTER {m.group(1).upper()} WITH {_ACCENTS[m.group(2)]}")
        except KeyError:
            return None
    m = _SIZE_RE.fullmatch(name)
    if m:
        base, size = m.group("base"), m.group("size")
        if size in _PIECE_SIZES:
            return ""  # middle and bottom pieces of a built-up delimiter
        if base in _CMEX_BASES:
            return _CMEX_BASES[base]
        if base in _NAMES:
            return _NAMES[base]
    if re.fullmatch(r"(hat|tilde)wide(r|st)?", name):
        return ""
    return None


# Pairs that differ only in form, so a ToUnicode entry using either is fine.
# fmt: off
_EQUIVALENT = [
    {"'", "’", "′"}, {"`", "‘"}, {'"', "“", "”"}, {"-", "−", "‐"},
    {"·", "⋅", "∙"}, {"ε", "ϵ"}, {"φ", "ϕ"}, {"~", "∼"},
    {"*", "∗"}, {"|", "∣"}, {"‖", "∥"}, {"◦", "∘"},
]
# fmt: on


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    na, nb = unicodedata.normalize("NFKC", a), unicodedata.normalize("NFKC", b)
    if na == nb:
        return True
    return any(na in group and nb in group for group in _EQUIVALENT)


# ----------------------------------------------------------------------------
# CMaps


def parse_cmap(data: bytes) -> dict[int, str]:
    """``bfchar`` and ``bfrange`` entries of a ToUnicode CMap (one-byte codes)."""
    text = data.decode("latin-1")
    out: dict[int, str] = {}

    def utf16(hexstr: str) -> str:
        raw = bytes.fromhex(hexstr)
        return raw.decode("utf-16-be", errors="replace")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, rest in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            a, b = int(lo, 16), int(hi, 16)
            if rest.startswith("["):
                for i, dst in enumerate(re.findall(r"<([0-9A-Fa-f]*)>", rest)):
                    out[a + i] = utf16(dst)
            else:
                start = bytes.fromhex(rest[1:-1])
                for i in range(b - a + 1):
                    if len(start) >= 2:
                        last = int.from_bytes(start[-2:], "big") + i
                        dst = start[:-2] + (last & 0xFFFF).to_bytes(2, "big")
                    else:
                        dst = start
                    out[a + i] = dst.decode("utf-16-be", errors="replace")
    return out


def build_cmap(mapping: dict[int, str], name: str = "Repaired") -> bytes:
    """A one-byte-code ToUnicode CMap for ``mapping``."""
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        f"/CMapName /{name} def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    items = sorted(mapping.items())
    for i in range(0, len(items), 100):
        chunk = items[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, text in chunk:
            dst = (text or UNKNOWN).encode("utf-16-be").hex().upper()
            lines.append(f"<{code:02X}> <{dst}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(lines) + "\n").encode("ascii")


# ----------------------------------------------------------------------------
# PDF


@dataclass
class FontRepair:
    name: str
    codes: int
    mismatches: list[tuple[int, str, str, str]] = field(default_factory=list)  # code, glyph, was, now
    unknown: list[str] = field(default_factory=list)


def differences(encoding) -> dict[int, str]:
    """``code -> glyph name`` from an ``/Encoding`` dictionary's ``/Differences``."""
    out: dict[int, str] = {}
    diffs = encoding.get("/Differences") if hasattr(encoding, "get") else None
    if not diffs:
        return out
    code = 0
    for item in diffs:
        value = item.get_object() if hasattr(item, "get_object") else item
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            code = int(value)
        else:
            out[code] = str(value).lstrip("/")
            code += 1
    return out


def plan_font(font) -> tuple[dict[int, str], FontRepair] | None:
    """The repaired code map of a font whose ToUnicode contradicts its glyph names, else ``None``."""
    tu_ref = font.get("/ToUnicode")
    enc = font.get("/Encoding")
    if tu_ref is None or enc is None:
        return None
    enc = enc.get_object()
    if not hasattr(enc, "get"):
        return None
    names = differences(enc)
    if not names:
        return None
    try:
        current = parse_cmap(tu_ref.get_object().get_data())
    except Exception:  # noqa: BLE001 - an unreadable CMap is left for pdftotext to judge
        return None
    rep = FontRepair(name=str(font.get("/BaseFont", "?")).lstrip("/"), codes=len(names))
    fixed = dict(current)
    for code, glyph in names.items():
        want = glyph_text(glyph)
        was = current.get(code)
        if want is None:
            rep.unknown.append(glyph)
            fixed[code] = UNKNOWN
            continue
        fixed[code] = want
        if was is not None and not _same(want, was):
            rep.mismatches.append((code, glyph, was, want))
    if not rep.mismatches:
        return None
    return fixed, rep


def _fonts(resources, seen_res: set[int]):
    """Every font dictionary reachable from a resource dictionary (forms and patterns included)."""
    if resources is None:
        return
    ref = getattr(resources, "idnum", None)
    res = resources.get_object()
    if ref is not None:
        if ref in seen_res:
            return
        seen_res.add(ref)
    if not hasattr(res, "get"):
        return
    fonts = res.get("/Font")
    if fonts is not None:
        for value in fonts.get_object().values():
            yield value
    for kind in ("/XObject", "/Pattern"):
        group = res.get(kind)
        if group is None:
            continue
        for value in group.get_object().values():
            obj = value.get_object()
            sub = obj.get("/Resources") if hasattr(obj, "get") else None
            if sub is not None:
                yield from _fonts(sub, seen_res)


def repair_pdf(src: Path, dst: Path) -> list[FontRepair]:
    """Write ``src`` to ``dst`` with repaired ToUnicode maps; return what changed.

    ``dst`` is written even when nothing needed repair, so callers can always
    read text from it.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject, StreamObject

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(src))
    writer = PdfWriter(clone_from=reader)
    repairs: list[FontRepair] = []
    done: set[int] = set()
    seen_res: set[int] = set()
    for page in writer.pages:
        for ref in _fonts(page.get("/Resources"), seen_res):
            key = getattr(ref, "idnum", None)
            if key is not None and key in done:
                continue
            if key is not None:
                done.add(key)
            font = ref.get_object()
            planned = plan_font(font)
            if planned is None:
                continue
            mapping, rep = planned
            stream = StreamObject()
            stream.set_data(build_cmap(mapping, name=f"Repaired{len(repairs)}"))
            font[NameObject("/ToUnicode")] = writer._add_object(stream)
            repairs.append(rep)
    buf = io.BytesIO()
    writer.write(buf)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(dst)
    return repairs


def repaired_copy(src: Path, cache_dir: Path) -> tuple[Path, list[dict]]:
    """Cached :func:`repair_pdf` of ``src``, keyed by its content; also returns a summary."""
    import json

    h = hashlib.sha256()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    stem = f"{h.hexdigest()[:24]}-tounicode{REPAIR_VERSION}"
    pdf = Path(cache_dir) / f"{stem}.pdf"
    meta = Path(cache_dir) / f"{stem}.json"
    if pdf.is_file() and meta.is_file():
        return pdf, json.loads(meta.read_text(encoding="utf-8"))
    repairs = repair_pdf(Path(src), pdf)
    summary = [
        {
            "font": r.name,
            "codes": r.codes,
            "wrong": len(r.mismatches),
            "examples": [f"{g}: {was!r} -> {now!r}" for _, g, was, now in r.mismatches[:4]],
            "unknown_glyphs": sorted(set(r.unknown)),
        }
        for r in repairs
    ]
    meta.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return pdf, summary


__all__ = ["glyph_text", "parse_cmap", "build_cmap", "plan_font", "repair_pdf", "repaired_copy", "UNKNOWN"]
