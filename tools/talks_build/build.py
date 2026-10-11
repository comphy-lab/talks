"""``build``: Keynote HTML export + talk.yaml -> media, manifest, talk page."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import patches as patchmod
from . import site as sitemod
from .geometry import Rect, scaled_video_size
from .keynote import Deck, MovieUse, Slide, State, leaves_of, load_deck, movie_leaf_orders
from .manifest import MEDIA_NAME_RE, dumps, validate
from .media import MediaStore
from .raster import Compositor, PageCache, image_diff
from .talkfile import Talk, load_talk
from .text import (
    attach_marks,
    blocks_text,
    clean_text,
    drop_invisible,
    pdf_pages_blocks,
    pdf_pages_runs,
    replace_spaced,
    slide_title,
)
from .tounicode import repaired_copy
from .video import EncodeCache, first_frame, probe, stream_hash
from .youtube import choose_rect, fix_contrast, movie_object_ids, obstacles, poster_in_rect

OVERLAY_ALPHA_MIN = 8
# Canvas pixels by which an overlay continues past a movie's box. When the
# browser scales the stage it blends the overlay's edge with what lies just
# outside it; without this band a mask that ends exactly on the box edge lets
# the movie's edge pixels show through as a hairline. The band repeats the
# state's own still, which is what lies beneath it, so at 1:1 it changes
# nothing. Browsers downscale through mipmaps, so the blend reaches several
# canvas pixels on a phone (8 measured in Chromium at 412 x 915, 2.625 dppx);
# sixteen leave a margin.
OVERLAY_BLEED = 16
RENDER_SCALES = (1, 2)


class BuildError(RuntimeError):
    pass


@dataclass
class VideoGroup:
    digest: str
    ref: str
    files: list[Path] = field(default_factory=list)
    boxes: list[tuple[float, float]] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    entry: dict | None = None


def infer_root(talk_path: Path) -> Path:
    talk_path = Path(talk_path).resolve()
    if talk_path.parent.parent.name == "talks-src":
        return talk_path.parent.parent.parent
    raise BuildError("cannot infer the repository root from the talk path; pass --out-root")


class Builder:
    def __init__(self, export_dir: Path, talk_path: Path, out_root: Path | None, pdf: Path | None, jobs: int, log):
        self.log = log
        self.talk: Talk = load_talk(talk_path)
        self.root = Path(out_root).resolve() if out_root else infer_root(talk_path)
        if Path(talk_path).resolve().parent.name != self.talk.slug:
            log(f"warning: talk.yaml folder name differs from slug {self.talk.slug!r}")
        self.export_dir = Path(export_dir)
        self.pdf = Path(pdf) if pdf else self._sibling_pdf()
        self.jobs = max(1, jobs)
        self.build_dir = self.root / "build" / self.talk.slug
        self.site_dir = self.root / "site"
        self.store = MediaStore(self.build_dir / "media")
        self.cache_dir = self.build_dir / "cache"
        self.pages = PageCache(self.cache_dir / "pages")
        self.encodes = EncodeCache(self.cache_dir / "video")
        self.report: dict = {"slug": self.talk.slug, "warnings": []}
        self.pool = ThreadPoolExecutor(max_workers=self.jobs)
        self.previews: set[int] = set()
        self.prune = False

    def _sibling_pdf(self) -> Path | None:
        candidate = self.export_dir.with_name(self.export_dir.name + ".pdf")
        return candidate if candidate.is_file() else None

    def warn(self, message: str) -> None:
        self.report["warnings"].append(message)
        self.log(f"warning: {message}")

    # ------------------------------------------------------------------
    def run(self) -> dict:
        t0 = time.time()
        deck = load_deck(self.export_dir)
        if (deck.width, deck.height) != (1920, 1080):
            raise BuildError(f"canvas is {deck.width} x {deck.height}; v1 requires 1920 x 1080")
        self.deck = deck
        self.log(f"export: {len(deck.slides)} slides, {len(deck.states)} click states")
        if not self.talk.loop:
            self.warn("video.loop is false, but the v1 player loops every video")
        self._check_talk_refs(deck)

        self.yt_objects: dict[str, set[str]] = {}
        for y in self.talk.youtube:
            slide = self.slides_by_id[y.slide]
            self.yt_objects.setdefault(slide.id, set()).update(movie_object_ids(slide, y.movie))

        videos = self._videos(deck)
        youtube, yt_rects = self._youtube(deck)
        flat_text = self.flat_blocks = None
        if self.pdf:
            flat_text, self.flat_blocks = self._flat_pdf_text(self.pdf)
        if flat_text is not None and len(flat_text) != len(deck.slides):
            self.warn(
                f"flat PDF has {len(flat_text)} pages for {len(deck.slides)} slides; using per-slide textures for text"
            )
            flat_text = self.flat_blocks = None
        if self.pdf is None:
            self.warn("no flat PDF export found; slide text comes from the per-slide textures")

        slides_out = []
        for slide in deck.slides:
            slides_out.append(self._slide(slide, videos, youtube, yt_rects, flat_text))
            self.pages.forget()

        manifest = OrderedDict(
            schema=2,
            slug=self.talk.slug,
            title=self.talk.title,
            subtitle=self.talk.subtitle,
            speaker=self.talk.speaker,
            updated=self.talk.updated,
            canvas={"width": deck.width, "height": deck.height},
            media_base=self.talk.media_base,
            videos={g.ref: g.entry for g in videos.values()},
            youtube={ref: entry for ref, entry in youtube.items()},
            slides=slides_out,
            changelog=self.talk.changelog,
        )
        problems = validate(manifest, self.store.dir)
        if problems:
            raise BuildError("manifest invalid:\n  " + "\n  ".join(problems))
        out = self.site_dir / self.talk.slug / "manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dumps(manifest), encoding="utf-8")
        page = sitemod.write_page(self.site_dir, self.talk)
        if page is None:
            self.warn("site/player/talk.template.html not found; index.html not written")
        sitemod.update_index(self.site_dir, self.talk)

        # Only content-addressed files this pipeline names are ever pruned.
        stale = [p for p in self.store.stale() if MEDIA_NAME_RE.match(p.name)]
        if stale and self.prune:
            for path in stale:
                path.unlink()
            self.log(f"pruned {len(stale)} superseded files from build media")
        elif stale:
            self.warn(
                f"{len(stale)} files in build media are not used by this manifest "
                "(upload skips them; --prune removes them)"
            )
        sizes = {"webp": 0, "mp4": 0}
        for name in self.store.written:
            sizes[name.rsplit(".", 1)[1]] += (self.store.dir / name).stat().st_size
        self.report.update(
            slides=len(slides_out),
            states=sum(len(s["states"]) for s in slides_out),
            videos=len(videos),
            youtube=len(youtube),
            overlays=sum(1 for s in slides_out for st in s["states"] if st["overlay"]),
            media_files=len(self.store.written),
            media_bytes=sizes,
            render_scales=list(RENDER_SCALES),
            seconds=round(time.time() - t0, 1),
            manifest=str(out.relative_to(self.root)),
        )
        report_path = self.build_dir / "report.json"
        report_path.write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.pool.shutdown()
        return self.report

    # ------------------------------------------------------------------
    def _flat_pdf_text(self, pdf: Path) -> tuple[list[str], list]:
        """Per page: text and ``pdftotext`` blocks of the flat PDF, as a viewer sees them.

        Text comes from a copy whose wrong ToUnicode maps are rebuilt from the
        fonts' own glyph names (see ``tounicode``), and words painted at zero
        opacity (later Beamer overlays kept in place) are left out.
        """
        text_pdf, repairs = repaired_copy(pdf, self.cache_dir / "text")
        if repairs:
            wrong = sum(r["wrong"] for r in repairs)
            self.log(f"text: rebuilt ToUnicode of {len(repairs)} fonts ({wrong} glyphs mapped wrongly by the export)")
        unknown = sorted({g for r in repairs for g in r["unknown_glyphs"]})
        if unknown:
            self.warn(f"text: glyph names with no known Unicode, left out of slide text: {', '.join(unknown)}")
        self.report["tounicode_repairs"] = {
            "fonts": len(repairs),
            "glyphs": sum(r["wrong"] for r in repairs),
            "examples": sorted({e for r in repairs for e in r["examples"]})[:40],
        }
        pages = pdf_pages_blocks(text_pdf)
        runs = pdf_pages_runs(text_pdf)
        hidden = 0
        if len(runs) == len(pages):
            filtered = []
            for blocks, page_runs in zip(pages, runs, strict=True):
                kept, dropped = drop_invisible(blocks, page_runs)
                filtered.append(kept)
                hidden += dropped
            pages = filtered
        else:
            self.warn("text: pdftohtml and pdftotext disagree on the page count; transparent text kept")
        pages = [attach_marks(blocks) for blocks in pages]
        self.report["transparent_words_dropped"] = hidden
        return [blocks_text(b) for b in pages], pages

    def _check_talk_refs(self, deck: Deck) -> None:
        self.slides_by_id = {s.id: s for s in deck.slides}
        for y in self.talk.youtube:
            slide = self.slides_by_id.get(y.slide)
            if slide is None:
                raise BuildError(f"youtube: slide {y.slide} is not in the export")
            if not movie_object_ids(slide, y.movie):
                raise BuildError(f"youtube: no movie starting with {y.movie!r} on slide {slide.n}")
        for p in self.talk.patches:
            if p.slide not in self.slides_by_id:
                raise BuildError(f"patch: slide {p.slide} is not in the export")
        for sid in self.talk.titles:
            if sid not in self.slides_by_id:
                raise BuildError(f"titles: slide {sid} is not in the export")

    # ------------------------------------------------------------------
    def _videos(self, deck: Deck) -> dict[str, VideoGroup]:
        uses: list[tuple[State, MovieUse]] = []
        for state in deck.states:
            for m in state.movies:
                if m.object_id in self.yt_objects.get(state.slide.id, set()):
                    continue
                uses.append((state, m))
        files = sorted({m.file for _, m in uses}, key=str)
        for f in files:
            if not f.is_file():
                raise BuildError(f"movie file missing from export: {f.name}")
        digests = dict(zip(files, self.pool.map(stream_hash, files), strict=True))
        groups: dict[str, VideoGroup] = OrderedDict()
        self.file_digest = digests
        for _state, m in uses:
            d = digests[m.file]
            g = groups.get(d)
            if g is None:
                g = groups[d] = VideoGroup(digest=d, ref=f"v{len(groups) + 1:02d}")
            if m.file not in g.files:
                g.files.append(m.file)
            g.boxes.append((m.rect.w, m.rect.h))
            g.names.add(m.asset_id)

        def encode_group(g: VideoGroup):
            src = g.files[0]
            p = probe(src)
            w, h = scaled_video_size(p.width, p.height, g.boxes, self.talk.max_scale)
            mp4 = self.encodes.get(src, g.digest, w, h)
            q = probe(mp4)
            if q.codec != "h264" or (q.width, q.height) != (w, h):
                raise BuildError(f"encode of {src.name} produced {q.codec} {q.width}x{q.height}")
            return p, w, h, mp4, q

        results = list(self.pool.map(encode_group, groups.values()))
        vids = []
        for g, (p, w, h, mp4, q) in zip(groups.values(), results, strict=True):
            src_name = self.store.put_file(mp4, "mp4")
            poster = self.store.put_webp(first_frame(mp4))
            g.entry = OrderedDict(
                src=src_name, poster=poster, width=w, height=h, duration=round(q.duration, 3), credit=None
            )
            vids.append(
                {
                    "ref": g.ref,
                    "source": sorted(g.names)[0],
                    "copies": len(g.files),
                    "placements": len(g.boxes),
                    "source_codec": p.codec,
                    "source_size": [p.width, p.height],
                    "largest_box": [round(max(b[0] for b in g.boxes), 1), round(max(b[1] for b in g.boxes), 1)],
                    "encoded_size": [w, h],
                    "bytes": (self.store.dir / src_name).stat().st_size,
                    "src": src_name,
                }
            )
            self.log(f"video {g.ref}: {sorted(g.names)[0]} {p.codec} {p.width}x{p.height} -> h264 {w}x{h}")
        self.report["videos_detail"] = vids
        return groups

    # ------------------------------------------------------------------
    def _youtube(self, deck: Deck) -> tuple[dict, dict[str, tuple[int, int, int, int]]]:
        entries: dict[str, dict] = OrderedDict()
        rects: dict[str, tuple[int, int, int, int]] = {}
        detail = []
        self.yt_ref: dict[str, str] = {}
        self.yt_spec = {y.slide: y for y in self.talk.youtube}
        for i, y in enumerate(self.talk.youtube, 1):
            ref = f"y{i:02d}"
            slide = self.slides_by_id[y.slide]
            excluded = self.yt_objects[slide.id]
            blockers = obstacles(slide, excluded, deck.width, deck.height)
            movie_rects = [m.rect for m in slide.movies() if m.object_id in excluded]
            if y.rect is not None:
                rect = y.rect
                r = Rect(*rect)
                hits = [b for b in blockers if r.intersects(b)]
                if hits:
                    self.warn(f"youtube {ref}: talk.yaml rect overlaps {len(hits)} slide objects")
                source = "talk.yaml"
            else:
                rect = choose_rect(blockers, deck.width, deck.height, prefer=movie_rects[0] if movie_rects else None)
                source = "computed"
            rects[slide.id] = rect
            self.yt_ref[slide.id] = ref
            entries[ref] = OrderedDict(id=y.id, start=y.start, end=y.end, title=y.title, credit=y.credit)
            detail.append({"ref": ref, "slide": slide.n, "rect": list(rect), "rect_source": source})
            self.log(f"youtube {ref}: slide {slide.n} embed rect {list(rect)} ({source})")
        self.report["youtube_detail"] = detail
        return entries, rects

    # ------------------------------------------------------------------
    def _slide(self, slide: Slide, videos, youtube, yt_rects, flat_text) -> dict:
        comp = Compositor(slide, self.pages, self.deck.width, self.deck.height)
        excluded = self.yt_objects.get(slide.id, set())
        specs = [p for p in self.talk.patches if p.slide == slide.id]
        applied = []
        # Contrast first: a patch takes its ink colour from the text it replaces,
        # so it must see the recoloured text, not the original white-on-movie ink.
        if excluded:
            fixed = fix_contrast(slide, comp, excluded, self.deck.width, self.deck.height)
            for f in fixed:
                self.log(f"slide {slide.n}: recoloured {f['ink']} text over {f['background']} "
                         f"(contrast {f['contrast_before']}) to {f['recoloured_to']}")  # fmt: skip
            self.report.setdefault("contrast_fixes", []).extend(fixed)
        if specs:
            applied = patchmod.plan_slide(slide, specs, comp, self.deck.width, self.deck.height)
            self.report.setdefault("patches", []).extend(a.describe() | {"slide_n": slide.n} for a in applied)
            for a in applied:
                self.log(f"patch slide {slide.n}: {a.spec.find!r} -> {a.lines} ({a.layout}, {a.mode})")
        force_composite = bool(excluded or applied)
        self._verify_final(slide, comp)

        # Keep the 1x render exactly as before. The second compositor renders
        # the original PDF pages at 144 dpi, using the same logical geometry.
        # Patch layout is shared, but glyphs are drawn anew at that density.
        comps = {1: comp}
        for scale in RENDER_SCALES[1:]:
            hi = Compositor(slide, self.pages, self.deck.width, self.deck.height, scale)
            if excluded:
                fix_contrast(slide, hi, excluded, self.deck.width, self.deck.height)
            if applied:
                patchmod.install(hi, applied)
            comps[scale] = hi

        yt_spec = self.yt_spec.get(slide.id)
        jobs = []
        for state in slide.states:
            # A replaced movie shown before the click that starts it (Keynote
            # shows its poster there): draw the poster into the embed box, or
            # mount the embed already, as talk.yaml says.
            embed = any(m.object_id in excluded for m in state.movies)
            if yt_spec is not None and not embed and self._shows_movie(state, excluded):
                if yt_spec.before_start == "embed":
                    embed = True
            boxes = [Rect(*m.rect.snapped()) for m in state.movies if m.object_id not in excluded]
            if embed:
                boxes.append(Rect(*yt_rects[slide.id]))
            density_jobs = {}
            for scale, compositor in comps.items():
                image = self._state_image(state, compositor, excluded, force_composite)
                if yt_spec is not None and not embed and self._shows_movie(state, excluded):
                    poster = poster_in_rect(compositor, state.leaves(), excluded, yt_rects[slide.id])
                    if poster is not None:
                        image = image.convert("RGBA")
                        image.alpha_composite(poster)
                        if scale == 1:
                            self.report.setdefault("youtube_before_start", []).append(
                                {"slide": slide.n, "state": state.index + 1, "shown": "poster"}
                            )
                overlay = self._overlay(state, compositor, excluded, image, boxes)
                density_jobs[scale] = (
                    self.pool.submit(self.store.put_webp, image),
                    self.pool.submit(self.store.put_webp, overlay, alpha=True) if overlay else None,
                )
                if slide.n in self.previews:
                    self._save_preview(
                        slide, state, image, overlay, yt_rects.get(slide.id) if embed else None, excluded, scale
                    )
            jobs.append((state, embed, density_jobs))
        states_out = []
        for state, embed, density_jobs in jobs:
            media = []
            if embed and not any(m.object_id in excluded for m in state.movies):
                media.append(OrderedDict(kind="youtube", ref=self.yt_ref[slide.id], rect=list(yt_rects[slide.id])))
            for m in state.movies:
                if m.object_id in excluded:
                    media.append(OrderedDict(kind="youtube", ref=self.yt_ref[slide.id], rect=list(yt_rects[slide.id])))
                else:
                    ref = videos[self.file_digest[m.file]].ref
                    media.append(OrderedDict(kind="video", ref=ref, rect=list(m.rect.snapped())))
            img_f, ovl_f = density_jobs[1]
            hi_img_f, hi_ovl_f = density_jobs[2]
            states_out.append(
                OrderedDict(
                    image=img_f.result(),
                    overlay=ovl_f.result() if ovl_f else None,
                    image2x=hi_img_f.result(),
                    overlay2x=hi_ovl_f.result() if hi_ovl_f else None,
                    transition=state.transition,
                    media=media,
                )
            )
        raw = flat_text[slide.n - 1] if flat_text else self._texture_text(slide)
        blocks = self.flat_blocks[slide.n - 1] if self.flat_blocks else None
        # The corrections drawn on the slide apply to its text as well.
        for spec in specs:
            raw = replace_spaced(raw, spec.find, spec.replace)
        title = slide_title(raw, blocks) or self._accessibility_title(slide) or f"Slide {slide.n}"
        for spec in specs:
            title = replace_spaced(title, spec.find, spec.replace)
        if slide.id in self.talk.titles:
            title = self.talk.titles[slide.id]
        return OrderedDict(id=slide.id, n=slide.n, title=title, text=clean_text(raw), states=states_out)

    def _save_preview(self, slide, state, image, overlay, yt_rect, excluded, scale=1) -> None:
        """PNG previews: image, overlay, and a layout view with media boxes."""
        from PIL import ImageDraw

        d = self.build_dir / "preview"
        d.mkdir(parents=True, exist_ok=True)
        stem = f"s{slide.n:03d}-{state.index + 1}"
        if scale != 1:
            stem += f"-{scale}x"
        image.convert("RGB").save(d / f"{stem}-image.png")
        if overlay is not None:
            overlay.save(d / f"{stem}-overlay.png")
        layout = image.convert("RGBA")
        draw = ImageDraw.Draw(layout)
        if yt_rect is not None:
            x, y, w, h = (v * scale for v in yt_rect)
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(255, 0, 0, 255))
        for m in state.movies:
            if m.object_id not in excluded:
                x, y, w, h = (v * scale for v in m.rect.snapped())
                draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(0, 160, 255, 255))
        if overlay is not None:
            layout.alpha_composite(overlay)
        layout.convert("RGB").save(d / f"{stem}-layout.png")

    @staticmethod
    def _shows_movie(state: State, objects: set[str]) -> bool:
        """Whether one of ``objects`` (movies) is drawn in this state's per-object tree."""
        return state.tree is not None and any(lf.visible and lf.object_id in objects for lf in state.leaves())

    def _texture_text(self, slide: Slide) -> str:
        from .text import page_lines

        leaf = [lf for lf in leaves_of(slide.final_event.base) if lf.visible][0]
        lines = page_lines(slide.texture_pdf(leaf.texture), slide.texture_page(leaf.texture))
        return "\n".join(line.text for line in lines)

    def _accessibility_title(self, slide: Slide) -> str | None:
        for item in slide.final_event.accessibility:
            text = clean_text(str(item.get("text", "")))
            if sum(ch.isalpha() for ch in text) >= 3:
                return text
        return None

    def _verify_final(self, slide: Slide, comp: Compositor) -> None:
        """Check the replayed per-object final state against Keynote's flattened one."""
        final = slide.states[-1]
        if not final.is_final or final.tree is None or final.tree is slide.final_event.base:
            return
        # Compare without patches so the check is about the state model only.
        saved = (comp.overrides, comp.after, comp.on_top)
        comp.overrides, comp.after, comp.on_top = {}, {}, {}
        try:
            a = comp.composite(final.leaves())
            b = comp.flattened(slide.final_event.base)
        finally:
            comp.overrides, comp.after, comp.on_top = saved
        stats = image_diff(a, b)
        self.report.setdefault("final_state_check", []).append(
            {"slide": slide.n, **{k: round(v, 4) for k, v in stats.items()}}
        )
        if stats["mean"] > 2.0:
            self.warn(f"slide {slide.n}: replayed final state differs from Keynote's (mean diff {stats['mean']:.2f})")

    def _state_image(self, state: State, comp: Compositor, excluded: set[str], force: bool) -> Image.Image:
        if state.tree is None or (state.is_final and not force):
            return comp.flattened(state.slide.final_event.base)
        return comp.composite(state.leaves(), include=lambda lf: lf.object_id not in excluded)

    def _overlay(
        self, state: State, comp: Compositor, excluded: set[str], image: Image.Image, boxes: list[Rect]
    ) -> Image.Image | None:
        """Layers above a playing movie, on transparency, plus a bleed band.

        ``boxes`` are every media rectangle of the state (videos and YouTube);
        the bleed band (see ``OVERLAY_BLEED``) never covers any of them.
        """
        movies = [m for m in state.movies if m.object_id not in excluded]
        if not movies or state.tree is None:
            return None
        leaves = state.leaves()
        width, height = self.deck.width, self.deck.height
        scale = comp.scale
        overlay = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        canvas = Rect(0, 0, width, height)
        sprites = [s for lf in leaves if lf.visible for s in comp.on_top.get(lf.texture, [])]
        drawn: list[tuple[int, int, int, int]] = []
        for m in movies:  # bottom to top: a higher movie's region wins
            span = movie_leaf_orders(state.tree, m.object_id)
            if span is None:
                continue
            hi = span[1]
            box = Rect(*m.rect.snapped()).intersection(canvas)
            if box is None:
                continue
            above = [lf for lf in leaves if lf.visible and lf.order > hi and lf.object_id not in excluded]
            hits = any(lf.rect.intersects(box) for lf in above) or any(
                Rect(s.x, s.y, s.image.width / scale, s.image.height / scale).intersects(box) for s in sprites
            )
            if not hits:
                continue
            layer = comp.composite(
                leaves, background=None, include=lambda lf, h=hi: lf.order > h and lf.object_id not in excluded
            )
            x1, y1, x2, y2 = (int(v) * scale for v in (box.x, box.y, box.x2, box.y2))
            overlay.paste(layer.crop((x1, y1, x2, y2)), (x1, y1))
            drawn.append((x1, y1, x2, y2))
        if not drawn:
            return None
        if int(np.asarray(overlay.getchannel("A")).max()) < OVERLAY_ALPHA_MIN:
            return None
        rendered_boxes = [Rect(*(v * scale for v in (b.x, b.y, b.w, b.h))) for b in boxes]
        return bleed_overlay(overlay, image, drawn, rendered_boxes, OVERLAY_BLEED * scale)


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary dilation with a (2r+1)^2 square (separable, no SciPy)."""
    out = mask.copy()
    for axis in (0, 1):
        grown = out.copy()
        n = out.shape[axis]
        for d in range(1, r + 1):
            if d >= n:
                break
            if axis == 0:
                grown[d:] |= out[:-d]
                grown[:-d] |= out[d:]
            else:
                grown[:, d:] |= out[:, :-d]
                grown[:, :-d] |= out[:, d:]
        out = grown
    return out


def bleed_overlay(
    overlay: Image.Image,
    still: Image.Image,
    drawn: list[tuple[int, int, int, int]],
    boxes: list[Rect],
    bleed: int,
) -> Image.Image:
    """Continue ``overlay`` up to ``bleed`` px past each movie box it was cropped to.

    Wherever the overlay has ink within ``bleed`` px inside a box edge, the
    band just outside that edge is filled, opaque, with ``still`` (the state's
    own still, which is what lies beneath). No media box is ever covered.
    """
    if bleed <= 0:
        return overlay
    width, height = overlay.size
    ov = np.array(overlay.convert("RGBA"))
    st = np.asarray(still.convert("RGBA"))
    media = np.zeros((height, width), dtype=bool)
    for b in boxes:
        x1, y1 = max(0, int(b.x)), max(0, int(b.y))
        x2, y2 = min(width, int(b.x2)), min(height, int(b.y2))
        if x2 > x1 and y2 > y1:
            media[y1:y2, x1:x2] = True
    band = np.zeros((height, width), dtype=bool)
    for x1, y1, x2, y2 in drawn:
        # Work in a window around the box; the band lies within it.
        wx1, wy1 = max(0, x1 - bleed), max(0, y1 - bleed)
        wx2, wy2 = min(width, x2 + bleed), min(height, y2 + bleed)
        ink = np.zeros((wy2 - wy1, wx2 - wx1), dtype=bool)
        ink[y1 - wy1 : y2 - wy1, x1 - wx1 : x2 - wx1] = ov[y1:y2, x1:x2, 3] > 0
        band[wy1:wy2, wx1:wx2] |= _dilate(ink, bleed)
    band &= ~media
    if not band.any():
        return overlay
    ov[band, :3] = st[band, :3]
    ov[band, 3] = 255
    return Image.fromarray(ov, "RGBA")


def build(
    export_dir: Path,
    talk_path: Path,
    out_root: Path | None = None,
    *,
    pdf: Path | None = None,
    jobs: int = 4,
    previews: set[int] | None = None,
    prune: bool = False,
    log=print,
) -> dict:
    b = Builder(export_dir, talk_path, out_root, pdf, jobs, log)
    b.previews = set(previews or ())
    b.prune = prune
    return b.run()


__all__ = ["build", "bleed_overlay", "BuildError", "infer_root"]
