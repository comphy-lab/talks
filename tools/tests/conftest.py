"""Shared fixtures. Tests needing external tools skip when they are absent."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

TOOLS = ("ffmpeg", "ffprobe", "pdftocairo", "pdftotext", "pdffonts", "cwebp")


def have_tools() -> bool:
    return all(shutil.which(t) for t in TOOLS)


needs_tools = pytest.mark.skipif(not have_tools(), reason="needs ffmpeg, poppler and cwebp on PATH")


@pytest.fixture(scope="session")
def synthetic_export(tmp_path_factory) -> Path:
    if not have_tools():
        pytest.skip("needs ffmpeg, poppler and cwebp on PATH")
    from synthetic_export import write_export

    root = tmp_path_factory.mktemp("export")
    return write_export(root)


@pytest.fixture(scope="session")
def real_export() -> Path:
    """The real Keynote export, from ``TALKS_EXPORT`` (skipped when unset)."""
    value = os.environ.get("TALKS_EXPORT")
    if not value:
        pytest.skip("set TALKS_EXPORT to the Keynote HTML export folder")
    path = Path(value)
    if not (path / "assets" / "header.json").is_file():
        pytest.skip("TALKS_EXPORT does not point at a Keynote HTML export")
    return path
