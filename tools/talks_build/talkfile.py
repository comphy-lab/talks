"""Load ``talks-src/<slug>/talk.yaml`` (docs/SPEC.md section 1.2)."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .patches import PatchSpec
from .youtube import BEFORE_START, YouTubeSpec

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DOWNLOAD_FORMATS = ("pdf", "pptx", "key")


class TalkError(ValueError):
    pass


@dataclass
class Talk:
    path: Path
    slug: str
    title: str
    subtitle: str
    speaker: str
    venue_note: str
    updated: str
    media_base: str
    loop: bool
    spans: str
    max_scale: float
    youtube: list[YouTubeSpec] = field(default_factory=list)
    patches: list[PatchSpec] = field(default_factory=list)
    changelog: list[dict] = field(default_factory=list)
    # Slide UUID -> title, where the title derived from the slide's text is wrong.
    titles: dict[str, str] = field(default_factory=dict)
    # Downloadable copies offered on the talk page, in this order.
    downloads: list[str] = field(default_factory=list)


def _date(value, what: str) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise TalkError(f"{what}: not an ISO date: {text!r}") from exc
    return text


def _str(data: dict, key: str, required: bool = True) -> str:
    value = data.get(key)
    if value is None:
        if required:
            raise TalkError(f"talk.yaml: missing {key}")
        return ""
    if not isinstance(value, str):
        raise TalkError(f"talk.yaml: {key} must be a string")
    return value.strip()


def load_talk(path: Path) -> Talk:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TalkError("talk.yaml: top level must be a mapping")
    slug = _str(data, "slug")
    if not SLUG_RE.match(slug):
        raise TalkError(f"talk.yaml: bad slug {slug!r}")
    media_base = _str(data, "media_base")
    want = f"https://media.comphy-lab.org/talks/{slug}/"
    if media_base != want:
        raise TalkError(f"talk.yaml: media_base must be {want}")
    video = data.get("video") or {}
    youtube = []
    for i, y in enumerate(data.get("youtube") or []):
        where = f"talk.yaml youtube[{i}]"
        if not UUID_RE.match(str(y.get("slide", ""))):
            raise TalkError(f"{where}: slide must be a Keynote slide UUID")
        if not YOUTUBE_ID_RE.match(str(y.get("id", ""))):
            raise TalkError(f"{where}: bad YouTube id")
        start, end = float(y.get("start", 0)), float(y.get("end", 0))
        if not 0 <= start < end:
            raise TalkError(f"{where}: need 0 <= start < end")
        rect = y.get("rect")
        if rect is not None:
            if not (isinstance(rect, list) and len(rect) == 4):
                raise TalkError(f"{where}: rect must be [x, y, w, h] or null")
            rect = tuple(int(round(float(v))) for v in rect)
        before_start = str(y.get("before_start") or "poster")
        if before_start not in BEFORE_START:
            raise TalkError(f"{where}: before_start must be one of {', '.join(BEFORE_START)}")
        youtube.append(
            YouTubeSpec(
                slide=str(y["slide"]),
                movie=str(y.get("movie") or ""),
                id=str(y["id"]),
                start=start,
                end=end,
                title=str(y.get("title") or ""),
                credit=str(y.get("credit") or ""),
                rect=rect,
                before_start=before_start,
            )
        )
        if not youtube[-1].movie:
            raise TalkError(f"{where}: movie (file-name prefix) is required")
    patches = []
    for i, p in enumerate(data.get("patches") or []):
        where = f"talk.yaml patches[{i}]"
        if not UUID_RE.match(str(p.get("slide", ""))):
            raise TalkError(f"{where}: slide must be a Keynote slide UUID")
        find, replace = p.get("find"), p.get("replace")
        if not isinstance(find, str) or not find.strip():
            raise TalkError(f"{where}: find must be non-empty text")
        if not isinstance(replace, str) or not replace.strip():
            raise TalkError(f"{where}: replace must be non-empty text")
        patches.append(PatchSpec(slide=str(p["slide"]), find=find.strip(), replace=replace.strip(), doi=p.get("doi")))
    titles: dict[str, str] = {}
    raw_titles = data.get("titles") or {}
    if not isinstance(raw_titles, dict):
        raise TalkError("talk.yaml: titles must map slide UUIDs to text")
    for sid, title in raw_titles.items():
        if not UUID_RE.match(str(sid)):
            raise TalkError(f"talk.yaml titles: {sid!r} is not a Keynote slide UUID")
        if not isinstance(title, str) or not title.strip():
            raise TalkError(f"talk.yaml titles[{sid}]: title must be non-empty text")
        titles[str(sid)] = title.strip()
    changelog = []
    for i, c in enumerate(data.get("changelog") or []):
        changelog.append({"date": _date(c.get("date"), f"changelog[{i}]"), "note": str(c.get("note") or "").strip()})
    downloads = data.get("downloads") or []
    if not isinstance(downloads, list) or not all(isinstance(d, str) for d in downloads):
        raise TalkError("talk.yaml: downloads must be a list of formats")
    bad = [d for d in downloads if d not in DOWNLOAD_FORMATS]
    if bad or len(set(downloads)) != len(downloads):
        raise TalkError(f"talk.yaml: downloads must be distinct values from {', '.join(DOWNLOAD_FORMATS)}")
    max_scale = float(video.get("max_scale", 2.0))
    if not 0 < max_scale <= 8:
        raise TalkError("talk.yaml: video.max_scale out of range")
    return Talk(
        path=path,
        slug=slug,
        title=_str(data, "title"),
        subtitle=_str(data, "subtitle", required=False),
        speaker=_str(data, "speaker"),
        venue_note=_str(data, "venue_note", required=False),
        updated=_date(data.get("updated"), "updated"),
        media_base=media_base,
        loop=bool(video.get("loop", True)),
        spans=str(video.get("spans", "auto")),
        max_scale=max_scale,
        youtube=youtube,
        patches=patches,
        changelog=changelog,
        titles=titles,
        downloads=list(downloads),
    )
