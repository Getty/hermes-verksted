"""Target folder checks, done where the session will run: on the backend that executes the tool.

Minimal for the step-1 prototype: absolute, existing, a directory, canonical. Drive letters,
junctions, the WSL path space and Unicode normalisation are card k6.
"""

from __future__ import annotations

import os
from pathlib import Path


class PathRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def canonical_folder(raw: object) -> tuple[str, str]:
    """``(canonical path, comparison key)`` for a target folder, or :class:`PathRefused`.

    The key is what "one active run per folder" compares; ``os.path.normcase`` folds case on
    Windows and is the identity elsewhere.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PathRefused("bad_folder", "folder must be a non-empty absolute path")
    if raw != raw.strip() or "\x00" in raw:
        raise PathRefused("bad_folder", "folder must not have surrounding whitespace or NUL bytes")
    if not os.path.isabs(raw):
        raise PathRefused("bad_folder", f"folder must be an absolute path on the backend: {raw!r}")
    try:
        resolved = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathRefused("folder_missing", f"folder does not exist on the backend: {raw!r} ({exc})") from exc
    if not resolved.is_dir():
        raise PathRefused("not_a_folder", f"not a folder: {str(resolved)!r}")
    canonical = str(resolved)
    return canonical, os.path.normcase(canonical)


def relative_files(folder: str, raw: object) -> list[str]:
    """Validate the optional list of relevant files: relative paths that stay inside ``folder``."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 50:
        raise PathRefused("bad_files", "files must be a list of at most 50 relative paths")
    out: list[str] = []
    base = Path(folder)
    for item in raw:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise PathRefused("bad_files", "each file must be a non-empty relative path")
        if os.path.isabs(item) or Path(item).drive:
            raise PathRefused("bad_files", f"file must be relative to the folder: {item!r}")
        target = (base / item).resolve()
        if target != base and base not in target.parents:
            raise PathRefused("bad_files", f"file leaves the folder: {item!r}")
        out.append(item)
    return out
