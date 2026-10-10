"""Screenshot every click state of a built talk, and render the flat Keynote PDF, for review.

Writes ``<out>/<engine>/<n>-<k>.png`` (slide ``n``, state ``k`` counted from 1
as in deep links; the stage at 1920 x 1080 in full screen, videos playing) and,
with ``--pdf``, ``<out>/keynote/<n>.png`` (one page per slide, 1920 px wide,
rendered with poppler's ``pdftoppm``). ``<out>/compare.json`` lists, per slide,
the state closest to the Keynote page and their mean absolute difference,
over the whole slide and outside the media boxes. The flat PDF shows every
object of a slide at once, whatever its builds do, so a slide with build-outs
differs from all its states; movie frames and the YouTube embed differ by
design.

Usage::

    uv run python screenshots.py --out <dir> [--pdf <flat Keynote PDF>] [--live-youtube]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import Route, sync_playwright

import serve
from talk_helpers import INIT_JS, Talk, TalkPage, wait_until

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STUBS = HERE / "stubs"


def render_keynote(pdf: Path, out: Path, count: int) -> list[Path]:
    if shutil.which("pdftoppm") is None:
        raise SystemExit("pdftoppm (poppler) is needed for --pdf")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-scale-to-x", "1920", "-scale-to-y", "-1", str(pdf), str(prefix)], check=True
        )
        pages = sorted(Path(tmp).glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        if len(pages) != count:
            print(f"warning: the PDF has {len(pages)} pages, the talk {count} slides", file=sys.stderr)
        written = []
        for i, page in enumerate(pages, start=1):
            target = out / f"{i}.png"
            shutil.move(str(page), target)
            written.append(target)
    return written


def shoot(talk: Talk, site: Path, out: Path, engine: str, live_youtube: bool) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    srv = serve.make_server(site, {talk.slug: talk.media_dir})
    serve.start_in_thread(srv)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    notes: list[str] = []
    try:
        with sync_playwright() as p:
            browser = getattr(p, engine).launch()
            ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
            ctx.add_init_script(INIT_JS)
            page = ctx.new_page()
            tp = TalkPage(page=page, base=base, talk=talk, engine=engine)
            ctx.expose_binding("__reportCsp", lambda source, v: tp.csp_events.append(v))

            def handle(route: Route) -> None:
                parts = urllib.parse.urlsplit(route.request.url)
                if parts.hostname == "www.youtube.com" and parts.path == "/iframe_api":
                    route.fulfill(path=STUBS / "youtube_iframe_api.js", content_type="text/javascript")
                elif parts.hostname == "www.youtube-nocookie.com" and parts.path.startswith("/embed/"):
                    route.fulfill(path=STUBS / "youtube_embed.html", content_type="text/html")
                else:
                    tp.external.append(route.request.url)
                    route.abort()

            if live_youtube:
                ctx.route(lambda u: urllib.parse.urlsplit(u).hostname == "media.comphy-lab.org", handle)
            else:
                ctx.route(lambda u: not u.startswith(base + "/") and not u.startswith("data:"), handle)
            page.on("console", lambda m: tp.console.append((m.type, m.text, (m.location or {}).get("url", ""))))
            page.on("pageerror", lambda e: tp.page_errors.append(str(e)))

            tp.goto()
            tp.enter_fullscreen()
            for step in talk.steps:
                if step.index > 0:
                    tp.press("ArrowRight")
                tp.wait_state(step.n, step.k)
                tp.wait_stills(step)
                if step.video_keys:
                    tp.wait_videos_playing(min_time=0.6, timeout=15)
                if any(k.startswith("youtube:") for k in step.keys):
                    wait_until(
                        page, "() => document.querySelector('.media--youtube > iframe, .yt-fallback')", timeout=20
                    )
                    if live_youtube:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            frame = next((f for f in page.frames if "youtube-nocookie.com/embed/" in f.url), None)
                            try:
                                t = (
                                    frame.evaluate(
                                        "() => { const v = document.querySelector('video');"
                                        " return v ? v.currentTime : 0; }"
                                    )
                                    if frame
                                    else 0
                                )
                            except Exception:  # noqa: BLE001 - frame may be navigating
                                t = 0
                            if t > 8:
                                break
                            page.wait_for_timeout(500)
                        notes.append(f"{step.n}.{step.k + 1}: live YouTube at t={t:.1f} s")
                    page.wait_for_timeout(1500)
                x, y, w, h = tp.stage_rect()
                tp.screenshot(
                    path=str(out / f"{step.n}-{step.k + 1}.png"), clip={"x": x, "y": y, "width": w, "height": h}
                )
            problems = tp.problems()
            if live_youtube:
                problems = [q for q in problems if "youtube" not in q.lower() and "ytimg" not in q.lower()]
            notes += problems
            browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    return notes


def compare(talk: Talk, shots: Path, keynote: Path) -> list[dict]:
    """Per slide, the state closest to the Keynote page (a flat PDF shows one stage of the builds)."""
    rows = []
    for slide in talk.manifest["slides"]:
        n = slide["n"]
        page = keynote / f"{n}.png"
        if not page.is_file():
            continue
        best = None
        for k, state in enumerate(slide["states"], start=1):
            shot = shots / f"{n}-{k}.png"
            if not shot.is_file():
                continue
            with Image.open(shot) as ia, Image.open(page) as ib:
                x = np.asarray(ia.convert("RGB"), dtype=np.int16)
                y = np.asarray(ib.convert("RGB").resize(ia.size), dtype=np.int16)
            diff = np.abs(x - y).max(axis=2)
            outside = np.ones(diff.shape, dtype=bool)
            for m in state["media"]:
                mx, my, mw, mh = (round(v) for v in m["rect"])
                outside[max(0, my - 2) : max(0, my + mh + 2), max(0, mx - 2) : max(0, mx + mw + 2)] = False
            row = {"slide": n, "state": k, "states": len(slide["states"]),
                   "mean_abs_diff": round(float(diff.mean()), 2), "share_over_48": round(float((diff > 48).mean()), 4),
                   "mean_abs_diff_outside_media": round(float(diff[outside].mean()), 2),
                   "media": [m["kind"] + ":" + m["ref"] for m in state["media"]]}  # fmt: skip
            if best is None or row["mean_abs_diff_outside_media"] < best["mean_abs_diff_outside_media"]:
                best = row
        if best:
            rows.append(best)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pdf", type=Path, help="flat Keynote PDF export of the same deck")
    ap.add_argument("--slug", default="singular-bursting-bubbles")
    ap.add_argument("--site", type=Path, default=REPO / "site")
    ap.add_argument("--build", type=Path, default=REPO / "build")
    ap.add_argument("--engine", choices=["chromium", "webkit"], default="chromium")
    ap.add_argument("--live-youtube", action="store_true", help="load the real YouTube embed (network)")
    args = ap.parse_args()
    talk = Talk.load(args.site.resolve(), (args.build / args.slug / "media").resolve(), args.slug)
    shots = args.out / args.engine
    notes = shoot(talk, args.site.resolve(), shots, args.engine, args.live_youtube)
    print(f"{len(talk.steps)} states -> {shots}")
    for note in notes:
        print("note:", note)
    if args.pdf:
        pages = render_keynote(args.pdf, args.out / "keynote", len(talk.manifest["slides"]))
        print(f"{len(pages)} Keynote pages -> {args.out / 'keynote'}")
        rows = compare(talk, shots, args.out / "keynote")
        (args.out / "compare.json").write_text(json.dumps(rows, indent=1) + "\n")
        worst = sorted(rows, key=lambda r: r["mean_abs_diff_outside_media"], reverse=True)[:12]
        print("largest differences (closest state vs Keynote page; mean over the slide and outside media):")
        for r in worst:
            print(
                f"  slide {r['slide']:3d} state {r['state']}/{r['states']}: mean {r['mean_abs_diff']:6.2f},"
                f" outside media {r['mean_abs_diff_outside_media']:6.2f}, >48 on {r['share_over_48']:.1%}  {r['media']}"
            )


if __name__ == "__main__":
    main()
