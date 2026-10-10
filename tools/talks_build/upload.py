"""Upload content-addressed media to R2 with the ``cf`` CLI, never overwriting.

Keys are ``talks/<slug>/<file>`` in bucket ``comphy-media``. The existing
keys are listed first, every page of them; a file whose key exists is
skipped. If the listing fails or does not finish, nothing is uploaded.

Paging uses ``--start-after`` with the last key seen, not the API's cursor:
the ``cf`` CLI prints only the ``result`` array of a response, so the
``result_info`` cursor never reaches us (cf 1.0.0-beta.12). Listing stops at
the first page that brings no key past the previous one, which does not
depend on the page size the server actually honours.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

from .media import CONTENT_TYPES
from .proc import run

BUCKET = "comphy-media"
NAME_RE = re.compile(r"^[0-9a-f]{16}\.(webp|mp4)$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PER_PAGE = 1000  # R2's largest page
MAX_PAGES = 10000

Runner = Callable[[Sequence[str]], str]


def _default_runner(args: Sequence[str]) -> str:
    return run(list(args), capture=True, text=True).stdout


def _keys_from_output(text: str) -> list[str]:
    """Object keys in ``cf r2 objects list`` output (a bare array, or an API envelope)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("cf r2 objects list did not return JSON; refusing to upload blind") from exc
    keys: list[str] = []

    def collect(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("key"), str):
                keys.append(obj["key"])
            for k, v in obj.items():
                if k != "result_info":
                    collect(v)
        elif isinstance(obj, list):
            for v in obj:
                collect(v)

    collect(data)
    return keys


def check_slug(slug: str) -> str:
    """A talk slug as talk.yaml allows it (lower-case words joined by hyphens), or ValueError."""
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ValueError(f"bad slug {slug!r}: lower-case letters and digits joined by single hyphens")
    return slug


def remote_keys(slug: str, runner: Runner = _default_runner, bucket: str = BUCKET) -> set[str]:
    prefix = f"talks/{check_slug(slug)}/"
    keys: set[str] = set()
    after: str | None = None
    for _ in range(MAX_PAGES):
        args = ["cf", "r2", "objects", "list", "--bucket-name", bucket, "--prefix", prefix,
                "--per-page", str(PER_PAGE)]  # fmt: skip
        if after is not None:
            args += ["--start-after", after]
        page = [k for k in _keys_from_output(runner(args)) if k.startswith(prefix)]
        fresh = [k for k in page if after is None or k > after]
        if not fresh:
            return keys
        keys.update(fresh)
        after = max(fresh)
    raise RuntimeError("R2 listing did not terminate; refusing to upload")


def plan(media_dir: Path, slug: str, existing: set[str], only: set[str] | None = None) -> list[tuple[Path, str, str]]:
    """Files to upload as ``(path, key, content_type)``, skipping existing keys.

    ``only`` restricts the upload to the files a manifest references, so
    leftovers from earlier builds in the same folder are never published.
    """
    check_slug(slug)
    todo = []
    for path in sorted(Path(media_dir).iterdir()):
        if not path.is_file() or not NAME_RE.match(path.name):
            continue
        if only is not None and path.name not in only:
            continue
        key = f"talks/{slug}/{path.name}"
        if key in existing:
            continue
        todo.append((path, key, CONTENT_TYPES[path.suffix[1:]]))
    return todo


def put_command(path: Path, key: str, content_type: str, bucket: str = BUCKET) -> list[str]:
    return [
        "cf", "r2", "objects", "put", key,
        "--bucket-name", bucket,
        "--file", str(path),
        "--content-type", content_type,
    ]  # fmt: skip


def upload(
    media_dir: Path,
    slug: str,
    *,
    dry_run: bool,
    check_remote: bool = True,
    runner: Runner = _default_runner,
    only: set[str] | None = None,
    log=print,
) -> list[str]:
    """Upload missing files; return the keys uploaded (or planned in a dry run)."""
    check_slug(slug)
    media_dir = Path(media_dir)
    if not media_dir.is_dir():
        raise FileNotFoundError(f"no media directory: {media_dir}")
    existing: set[str] = set()
    if check_remote:
        existing = remote_keys(slug, runner)
        log(f"{len(existing)} objects already under talks/{slug}/")
    elif not dry_run:
        raise ValueError("a real upload must check the remote listing first")
    if only is not None:
        missing = sorted(n for n in only if not (media_dir / n).is_file())
        if missing:
            raise FileNotFoundError(f"{len(missing)} manifest media files are not in {media_dir.name}/")
        extra = [p.name for p in media_dir.iterdir() if p.is_file() and NAME_RE.match(p.name) and p.name not in only]
        if extra:
            log(f"skipping {len(extra)} files not referenced by the manifest")
    todo = plan(media_dir, slug, existing, only)
    total = sum(p.stat().st_size for p, _, _ in todo)
    log(f"{len(todo)} files to upload ({total / 1e6:.1f} MB)")
    done = []
    for path, key, ctype in todo:
        args = put_command(path, key, ctype)
        if dry_run:
            log(
                "would run: "
                + " ".join(args[:5])
                + f" --bucket-name {BUCKET} --file {path.name} --content-type {ctype}"
            )
        else:
            runner(args)
            log(f"uploaded {key}")
        done.append(key)
    return done
