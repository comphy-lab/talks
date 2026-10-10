"""Content-addressed media store: ``<sha256[:16]>.<ext>``."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from .proc import run

CONTENT_TYPES = {"webp": "image/webp", "mp4": "video/mp4"}
WEBP_QUALITY = 85


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def file_sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class MediaStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.written: set[str] = set()

    def _tmp(self, name: str) -> Path:
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".part", dir=self.dir)
        os.close(fd)
        return Path(tmp)

    def put_bytes(self, data: bytes, ext: str) -> str:
        name = f"{sha16(data)}.{ext}"
        path = self.dir / name
        if not path.exists():
            tmp = self._tmp(name)
            tmp.write_bytes(data)
            tmp.replace(path)
        self.written.add(name)
        return name

    def put_file(self, src: Path, ext: str) -> str:
        name = f"{file_sha16(src)}.{ext}"
        path = self.dir / name
        if not path.exists():
            tmp = self._tmp(name)
            shutil.copyfile(src, tmp)
            tmp.replace(path)
        self.written.add(name)
        return name

    def put_webp(self, img: Image.Image, *, alpha: bool = False, quality: int = WEBP_QUALITY) -> str:
        return self.put_bytes(encode_webp(img, alpha=alpha, quality=quality), "webp")

    def stale(self) -> list[Path]:
        """Files in the store not produced by this build."""
        return sorted(
            p for p in self.dir.iterdir() if p.is_file() and not p.name.startswith(".") and p.name not in self.written
        )


def encode_webp(img: Image.Image, *, alpha: bool = False, quality: int = WEBP_QUALITY) -> bytes:
    """Encode with ``cwebp`` (deterministic for identical pixels and options)."""
    with tempfile.TemporaryDirectory(prefix="talks-build-webp-") as tmp:
        src = Path(tmp) / "in.png"
        dst = Path(tmp) / "out.webp"
        if alpha:
            img.convert("RGBA").save(src, format="PNG", compress_level=1)
            args = ["cwebp", "-quiet", "-q", str(quality), "-alpha_q", "100", "-m", "6", "-sharp_yuv"]
        else:
            img.convert("RGB").save(src, format="PNG", compress_level=1)
            args = ["cwebp", "-quiet", "-q", str(quality), "-m", "6", "-sharp_yuv"]
        run(args + ["-metadata", "none", str(src), "-o", str(dst)])
        return dst.read_bytes()
