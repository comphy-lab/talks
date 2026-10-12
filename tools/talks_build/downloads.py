"""Downloadable copies of a talk: PDF, PowerPoint and Keynote (docs/SPEC.md section 2.1).

- :func:`derive_pdf` assembles one vector page per slide from the final-state
  textures of the Keynote HTML export. It is the flat PDF the build reads for
  slide text, and the source of the PDF download. It is *derived*, not
  Keynote's own PDF export.
- :func:`handout_pdf` turns it into the PDF download: text maps repaired
  (:mod:`talks_build.tounicode`), document metadata set, and every movie area
  linked to that slide on the website.
- :func:`build_pptx` writes one PowerPoint slide per click state from the
  build's lossless 2x stills (``build --stills``), with each video placed at
  its manifest rectangle, playing automatically and looping, and the overlay
  above it. Nothing is re-typeset, so every slide matches the web player.
- :func:`build_key` opens that PowerPoint file in Keynote (macOS), checks it
  and saves it as a Keynote document.

Each file is stored as ``build/<slug>/media/<sha256[:16]>/<name>`` and listed
in the manifest's ``downloads``; the hash directory keeps keys
content-addressed while the browser saves the file under its own name.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .keynote import export_digest, leaves_of, load_deck

MAX_BYTES = 300_000_000  # the cf CLI's single-request upload limit

# PowerPoint geometry: a 1920 x 1080 canvas on the standard 13.333 x 7.5 in slide.
EMU_PER_PX = 6350
SLIDE_W, SLIDE_H = 1920 * EMU_PER_PX, 1080 * EMU_PER_PX


class DownloadError(RuntimeError):
    pass


def file_name(speaker: str, slug: str, updated: str, fmt: str) -> str:
    surname = re.sub(r"[^A-Za-z0-9-]", "", speaker.split()[-1]) if speaker.split() else ""
    stem = f"{surname}-{slug}-{updated}" if surname else f"{slug}-{updated}"
    return f"{stem}.{fmt}"


def sha16_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------
# PDF


def derive_pdf(export_dir: Path, out: Path) -> dict:
    """One page per slide: the slide's final flattened texture, cropped to the canvas."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    deck = load_deck(Path(export_dir))
    writer = PdfWriter()
    readers: dict[Path, PdfReader] = {}
    for slide in deck.slides:
        visible = [leaf for leaf in leaves_of(slide.final_event.base) if leaf.visible]
        if len(visible) != 1:
            raise DownloadError(f"slide {slide.n}: final state is not one flattened texture")
        leaf = visible[0]
        src = slide.texture_pdf(leaf.texture)
        reader = readers.setdefault(src, PdfReader(src))
        page = writer.add_page(reader.pages[slide.texture_page(leaf.texture)])
        box = page.mediabox
        left, bottom, height = float(box.left), float(box.bottom), float(box.height)
        # Canvas point (cx, cy), y down, lies at (left + cx - x, bottom + height - (cy - y)) on the texture page.
        x, y = leaf.rect.x, leaf.rect.y
        top = bottom + height + y
        crop = RectangleObject([left - x, top - deck.height, left - x + deck.width, top])
        page.mediabox = crop
        page.cropbox = crop
        for name in ("/TrimBox", "/BleedBox", "/ArtBox"):
            if name in page:
                del page[name]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f:
        writer.write(f)
    return {"pages": len(deck.slides), "path": str(out)}


def handout_pdf(derived: Path, manifest: dict, page_url: str, out: Path) -> dict:
    """The PDF download: repaired text maps, metadata, and movie areas linked to the web slide."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link

    from .tounicode import repair_pdf

    with tempfile.TemporaryDirectory(prefix="talks-handout-") as tmp:
        repaired = Path(tmp) / "repaired.pdf"
        fonts = repair_pdf(Path(derived), repaired)
        reader = PdfReader(repaired)
        if len(reader.pages) != len(manifest["slides"]):
            raise DownloadError(f"derived PDF has {len(reader.pages)} pages for {len(manifest['slides'])} slides")
        writer = PdfWriter(clone_from=reader)
        links = 0
        for i, slide in enumerate(manifest["slides"]):
            page = writer.pages[i]
            box = page.mediabox
            left, top = float(box.left), float(box.top)
            sx = float(box.width) / manifest["canvas"]["width"]
            sy = float(box.height) / manifest["canvas"]["height"]
            # The player's deep-link prefix: the first 8 hex digits of the slide id.
            url = f"{page_url}#{re.sub(r'[^0-9a-fA-F]', '', slide['id'])[:8].lower()}"
            seen = set()
            for state in slide["states"]:
                for item in state["media"]:
                    rect = tuple(item["rect"])
                    if rect in seen:
                        continue
                    seen.add(rect)
                    x, y, w, h = rect
                    pdf_rect = (left + x * sx, top - (y + h) * sy, left + (x + w) * sx, top - y * sy)
                    writer.add_annotation(page_number=i, annotation=Link(rect=pdf_rect, url=url))
                    links += 1
        title = f"{manifest['title']}: {manifest['subtitle']}" if manifest.get("subtitle") else manifest["title"]
        writer.add_metadata(
            {
                "/Title": title,
                "/Author": manifest["speaker"],
                "/Subject": f"Talk slides, {page_url}",
                "/Creator": "CoMPhy Lab talks pipeline (derived from the Keynote export)",
            }
        )
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as f:
            writer.write(f)
    return {"pages": len(manifest["slides"]), "movie_links": links, "fonts_repaired": len(fonts)}


# --------------------------------------------------------------------------
# PowerPoint

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _emu(v: float) -> int:
    return int(round(v * EMU_PER_PX))


def _timing_xml(videos: list[tuple[int, int]]) -> str:
    """Slide timing: every video starts with the slide, muted, and loops until the slide ends.

    ``videos`` holds ``(shape id, duration in ms)``. This is the structure
    PowerPoint writes for "Start: Automatically" and "Loop until Stopped".
    """
    ids = iter(range(1, 10_000))
    root, main, group, inner = next(ids), next(ids), next(ids), next(ids)
    calls = []
    for i, (spid, ms) in enumerate(videos):
        node, beh = next(ids), next(ids)
        kind = "afterEffect" if i == 0 else "withEffect"
        calls.append(
            f'<p:par><p:cTn id="{node}" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" '
            f'nodeType="{kind}"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
            f'<p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr><p:cTn id="{beh}" dur="{ms}" fill="hold"/>'
            f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cBhvr></p:cmd></p:childTnLst></p:cTn></p:par>'
        )
    media = "".join(
        f'<p:video><p:cMediaNode vol="0" mute="1"><p:cTn id="{next(ids)}" repeatCount="indefinite" fill="hold" '
        f'display="0"><p:stCondLst><p:cond delay="indefinite"/></p:stCondLst></p:cTn>'
        f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl></p:cMediaNode></p:video>'
        for spid, _ in videos
    )
    return (
        f'<p:timing xmlns:p="{_P}"><p:tnLst><p:par>'
        f'<p:cTn id="{root}" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
        f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="{main}" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        f'<p:par><p:cTn id="{group}" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
        f'<p:cond evt="onBegin" delay="0"><p:tn val="{main}"/></p:cond></p:stCondLst><p:childTnLst>'
        f'<p:par><p:cTn id="{inner}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>'
        f'{"".join(calls)}</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>'
        f"</p:childTnLst></p:cTn>"
        f'<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
        f'<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>'
        f"</p:seq>{media}</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>"
    )


def _set_timing_and_transition(slide, videos: list[tuple[int, int]], dissolve: bool) -> None:
    from pptx.oxml import parse_xml

    sld = slide._element
    for tag in ("timing", "transition"):
        for old in sld.findall(f"{{{_P}}}{tag}"):
            sld.remove(old)
    # Schema order: cSld, clrMapOvr, transition, timing, extLst.
    ext = sld.find(f"{{{_P}}}extLst")
    new = []
    if dissolve:
        new.append(parse_xml(f'<p:transition xmlns:p="{_P}" spd="fast"><p:fade/></p:transition>'))
    if videos:
        new.append(parse_xml(_timing_xml(videos)))
    for el in new:
        if ext is not None:
            ext.addprevious(el)
        else:
            sld.append(el)


_SRGB: bytes | None = None


def _srgb_png(src: Path, tmp: Path) -> Path:
    """A copy of ``src`` tagged sRGB: the stills are sRGB, as browsers assume, and the tag says so."""
    global _SRGB
    from PIL import Image, ImageCms

    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    out = tmp / f"srgb-{Path(src).name}"
    if not out.exists():
        with Image.open(src) as im:
            im.save(out, format="PNG", icc_profile=_SRGB, compress_level=6)
    return out


def _still(stills_dir: Path, name: str) -> Path:
    path = Path(stills_dir) / (name.rsplit(".", 1)[0] + ".png")
    if not path.is_file():
        raise DownloadError(f"lossless still for {name} not found in {Path(stills_dir).name}/; rebuild with --stills")
    return path


def _youtube_poster(slide: dict, ref: str, rect, stills_dir: Path, tmp: Path) -> Path | None:
    """The poster Keynote shows in the embed box, cropped from a state of the slide without the embed."""
    from PIL import Image

    for state in slide["states"]:
        if any(m["kind"] == "youtube" and m["ref"] == ref for m in state["media"]):
            continue
        src = _still(stills_dir, state.get("image2x") or state["image"])
        with Image.open(src) as im:
            scale = im.width / 1920
            x, y, w, h = (round(v * scale) for v in rect)
            out = tmp / f"yt-{slide['id'][:8]}-{ref}.png"
            im.crop((x, y, x + w, y + h)).save(out, format="PNG")
            return out
    return None


def build_pptx(manifest: dict, media_dir: Path, stills_dir: Path, out: Path, page_url: str) -> dict:
    """One slide per click state, from the build's lossless stills, with videos placed and looping."""
    from PIL import Image
    from pptx import Presentation

    media_dir, stills_dir = Path(media_dir), Path(stills_dir)
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    blank = prs.slide_layouts[6]
    props = prs.core_properties
    props.title = manifest["title"]
    props.subject = manifest.get("subtitle") or ""
    props.author = manifest["speaker"]
    props.comments = f"Web version: {page_url}"
    counts = {"slides": 0, "videos": 0, "youtube_links": 0, "overlays": 0}
    with tempfile.TemporaryDirectory(prefix="talks-pptx-") as tmpdir:
        tmp = Path(tmpdir)
        posters: dict[str, Path] = {}
        for ref, v in manifest["videos"].items():
            png = tmp / f"poster-{ref}.png"
            with Image.open(media_dir / v["poster"]) as im:
                im.convert("RGB").save(png, format="PNG")
            posters[ref] = _srgb_png(png, tmp)
        for slide in manifest["slides"]:
            for k, state in enumerate(slide["states"]):
                s = prs.slides.add_slide(blank)
                still = _srgb_png(_still(stills_dir, state.get("image2x") or state["image"]), tmp)
                pic = s.shapes.add_picture(str(still), 0, 0, SLIDE_W, SLIDE_H)
                pic.name = f"Slide {slide['n']}" + (f", step {k + 1}" if len(slide["states"]) > 1 else "")
                pic._element.nvPicPr.cNvPr.set("descr", slide.get("text") or slide["title"])
                timed = []
                for item in state["media"]:
                    x, y, w, h = item["rect"]
                    if item["kind"] == "video":
                        v = manifest["videos"][item["ref"]]
                        movie = s.shapes.add_movie(
                            str(media_dir / v["src"]), _emu(x), _emu(y), _emu(w), _emu(h),
                            poster_frame_image=str(posters[item["ref"]]), mime_type="video/mp4",
                        )  # fmt: skip
                        timed.append((movie.shape_id, max(1, int(round(v["duration"] * 1000)))))
                        counts["videos"] += 1
                    else:
                        y_entry = manifest["youtube"][item["ref"]]
                        url = f"https://www.youtube.com/watch?v={y_entry['id']}&t={int(y_entry['start'])}s"
                        poster = _youtube_poster(slide, item["ref"], item["rect"], stills_dir, tmp)
                        if poster is not None:
                            poster = _srgb_png(poster, tmp)
                            shape = s.shapes.add_picture(str(poster), _emu(x), _emu(y), _emu(w), _emu(h))
                        else:
                            from pptx.enum.shapes import MSO_SHAPE

                            shape = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, _emu(x), _emu(y), _emu(w), _emu(h))
                            shape.fill.background()
                            shape.line.fill.background()
                        shape.name = f"YouTube: {y_entry['title']}"
                        shape.click_action.hyperlink.address = url
                        counts["youtube_links"] += 1
                if state.get("overlay"):
                    overlay = _srgb_png(_still(stills_dir, state.get("overlay2x") or state["overlay"]), tmp)
                    s.shapes.add_picture(str(overlay), 0, 0, SLIDE_W, SLIDE_H).name = "Labels above the videos"
                    counts["overlays"] += 1
                _set_timing_and_transition(s, timed, state.get("transition") == "dissolve")
                counts["slides"] += 1
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(out))
    return counts


# --------------------------------------------------------------------------
# Keynote (macOS only)


def _osascript(script: str, timeout: int) -> str:
    try:
        r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise DownloadError("osascript not found: the Keynote download needs macOS with Keynote") from exc
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"Keynote did not answer within {timeout} s") from exc
    if r.returncode != 0:
        raise DownloadError(f"Keynote: {r.stderr.strip()}")
    return r.stdout.strip()


def _as_str_plain(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _as_str(path: Path) -> str:
    return _as_str_plain(str(Path(path).resolve()))


def build_key(pptx: Path, out: Path, expected_slides: int, expected_movies: int, timeout: int = 1800) -> dict:
    """Open the PowerPoint file in Keynote, loop and mute every movie, save it as a Keynote document."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    name = Path(pptx).stem
    # Keynote answers "open" only after its import-warnings sheet is dismissed,
    # which needs someone at the screen, so the reply is not awaited: the
    # document is found by name once it exists, and later commands work
    # with the sheet still up.
    script = f"""
set docName to {_as_str_plain(name)}
tell application "Keynote"
  if exists document docName then error "a document named " & docName & " is already open"
  ignoring application responses
    open POSIX file {_as_str(pptx)}
  end ignoring
end tell
set waited to 0
repeat
  delay 2
  set waited to waited + 2
  tell application "Keynote" to set ready to exists document docName
  if ready then exit repeat
  if waited > {timeout} then error "Keynote did not open the file within {timeout} s"
end repeat
with timeout of {timeout} seconds
  tell application "Keynote"
    -- By id: saving renames the document.
    set d to document id (id of document docName)
    set nSlides to count of slides of d
    set nMovies to 0
    repeat with s in slides of d
      repeat with m in movies of s
        set repetition method of m to loop
        set movie volume of m to 0
        set nMovies to nMovies + 1
      end repeat
    end repeat
    -- "export ... as Keynote" does not return for an imported document; save does.
    save d in POSIX file {_as_str(out)}
    close d saving no
    return (nSlides as text) & " " & (nMovies as text)
  end tell
end timeout
"""
    slides, movies = (int(x) for x in _osascript(script, timeout + 60).split())
    if slides != expected_slides or movies != expected_movies:
        raise DownloadError(
            f"Keynote import has {slides} slides and {movies} movies; expected {expected_slides} and {expected_movies}"
        )
    if not out.exists():
        raise DownloadError("Keynote did not write the document")
    return {"slides": slides, "movies": movies}


# --------------------------------------------------------------------------
# Orchestration


def make_downloads(
    talk, root: Path, export_dir: Path | None, formats: list[str] | None = None, log=print
) -> list[dict]:
    """Build the talk's downloads, store them content-addressed and list them in its manifest."""
    from .manifest import dumps, validate

    root = Path(root)
    formats = list(formats if formats is not None else talk.downloads)
    unlisted = [f for f in formats if f not in talk.downloads]
    if unlisted:
        raise DownloadError(f"talk.yaml downloads does not list: {', '.join(unlisted)}")
    manifest_path = root / "site" / talk.slug / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build_dir = root / "build" / talk.slug
    # The downloads must show exactly what the site shows: the manifest the last
    # build wrote (the stills are named by its image hashes) and, for the PDF,
    # the export that build read.
    report_path = build_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    built = {k: v for k, v in manifest.items() if k != "downloads"}
    if not report.get("manifest_sha256") or report["manifest_sha256"] != hashlib.sha256(
        dumps(built).encode("utf-8")
    ).hexdigest():
        raise DownloadError("the site manifest is not the one the last build wrote; run the build first")
    if "pdf" in formats and export_dir is not None and export_digest(export_dir) != report.get("export_digest"):
        raise DownloadError("--export is not the export the last build read")
    media_dir, stills_dir = build_dir / "media", build_dir / "stills"
    page_url = f"https://comphy-lab.org/talks/{talk.slug}/"
    work = build_dir / "downloads"
    work.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}
    report: dict[str, dict] = {}
    if "pdf" in formats:
        if export_dir is None:
            raise DownloadError("the PDF download needs --export")
        derived = work / "derived.pdf"
        derive_pdf(export_dir, derived)
        made["pdf"] = work / file_name(talk.speaker, talk.slug, talk.updated, "pdf")
        report["pdf"] = handout_pdf(derived, manifest, page_url, made["pdf"])
        log(f"pdf: {report['pdf']}")
    if "pptx" in formats or "key" in formats:
        pptx = work / file_name(talk.speaker, talk.slug, talk.updated, "pptx")
        report["pptx"] = build_pptx(manifest, media_dir, stills_dir, pptx, page_url)
        log(f"pptx: {report['pptx']}")
        if "pptx" in formats:
            made["pptx"] = pptx
        if "key" in formats:
            made["key"] = work / file_name(talk.speaker, talk.slug, talk.updated, "key")
            report["key"] = build_key(pptx, made["key"], report["pptx"]["slides"], report["pptx"]["videos"])
            log(f"key: {report['key']}")
    for fmt in formats:
        size = made[fmt].stat().st_size
        if size > MAX_BYTES:
            raise DownloadError(f"the {fmt} download is {size} bytes, over the {MAX_BYTES} byte upload limit")
    new: dict[str, dict] = {}
    for fmt in formats:
        src = made[fmt]
        digest = sha16_file(src)
        dest = media_dir / digest / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(src, dest)
        new[fmt] = {"format": fmt, "file": f"{digest}/{src.name}", "bytes": dest.stat().st_size}
    # Formats not made this time keep their entries (made from this same build,
    # as the manifest check above shows), all in talk.yaml order.
    previous = {d["format"]: d for d in manifest.get("downloads", []) if isinstance(d, dict) and "format" in d}
    entries = [new.get(f) or previous[f] for f in talk.downloads if f in new or f in previous]
    manifest = copy.deepcopy(manifest)
    manifest.pop("downloads", None)
    if entries:
        manifest["downloads"] = entries
    problems = validate(manifest, media_dir)
    if problems:
        raise DownloadError("manifest invalid:\n  " + "\n  ".join(problems))
    manifest_path.write_text(dumps(manifest), encoding="utf-8")
    (work / "report.json").write_text(json.dumps({"downloads": entries, **report}, indent=2) + "\n", encoding="utf-8")
    return entries
