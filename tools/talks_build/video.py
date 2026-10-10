"""Video identity, encoding (docs/SPEC.md section 4) and posters."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .proc import run

# Everything that affects the encoded bytes; part of the cache key.
X264_ARGS = [
    "-c:v", "libx264",
    "-profile:v", "high",
    "-preset", "slow",
    "-crf", "23",
    "-pix_fmt", "yuv420p",
    "-threads", "4",
    "-movflags", "+faststart",
    "-map_metadata", "-1",
    "-map_chapters", "-1",
    "-fflags", "+bitexact",
    "-flags:v", "+bitexact",
]  # fmt: skip
ENCODER_VERSION = "x264-crf23-slow-v1"


@dataclass(frozen=True)
class Probe:
    codec: str
    width: int
    height: int
    duration: float
    pix_fmt: str


def probe(path: Path) -> Probe:
    out = run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,pix_fmt:format=duration",
            "-of", "json", str(path),
        ],
        capture=True,
        text=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"no video stream: {path.name}")
    s = streams[0]
    return Probe(
        codec=str(s.get("codec_name")),
        width=int(s["width"]),
        height=int(s["height"]),
        duration=float(data.get("format", {}).get("duration") or 0.0),
        pix_fmt=str(s.get("pix_fmt")),
    )


def stream_hash(path: Path) -> str:
    """SHA-256 of the video stream's packet payloads.

    Keynote stores one copy of a movie per slide and re-muxes each copy, so
    container bytes (timestamps) differ while the coded stream is identical.
    ``-c copy`` hashes the coded packets without decoding.
    """
    out = run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
         "-f", "streamhash", "-hash", "sha256", "-"],
        capture=True,
        text=True,
    ).stdout  # fmt: skip
    for line in out.splitlines():
        if "SHA256=" in line:
            return line.split("SHA256=", 1)[1].strip()
    raise ValueError(f"no stream hash for {path.name}")


def encode(src: Path, dst: Path, width: int, height: int) -> None:
    if width % 2 or height % 2:
        raise ValueError("encoded dimensions must be even")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part.mp4")
    run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
         "-map", "0:v:0", "-an", "-sn", "-dn",
         "-vf", f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p",
         *X264_ARGS, str(tmp)]
    )  # fmt: skip
    tmp.replace(dst)


def first_frame(path: Path) -> Image.Image:
    with tempfile.TemporaryDirectory(prefix="talks-build-poster-") as tmp:
        png = Path(tmp) / "f.png"
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", "-f", "image2",
             "-c:v", "png", str(png)])  # fmt: skip
        with Image.open(png) as im:
            img = im.convert("RGB")
            img.load()
        return img


class EncodeCache:
    """Reuse encodes across builds; keyed by stream identity and settings."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, src: Path, digest: str, width: int, height: int) -> Path:
        key = hashlib.sha256(f"{digest}|{width}x{height}|{ENCODER_VERSION}|{X264_ARGS}".encode()).hexdigest()[:24]
        path = self.dir / f"{key}.mp4"
        if not path.exists():
            encode(src, path, width, height)
        return path
