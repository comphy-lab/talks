"""Compare the talks shell with immutable main-site source; exit 1 on drift, 2 on error."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

UPSTREAM = "comphy-lab/comphy-lab.github.io"
EDIT_TALKS = "https://github.com/comphy-lab/talks"
EDIT_UPSTREAM = f"https://github.com/{UPSTREAM}"
ISSUE_MARKER = "<!-- comphy-shared-shell-drift -->"
MAX_RESPONSE = 2 * 1024 * 1024
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class ShellError(Exception):
    """The source could not be fetched or interpreted reliably."""


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list[Element | str] = field(default_factory=list)
    parent: Element | None = field(default=None, repr=False)

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def visible_text(self) -> str:
        if self.attrs.get("aria-hidden") == "true" or self.tag in ("script", "style"):
            return ""
        parts = [child.visible_text() if isinstance(child, Element) else child for child in self.children]
        if self.tag == "img":
            parts.append(" " + self.attrs.get("alt", "") + " ")
        return "".join(parts)

    def text(self) -> str:
        return " ".join(self.visible_text().split())


class Document(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        element = Element(tag, {key: value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def one(root: Element, class_name: str) -> Element:
    matches = [node for node in root.walk() if node.has_class(class_name)]
    if len(matches) != 1:
        raise ShellError(f"expected one .{class_name}, found {len(matches)}")
    return matches[0]


def name(node: Element, root: Element) -> str:
    if "aria-label" in node.attrs:
        return " ".join(node.attrs["aria-label"].split())
    if "aria-labelledby" in node.attrs:
        while root.parent is not None:
            root = root.parent
        ids = {element.attrs["id"]: element for element in root.walk() if "id" in element.attrs}
        labels = node.attrs["aria-labelledby"].split()
        if not labels or any(label not in ids for label in labels):
            raise ShellError("unresolved aria-labelledby in shell link")
        return " ".join(ids[label].text() for label in labels)
    return node.text()


def links(root: Element):
    for node in root.walk():
        if node.tag != "a":
            continue
        label, href = name(node, root), node.attrs.get("href", "").strip()
        if not label or not href:
            raise ShellError("shell link has no accessible name or href")
        yield node, label, href


def menu_breakpoint(css: str) -> int:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    widths = set()
    pattern = r"@media\s+([^{}]*?\(\s*max-width\s*:\s*(\d+)px\s*\)[^{}]*)\{"
    for match in re.finditer(pattern, css):
        depth, end, quote = 1, match.end(), None
        while end < len(css) and depth:
            char = css[end]
            if quote:
                if char == "\\":
                    end += 1
                elif char == quote:
                    quote = None
            elif char in ("'", '"'):
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            end += 1
        if depth:
            raise ShellError("unterminated CSS media query")
        block = css[match.end() : end - 1]
        if re.search(r"\.s-header__menu-toggle\s*\{[^{}]*\bdisplay\s*:\s*(?:flex|block|inline-flex)\b", block):
            widths.add(int(match.group(2)))
    if len(widths) != 1:
        raise ShellError(f"expected one Menu max-width breakpoint, found {sorted(widths)}")
    return widths.pop()


@dataclass(frozen=True)
class Shell:
    navigation: tuple[tuple[str, str], ...]
    columns: tuple[tuple[str, str], ...]
    footer: tuple[tuple[str, str, str], ...]
    breakpoint: int


def extract(header_html: str, footer_html: str, css: str, *, talks: bool = False) -> Shell:
    header = one(Document(header_html).root, "s-header")
    nav = one(header, "s-header__nav-list")
    navigation = tuple((label, href) for _, label, href in links(nav))
    if not navigation:
        raise ShellError("header navigation is empty")
    footer = one(Document(footer_html).root, "c-footer")
    columns = []
    for column in (node for node in footer.walk() if node.has_class("c-footer__col")):
        headings = [node for node in column.walk()
                    if node.has_class("c-footer__heading") or re.fullmatch(r"h[1-6]", node.tag)]
        if len(headings) != 1 or not column.attrs.get("aria-label"):
            raise ShellError("footer column needs one heading and an accessible label")
        columns.append((headings[0].text(), column.attrs["aria-label"]))
    if not columns:
        raise ShellError("footer columns are empty")
    footer_links = []
    for node, label, href in links(footer):
        parent, group = node.parent, None
        while parent is not None and parent is not footer:
            if parent.has_class("c-footer__col"):
                group = parent.attrs["aria-label"]
                break
            for class_name, section in (
                ("c-footer__brand", "Brand"),
                ("c-footer__partners", "Partners"),
                ("c-footer__legal", "Legal"),
            ):
                if parent.has_class(class_name):
                    group = section
                    break
            if group:
                break
            parent = parent.parent
        if group is None:
            raise ShellError("footer link is outside a recognised shell section")
        # This exact documented target is the sole footer URL substitution.
        if talks and label == "Edit on GitHub" and href == EDIT_TALKS:
            href = EDIT_UPSTREAM
        footer_links.append((group, label, href))
    return Shell(navigation, tuple(columns), tuple(footer_links), menu_breakpoint(css))


def differences(expected: Shell, actual: Shell, page: str) -> list[str]:
    output = []
    for field_name in ("navigation", "columns", "footer"):
        before, after = getattr(expected, field_name), getattr(actual, field_name)
        if before == after:
            continue
        output.append(f"{page}: {field_name} differs")
        output.extend(
            difflib.unified_diff(
                [json.dumps(row, ensure_ascii=False) for row in before],
                [json.dumps(row, ensure_ascii=False) for row in after],
                fromfile="main site", tofile=page, lineterm="",
            )
        )
    if expected.breakpoint != actual.breakpoint:
        output.append(f"{page}: Menu breakpoint {actual.breakpoint}px; main site {expected.breakpoint}px")
    return output


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Public source URLs are fixed. Do not forward an API bearer elsewhere.
        raise ShellError("unexpected redirect while fetching immutable upstream source")


def fetch(url: str) -> str:
    headers = {"User-Agent": "comphy-talks-shell-check", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if url.startswith("https://api.github.com/") and token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=20) as response:
        content = response.read(MAX_RESPONSE + 1)
    if len(content) > MAX_RESPONSE:
        raise ShellError("upstream response exceeds size limit")
    return content.decode("utf-8")


def load_upstream(root: Path | None) -> tuple[str, str, str, str | None]:
    if root is not None:
        include = root / "_includes/header.html"
        header = include if include.is_file() else root / "_layouts/default.html"
        return (
            header.read_text(), (root / "_includes/footer-v2.html").read_text(),
            (root / "assets/css/styles.css").read_text(), None,
        )
    ref = json.loads(fetch(f"https://api.github.com/repos/{UPSTREAM}/git/ref/heads/main"))
    obj = ref.get("object") if isinstance(ref, dict) else None
    commit = obj.get("sha") if isinstance(obj, dict) else None
    if (
        not isinstance(obj, dict) or obj.get("type") != "commit"
        or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
    ):
        raise ShellError("GitHub main did not resolve to a commit SHA")
    base = f"https://raw.githubusercontent.com/{UPSTREAM}/{commit}/"
    try:
        header = fetch(base + "_includes/header.html")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        header = fetch(base + "_layouts/default.html")
    return header, fetch(base + "_includes/footer-v2.html"), fetch(base + "assets/css/styles.css"), commit


def issue_body(commit: str, diff: list[str]) -> str:
    # Stable content, without timestamps: an unchanged report needs no issue write.
    rendered = "\n".join(diff).replace("```", "` ` `")
    return (
        f"{ISSUE_MARKER}\n\nThe shared header/footer differs from "
        f"[main-site commit {commit}](https://github.com/{UPSTREAM}/commit/{commit}).\n\n"
        f"```diff\n{rendered}\n```\n\n"
        "Port the verified changes to both talks templates and the vendored styles, "
        "then update `site/vendor/comphy-lab/SOURCE.md` and run the shell check.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", type=Path, help="read a local main-site checkout instead of GitHub")
    parser.add_argument("--site-root", type=Path, default=Path(__file__).resolve().parents[2] / "site")
    parser.add_argument("--json-output", type=Path, help="write the machine-readable report for CI")
    options = parser.parse_args(argv)
    report = {"status": "error", "upstream_commit": None, "differences": []}
    try:
        header, footer, css, commit = load_upstream(options.upstream_root)
        report["upstream_commit"] = commit
        expected = extract(header, footer, css)
        actual_css = (options.site_root / "vendor/comphy-lab/header.css").read_text()
        for path in ("index.html", "player/talk.template.html"):
            html = (options.site_root / path).read_text()
            report["differences"].extend(differences(expected, extract(html, html, actual_css, talks=True), path))
        report["status"] = "drift" if report["differences"] else "pass"
        if report["status"] == "drift" and commit:
            report["issue_body"] = issue_body(commit, report["differences"])
    except (ShellError, OSError, ValueError, urllib.error.URLError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    if options.json_output:
        options.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"pass": 0, "drift": 1, "error": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
