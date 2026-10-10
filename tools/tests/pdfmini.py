"""A minimal PDF writer for text-extraction tests (hand-written objects, no dependencies)."""

from __future__ import annotations


def write_pdf(objects: list[str | bytes], root: int) -> bytes:
    """Serialise ``objects`` (numbered from 1) with a classic xref table."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        data = body.encode("latin-1") if isinstance(body, str) else body
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + data + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def stream(data: str) -> str:
    return f"<< /Length {len(data.encode('latin-1'))} >>\nstream\n{data}\nendstream"


def tounicode_cmap(pairs: dict[int, str]) -> str:
    rows = "\n".join(f"<{c:02X}> <{t.encode('utf-16-be').hex().upper()}>" for c, t in sorted(pairs.items()))
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CMapName /Test def /CMapType 2 def\n1 begincodespacerange <00> <FF> endcodespacerange\n"
        f"{len(pairs)} beginbfchar\n{rows}\nendbfchar\nendcmap\n"
        "CMapName currentdict /CMap defineresource pop end end"
    )


def maths_pdf() -> bytes:
    """One page: a re-encoded maths font with a wrong ToUnicode, plain text, and transparent text.

    Font F2 is named like a TeX maths font; its /Differences say codes 33-35
    are beta, infinity and a19 (a check mark), but its ToUnicode says omega,
    an up arrow and scissors, as Keynote's export writes it. ``Hidden words``
    is painted at fill opacity 0.
    """
    content = (
        "BT /F1 40 Tf 100 700 Td (Visible words) Tj ET\n"
        'BT /F2 40 Tf 100 600 Td (!"#) Tj ET\n'
        "q /GS0 gs BT /F1 40 Tf 100 500 Td (Hidden words) Tj ET Q\n"
        "BT /F1 40 Tf 100 400 Td (Shown again) Tj ET\n"
    )
    objs: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 800 800] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> /ExtGState << /GS0 8 0 R >> >> >>",
        stream(content),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /AAAAAB+CMMI12 /FirstChar 33 /LastChar 35 "
        "/Widths [600 600 600] /Encoding << /Type /Encoding /Differences [33 /beta /infinity /a19] >> "
        "/ToUnicode 7 0 R >>",
        stream(tounicode_cmap({33: "ω", 34: "↑", 35: "✁"})),
        "<< /Type /ExtGState /ca 0 /CA 0 >>",
    ]
    return write_pdf(objs, root=1)
