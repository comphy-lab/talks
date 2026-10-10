"""Local server that mimics the production layout of the talks site.

- ``/talks/...``         -> ``site/``                (GitHub Pages: comphy-lab.org/talks/)
- ``/media/<slug>/...``  -> ``build/<slug>/media/``  (R2: media.comphy-lab.org/talks/<slug>/)

It answers single byte-range requests (Safari and WebKit need ``206`` replies
to play MP4) and serves the MIME types the site relies on. Open a talk with
``/talks/<slug>/?media=/media/<slug>/`` so the player's localhost-only test
hook reads stills and videos from the local build.

Usage::

    uv run python serve.py --slug singular-bursting-bubbles [--port 8000]
"""

from __future__ import annotations

import argparse
import posixpath
import re
import threading
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".webp": "image/webp",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


class TalksHandler(SimpleHTTPRequestHandler):
    """Static handler with two mount points and single-range support."""

    site_dir: Path
    media_dirs: dict[str, Path]
    request_log: list[str] | None = None

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass

    def guess_type(self, path) -> str:
        return MIME.get(Path(str(path)).suffix.lower(), "application/octet-stream")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        """Map ``/talks/`` to the site and ``/media/<slug>/`` to that build's media.

        Anything else maps to a path that does not exist, so it returns 404.
        """
        path = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
        clean = posixpath.normpath(path)
        if path.endswith("/") and not clean.endswith("/"):
            clean += "/"
        parts = [p for p in clean.split("/") if p]
        missing = str(self.site_dir / ".does-not-exist")
        if not parts:
            return missing
        if parts[0] == "talks":
            base, rest = self.site_dir, parts[1:]
        elif parts[0] == "media" and len(parts) >= 2 and parts[1] in self.media_dirs:
            base, rest = self.media_dirs[parts[1]], parts[2:]
            if len(rest) != 1 or not MEDIA_NAME_RE.match(rest[0]):
                return missing
        else:
            return missing
        if any(p in ("..", ".") for p in rest):
            return missing
        target = base.joinpath(*rest) if rest else base
        out = str(target)
        if clean.endswith("/"):
            out += "/"
        return out

    def send_head(self):
        if self.request_log is not None:
            self.request_log.append(self.path)
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().send_head()
        m = RANGE_RE.match(rng.strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return super().send_head()
        size = path.stat().st_size
        first, last = m.group(1), m.group(2)
        if first == "":
            start, end = max(0, size - int(last)), size - 1
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = path.open("rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile) -> None:
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            super().copyfile(source, outputfile)
            return
        try:
            while remaining > 0:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                outputfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers abort media range requests routinely.
            pass
        finally:
            self._remaining = None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        # Media elements cancel range requests mid-stream; that is not an error.
        pass


def make_server(
    site_dir: Path,
    media_dirs: dict[str, Path],
    host: str = "127.0.0.1",
    port: int = 0,
    request_log: list[str] | None = None,
) -> Server:
    handler = type(
        "BoundTalksHandler",
        (TalksHandler,),
        {
            "site_dir": site_dir.resolve(),
            "media_dirs": {k: v.resolve() for k, v in media_dirs.items()},
            "request_log": request_log,
        },
    )
    return Server((host, port), partial(handler, directory=str(site_dir.resolve())))


def start_in_thread(server: Server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="talks-server", daemon=True)
    thread.start()
    return thread


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", type=Path, default=REPO / "site", help="site directory (default: <repo>/site)")
    ap.add_argument("--build", type=Path, default=REPO / "build", help="build root (default: <repo>/build)")
    ap.add_argument("--slug", action="append", required=True, help="talk slug to serve media for (repeatable)")
    ap.add_argument(
        "--host", default="127.0.0.1", help="interface (localhost or 127.0.0.1 keep the ?media= hook working)"
    )
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    media = {slug: args.build / slug / "media" for slug in args.slug}
    for slug, d in media.items():
        if not d.is_dir():
            ap.error(f"no media for {slug}: run the build first")
    server = make_server(args.site, media, args.host, args.port)
    for slug in args.slug:
        print(f"http://{args.host}:{server.server_address[1]}/talks/{slug}/?media=/media/{slug}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
