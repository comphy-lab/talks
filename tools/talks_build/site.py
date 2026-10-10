"""Talk page from the player template, and the talks index (site/talks.json)."""

from __future__ import annotations

import html
import json
from pathlib import Path

TOKENS = ("{{TITLE}}", "{{DESCRIPTION}}", "{{SLUG}}")


def description(talk) -> str:
    parts = [p for p in (talk.subtitle, f"A talk by {talk.speaker}.", talk.venue_note) if p]
    text = " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)
    return text


def render_page(template: str, *, title: str, description: str, slug: str) -> str:
    missing = [t for t in TOKENS if t not in template]
    if missing:
        raise ValueError(f"player template lacks tokens: {', '.join(missing)}")
    values = {
        "{{TITLE}}": html.escape(title, quote=True),
        "{{DESCRIPTION}}": html.escape(description, quote=True),
        "{{SLUG}}": html.escape(slug, quote=True),
    }
    out = template
    for token, value in values.items():
        out = out.replace(token, value)
    return out


def write_page(site_dir: Path, talk) -> Path | None:
    """Write ``site/<slug>/index.html``; ``None`` if the template is absent."""
    template_path = site_dir / "player" / "talk.template.html"
    if not template_path.is_file():
        return None
    page = render_page(
        template_path.read_text(encoding="utf-8"),
        title=talk.title,
        description=description(talk),
        slug=talk.slug,
    )
    out = site_dir / talk.slug / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def update_index(site_dir: Path, talk) -> Path:
    """Insert or replace this talk in ``site/talks.json`` (newest first)."""
    path = site_dir / "talks.json"
    entries: list[dict] = []
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("site/talks.json must be a JSON array")
        entries = [e for e in data if isinstance(e, dict) and e.get("slug") != talk.slug]
    entries.append(
        {
            "slug": talk.slug,
            "title": talk.title,
            "subtitle": talk.subtitle,
            "speaker": talk.speaker,
            "updated": talk.updated,
            "url": f"{talk.slug}/",
        }
    )
    entries.sort(key=lambda e: str(e.get("slug", "")))
    entries.sort(key=lambda e: str(e.get("updated", "")), reverse=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
