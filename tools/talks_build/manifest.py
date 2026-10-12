"""Manifest schemas 1 and 2: serialisation and validation (docs/SPEC.md section 3)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import jsonschema
from PIL import Image

MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")
DOWNLOAD_FILE_RE = re.compile(r"^([0-9a-f]{16})/[A-Za-z0-9][A-Za-z0-9._-]*\.(pdf|pptx|key)$")
DOWNLOAD_MAX_BYTES = 300_000_000  # the cf CLI's single-request upload limit
CANVAS = (1920, 1080)


def schema_path() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "schema" / "manifest.schema.json", here / "schema" / "manifest.schema.json"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("manifest.schema.json not found next to the talks_build package")


def load_schema() -> dict:
    return json.loads(schema_path().read_text(encoding="utf-8"))


def dumps(manifest: dict) -> str:
    """Stable, diff-friendly JSON (key order as built, two-space indent)."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def placement_key(item: dict) -> str:
    """Player placement key: kind + ref + rect rounded to 1 px."""
    rect = ",".join(str(int(math.floor(float(v) + 0.5))) for v in item["rect"])
    return f"{item['kind']}:{item['ref']}:{rect}"


def media_files(manifest: dict) -> list[str]:
    names: list[str] = []
    for v in manifest.get("videos", {}).values():
        names += [v.get("src"), v.get("poster")]
    for slide in manifest.get("slides", []):
        for st in slide.get("states", []):
            names.append(st.get("image"))
            if st.get("overlay") is not None:
                names.append(st.get("overlay"))
            if st.get("image2x") is not None:
                names.append(st["image2x"])
            if st.get("overlay2x") is not None:
                names.append(st["overlay2x"])
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def download_files(manifest: dict) -> list[str]:
    """Media-relative paths (``<sha16>/<name>``) of the manifest's downloads."""
    return [d.get("file") for d in manifest.get("downloads", []) if isinstance(d, dict)]


def _sha16(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _validate_downloads(manifest: dict, media_dir: Path | None) -> list[str]:
    errors = []
    seen: set[str] = set()
    for i, d in enumerate(manifest.get("downloads", [])):
        where = f"downloads[{i}]"
        m = DOWNLOAD_FILE_RE.match(d["file"])
        if not m or m.group(2) != d["format"]:
            errors.append(f"{where}: file {d['file']!r} does not match format {d['format']!r}")
            continue
        if d["format"] in seen:
            errors.append(f"{where}: second {d['format']} download")
        seen.add(d["format"])
        if d["bytes"] > DOWNLOAD_MAX_BYTES:
            errors.append(f"{where}: {d['bytes']} bytes exceeds the {DOWNLOAD_MAX_BYTES} byte upload limit")
        if media_dir is None:
            continue
        path = Path(media_dir) / d["file"]
        if not path.is_file():
            errors.append(f"{where}: missing from {Path(media_dir).name}/: {d['file']}")
        elif path.stat().st_size != d["bytes"]:
            errors.append(f"{where}: {d['file']} is {path.stat().st_size} bytes, manifest says {d['bytes']}")
        elif _sha16(path) != m.group(1):
            errors.append(f"{where}: content hash of {d['file']} does not match its directory")
    return errors


def validate(manifest: dict, media_dir: Path | None = None) -> list[str]:
    """Return a list of problems (empty when the manifest is valid)."""
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(load_schema())
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"schema: {where}: {err.message}")
    if errors:
        return errors

    canvas = manifest["canvas"]
    if (canvas["width"], canvas["height"]) != CANVAS:
        errors.append(f"canvas must be {CANVAS[0]} x {CANVAS[1]} logical pixels")
    slug = manifest["slug"]
    want = f"https://media.comphy-lab.org/talks/{slug}/"
    if manifest["media_base"] != want:
        errors.append(f"media_base must be {want}")

    videos, youtube = manifest["videos"], manifest["youtube"]
    for ref, y in youtube.items():
        if not y["end"] > y["start"]:
            errors.append(f"youtube {ref}: end must be after start")
    seen_ids: set[str] = set()
    used_refs: set[str] = set()
    for i, slide in enumerate(manifest["slides"], 1):
        if slide["n"] != i:
            errors.append(f"slide {i}: n is {slide['n']}, expected {i}")
        if slide["id"] in seen_ids:
            errors.append(f"slide {i}: duplicate id {slide['id']}")
        seen_ids.add(slide["id"])
        for j, st in enumerate(slide["states"]):
            density_fields = "image2x" in st or "overlay2x" in st
            if density_fields:
                where = f"slide {i} state {j}"
                if manifest["schema"] != 2:
                    errors.append(f"{where}: density assets require schema 2")
                if "image2x" not in st or "overlay2x" not in st:
                    errors.append(f"{where}: image2x and overlay2x must be supplied together")
                elif (st["overlay"] is None) != (st["overlay2x"] is None):
                    errors.append(f"{where}: overlay and overlay2x must agree on transparency-layer presence")
            keys: set[str] = set()
            for item in st["media"]:
                ref = item["ref"]
                table = videos if item["kind"] == "video" else youtube
                if ref not in table:
                    errors.append(f"slide {i} state {j}: {item['kind']} ref {ref} does not resolve")
                used_refs.add(ref)
                x, y, w, h = item["rect"]
                if w <= 0 or h <= 0:
                    errors.append(f"slide {i} state {j}: {ref} has an empty rect")
                key = placement_key(item)
                if key in keys:
                    errors.append(f"slide {i} state {j}: duplicate placement key {key}")
                keys.add(key)
    for ref in list(videos) + list(youtube):
        if ref not in used_refs:
            errors.append(f"{ref} is never placed")
    for name in media_files(manifest):
        if not isinstance(name, str) or not MEDIA_NAME_RE.match(name):
            errors.append(f"bad media file name: {name!r}")
        elif media_dir is not None and not (Path(media_dir) / name).is_file():
            errors.append(f"media file missing from {Path(media_dir).name}/: {name}")
    if media_dir is not None:
        errors.extend(_validate_density_sizes(manifest, Path(media_dir)))
    errors.extend(_validate_downloads(manifest, Path(media_dir) if media_dir is not None else None))
    return errors


def _validate_density_sizes(manifest: dict, media_dir: Path) -> list[str]:
    """Verify actual still dimensions when density assets are available locally."""
    errors = []
    checked: set[tuple[str, int]] = set()
    for slide in manifest["slides"]:
        for state in slide["states"]:
            if "image2x" not in state:
                continue
            for field, scale in (("image", 1), ("overlay", 1), ("image2x", 2), ("overlay2x", 2)):
                name = state.get(field)
                if name is None or (name, scale) in checked or not (media_dir / name).is_file():
                    continue
                checked.add((name, scale))
                want = (CANVAS[0] * scale, CANVAS[1] * scale)
                try:
                    with Image.open(media_dir / name) as im:
                        if im.size != want:
                            errors.append(f"{field} {name}: expected {want[0]} x {want[1]}, got {im.width} x {im.height}")
                except OSError as exc:
                    errors.append(f"{field} {name}: cannot read image: {exc}")
    return errors


def validate_file(path: Path, media_dir: Path | None = None) -> list[str]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest: {exc}"]
    return validate(manifest, media_dir)
