"""Command line: ``python -m talks_build {derive-pdf,build,downloads,validate,upload,refresh-assets}``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .build import BuildError, build, infer_root
from .keynote import ExportError
from .manifest import validate_file
from .patches import PatchError
from .proc import ToolError
from .talkfile import TalkError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talks_build", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build media, manifest and talk page from a Keynote HTML export")
    b.add_argument("--export", required=True, type=Path, help="Keynote HTML export folder")
    b.add_argument("--talk", required=True, type=Path, help="talks-src/<slug>/talk.yaml")
    b.add_argument("--out-root", type=Path, default=None, help="repository root (default: inferred from --talk)")
    b.add_argument("--pdf", type=Path, default=None, help="flat PDF export for slide text (default: <export>.pdf)")
    b.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1), help="parallel encodes")
    b.add_argument(
        "--prune",
        action="store_true",
        help="delete media files in build/<slug>/media that this build did not produce",
    )
    b.add_argument(
        "--stills",
        action="store_true",
        help="also keep each state's 2x image and overlay losslessly in build/<slug>/stills (for downloads)",
    )
    b.add_argument(
        "--preview",
        default="",
        help="comma-separated slide numbers whose state images/overlays are also saved as PNG in build/<slug>/preview",
    )

    d = sub.add_parser("derive-pdf", help="one vector PDF page per slide from the export's final-state textures")
    d.add_argument("--export", required=True, type=Path, help="Keynote HTML export folder")
    d.add_argument("--out", type=Path, default=None, help="output PDF (default: <export>.pdf, the build's flat PDF)")

    dl = sub.add_parser("downloads", help="build the PDF/PowerPoint/Keynote downloads and list them in the manifest")
    dl.add_argument("--talk", required=True, type=Path, help="talks-src/<slug>/talk.yaml")
    dl.add_argument("--export", type=Path, default=None, help="Keynote HTML export folder (for the PDF)")
    dl.add_argument("--formats", default=None, help="a subset of talk.yaml downloads to make (default: all of them)")
    dl.add_argument("--out-root", type=Path, default=None, help="repository root (default: inferred from --talk)")

    v = sub.add_parser("validate", help="check a manifest against the schema and invariants")
    v.add_argument("manifest", type=Path)
    v.add_argument("--media-dir", type=Path, default=None, help="also check that every media file exists here")

    u = sub.add_parser("upload", help="upload build/<slug>/media to R2 (never overwrites)")
    u.add_argument("--slug", required=True)
    u.add_argument("--root", type=Path, default=None, help="repository root (default: parent of tools/)")
    u.add_argument("--dry-run", action="store_true", help="print what would be uploaded; no remote writes")
    u.add_argument(
        "--check-remote",
        action="store_true",
        help="with --dry-run, also list existing R2 keys (read-only); a real upload always does",
    )
    assets = sub.add_parser("refresh-assets", help="refresh content fingerprints of owned HTML JS/CSS URLs")
    assets.add_argument("--site", type=Path, default=None, help="site root (default: repository site/)")
    assets.add_argument("--check", action="store_true", help="fail on stale URLs without changing files")
    return p


def _repo_root_default() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "refresh-assets":
        from .site import refresh_assets

        root = args.site.resolve() if args.site else _repo_root_default() / "site"
        try:
            changed = refresh_assets(root, check=args.check)
        except (OSError, ValueError) as exc:
            print(f"asset fingerprints failed: {exc}", file=sys.stderr)
            return 1
        if args.check and changed:
            print("stale runtime asset URLs; run python -m talks_build refresh-assets:", file=sys.stderr)
            for path in changed:
                print("  " + str(path.relative_to(root)), file=sys.stderr)
            return 1
        print(f"asset fingerprints {'checked' if args.check else 'refreshed'}: {len(changed)} HTML files")
        return 0
    if args.command == "derive-pdf":
        from .downloads import DownloadError, derive_pdf

        out = args.out or args.export.with_name(args.export.name + ".pdf")
        try:
            result = derive_pdf(args.export, out)
        except (DownloadError, ExportError, OSError) as exc:
            print(f"derive-pdf failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result))
        return 0
    if args.command == "build":
        previews = {int(x) for x in args.preview.split(",") if x.strip()}
        try:
            report = build(
                args.export,
                args.talk,
                args.out_root,
                pdf=args.pdf,
                jobs=args.jobs,
                previews=previews,
                prune=args.prune,
                stills=args.stills,
            )
        except (BuildError, ExportError, TalkError, PatchError, ToolError, ValueError) as exc:
            print(f"build failed: {exc}", file=sys.stderr)
            return 1
        summary = {
            k: report[k]
            for k in ("slides", "states", "videos", "youtube", "overlays", "media_files", "media_bytes", "seconds")
        }
        print(json.dumps(summary))
        return 0
    if args.command == "downloads":
        from .downloads import DownloadError, make_downloads
        from .talkfile import load_talk

        try:
            talk = load_talk(args.talk)
            root = args.out_root.resolve() if args.out_root else infer_root(args.talk)
            formats = [f.strip() for f in args.formats.split(",") if f.strip()] if args.formats else None
            entries = make_downloads(talk, root, args.export, formats)
        except (DownloadError, ExportError, TalkError, ToolError, OSError, ValueError) as exc:
            print(f"downloads failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(entries))
        return 0
    if args.command == "validate":
        problems = validate_file(args.manifest, args.media_dir)
        if problems:
            for line in problems:
                print(line, file=sys.stderr)
            return 1
        print(f"{args.manifest.name}: valid")
        return 0
    if args.command == "upload":
        from .manifest import download_files, media_files
        from .upload import check_slug, upload

        try:
            check_slug(args.slug)
        except ValueError as exc:
            print(f"upload failed: {exc}", file=sys.stderr)
            return 1
        root = args.root.resolve() if args.root else _repo_root_default()
        media_dir = root / "build" / args.slug / "media"
        manifest_path = root / "site" / args.slug / "manifest.json"
        if not manifest_path.is_file():
            print(f"no manifest at site/{args.slug}/manifest.json; build first", file=sys.stderr)
            return 1
        problems = validate_file(manifest_path, media_dir)
        if problems:
            print("refusing to upload: manifest invalid or media missing:", file=sys.stderr)
            for line in problems:
                print("  " + line, file=sys.stderr)
            return 1
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("slug") != args.slug:
            print(f"refusing to upload: the manifest's slug is {manifest.get('slug')!r}, not {args.slug!r}",
                  file=sys.stderr)  # fmt: skip
            return 1
        wanted = set(media_files(manifest)) | set(download_files(manifest))
        try:
            upload(media_dir, args.slug, dry_run=args.dry_run, check_remote=(not args.dry_run) or args.check_remote,
                   only=wanted)  # fmt: skip
        except (RuntimeError, ToolError, ValueError, FileNotFoundError) as exc:
            print(f"upload failed: {exc}", file=sys.stderr)
            return 1
        return 0
    return 2


__all__ = ["main", "infer_root"]
