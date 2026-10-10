"""Manifest schema 1: serialisation and validation (docs/SPEC.md section 3)."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import jsonschema

MEDIA_NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")
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
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


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
        errors.append(f"canvas must be {CANVAS[0]} x {CANVAS[1]} for v1")
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
    return errors


def validate_file(path: Path, media_dir: Path | None = None) -> list[str]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest: {exc}"]
    return validate(manifest, media_dir)
