"""Subprocess helpers. Commands are always argument lists (never a shell)."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence


class ToolError(RuntimeError):
    """An external tool is missing or failed."""


def require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolError(f"required tool not found on PATH: {tool}")
    return path


def run(args: Sequence[str], *, capture: bool = False, text: bool = False, input_bytes: bytes | None = None):
    """Run ``args``; raise ``ToolError`` with stderr on failure.

    Returns the ``CompletedProcess`` (stdout captured when ``capture``).
    """
    args = [str(a) for a in args]
    require(args[0])
    try:
        proc = subprocess.run(
            args,
            input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - environment failure
        raise ToolError(f"{args[0]}: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise ToolError(f"{args[0]} failed ({proc.returncode}): {err[-2000:]}")
    if capture and text:
        proc.stdout = proc.stdout.decode("utf-8", "replace")
    return proc
