"""Talk page from the player template, and the talks index (site/talks.json)."""

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

TOKENS = ("{{TITLE}}", "{{DESCRIPTION}}", "{{SLUG}}")
HTML_ATTRIBUTE = re.compile(
    r"(?P<name>[^\s=/>]+)(?:\s*=\s*(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)"
    r"|(?P<unquoted>[^\s>]+)))?",
    re.DOTALL,
)


def fingerprint_assets(markup: str, page_path: Path, site_dir: Path) -> str:
    """Version owned JS/CSS URLs by their bytes, preserving names and markup.

    URLs outside this site are untouched. Local paths and symlink targets must
    remain inside the site root; missing owned assets fail closed.
    """
    root = Path(site_dir).resolve()
    page = Path(page_path).resolve()
    if not page.is_relative_to(root):
        raise ValueError("HTML page must remain inside the site root")

    def version_url(raw_url):
        url = html.unescape(raw_url)
        parts = urlsplit(url)
        if parts.scheme or parts.netloc:
            return None
        path = unquote(parts.path)
        if not path.lower().endswith((".js", ".css")):
            return None
        if path.startswith("/"):
            if not path.startswith("/talks/"):
                return None  # assets owned by the main-site publisher
            target = root / path.removeprefix("/talks/")
        else:
            target = page.parent / path
        target = target.resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"owned asset escapes the site root: {parts.path}")
        if not target.is_file():
            raise ValueError(f"owned asset is missing: {parts.path}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
        # Keep each existing parameter's raw encoding and bare/blank form. A
        # decode/re-encode round trip can corrupt non-UTF8 bytes or signed URLs.
        query = [
            item for item in parts.query.split("&")
            if unquote_plus(item.partition("=")[0]) != "v"
        ] if parts.query else []
        query.append("v=" + digest)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), parts.fragment))

    line_starts = [0] + [match.end() for match in re.finditer("\n", markup)]
    edits = []

    class AssetParser(HTMLParser):
        def handle_starttag(self, tag, attrs):
            wanted = {"script": "src", "link": "href"}.get(tag)
            if wanted is None:
                return
            raw = self.get_starttag_text()
            tag_end = re.match(r"<[^\s/>]+", raw).end()
            for match in HTML_ATTRIBUTE.finditer(raw, tag_end):
                if match["name"].lower() != wanted:
                    continue
                group = "quoted" if match["quote"] else "unquoted"
                value = match[group]
                if value is None:
                    return
                updated = version_url(value)
                if updated is not None:
                    escaped = html.escape(updated, quote=True)
                    if not match["quote"]:
                        escaped = '"' + escaped + '"'
                    line, column = self.getpos()
                    offset = line_starts[line - 1] + column
                    start, end = match.span(group)
                    edits.append((offset + start, offset + end, escaped))
                return  # browsers use the first instance of a duplicate attribute

    parser = AssetParser(convert_charrefs=False)
    parser.feed(markup)
    parser.close()
    for start, end, replacement in reversed(edits):
        markup = markup[:start] + replacement + markup[end:]
    return markup


def refresh_assets(site_dir: Path, *, check: bool = False) -> list[Path]:
    """Refresh every source/generated HTML reference, without rebuilding slides."""
    root = Path(site_dir).resolve()
    if not root.is_dir():
        raise ValueError("site directory is missing")
    updates = []
    for path in sorted(root.rglob("*.html")):
        if not path.resolve().is_relative_to(root):
            raise ValueError("HTML symlink escapes the site root")
        original = path.read_text(encoding="utf-8")
        updated = fingerprint_assets(original, path, root)
        if updated != original:
            updates.append((path, updated))
    if not check:
        for path, updated in updates:
            path.write_text(updated, encoding="utf-8")
    return [path for path, _ in updates]


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
    page = fingerprint_assets(page, out, site_dir)
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
