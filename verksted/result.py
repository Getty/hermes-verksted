"""The run result's closing block (card k10): the text Verksted appends to every task, the parser
for the block the child ends its final answer with, and the check of the paths it names.

Hermes keeps no public per-turn record of the files a turn wrote, and "written" is not "output"
anyway, so the child declares its outputs and Verksted checks them. The check proves that a
named path exists inside the workspace root when the run finishes — not that the child wrote it.

Standard library only, and no import of the other Verksted modules: ``dashboard/plugin_api.py``
loads this file by path and passes the path backend (``paths.Backend``) in.

Grammar (the choices are Verksted's, recorded on k10)::

    <verksted-result>
    summary: one to three sentences
    outputs:
    - a/relative/path or an absolute one
    </verksted-result>

- Only the last 256 KiB of the answer are read; the last opening tag wins (tags are matched
  case-insensitively), so a block the child quoted earlier does not count.
- No opening tag → ``missing``. No closing tag, a body over 16 KiB, a line before ``summary:``,
  a repeated or missing key, a line after ``outputs:`` that is not an item, items after
  ``outputs: none`` or a value that is still the template's ``<…>`` placeholder → ``unparsable``.
- Tolerated: CRLF, blank lines, code-fence lines, ``**Summary:**``-style emphasis on a key,
  ``-``, ``*``, ``+``, ``•`` and ``1.`` item markers, one pair of backticks or quotes around a
  path. A path is taken verbatim otherwise: no glob, no ``~`` or variable expansion, nothing
  listed, nothing taken from tool calls.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

TAIL_LIMIT = 256 * 1024
BLOCK_LIMIT = 16 * 1024
SUMMARY_LIMIT = 2000
ITEMS_LIMIT = 50
PATH_LIMIT = 1024

_OPEN = re.compile(r"<\s*verksted-result\s*>", re.IGNORECASE)
_CLOSE = re.compile(r"<\s*/\s*verksted-result\s*>", re.IGNORECASE)
_KEY = re.compile(r"^[*_]{0,2}(summary|outputs)[*_]{0,2}\s*:[*_]{0,2}\s*(.*)$", re.IGNORECASE)
_ITEM = re.compile(r"^(?:[-*+•]|\d+[.)])\s+(.*)$")
_FENCE = re.compile(r"^(?:```|~~~)[\w-]*$")
_PLACEHOLDER = re.compile(r"^<[^<>]*>$")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def request_text(root: str) -> str:
    """The text appended to every submitted task (stored with the prompt before the start)."""
    return (
        "\n\n---\n"
        "When you are done, end your final answer with this block, filled in. Verksted reads it "
        "to report your result, so put it at the very end, once:\n"
        "\n"
        "<verksted-result>\n"
        "summary: <one to three sentences: what you did and what came out of it>\n"
        "outputs:\n"
        "- <a file or folder you created or changed for this task>\n"
        "</verksted-result>\n"
        "\n"
        "Rules for the block:\n"
        '- After "outputs:", one path per line, each line starting with "- ".\n'
        f"- Write each path as an absolute path, or relative to the workspace root `{root}`.\n"
        "- List the files and folders that make up your result and exist when you finish; check "
        "that each one is there before you list it. Leave out files you only read or deleted.\n"
        '- If you created or changed no files, write "outputs: none".\n'
        "- Nothing else inside the block: no Markdown, no code fences, no quotes or notes after a path.\n"
    )


def _parsed(block: str, detail: Optional[str] = None, summary: Optional[str] = None,
            items: Optional[list[str]] = None, truncated: bool = False) -> dict[str, Any]:
    return {"block": block, "detail": detail, "summary": summary, "items": items or [], "truncated": truncated}


def _unwrap(value: str) -> str:
    value = value.strip()
    for left, right in (("`", "`"), ('"', '"'), ("'", "'")):
        if len(value) >= 2 and value[0] == left and value[-1] == right:
            return value[1:-1].strip()
    return value


def parse(text: Any) -> dict[str, Any]:
    """``{block: ok|missing|unparsable, detail, summary, items, truncated}`` for a final answer."""
    text = text[-TAIL_LIMIT:] if isinstance(text, str) else ""
    opens = list(_OPEN.finditer(text))
    if not opens:
        return _parsed("missing", "the final answer has no <verksted-result> block")
    start = opens[-1].end()
    close = _CLOSE.search(text, start)
    if close is None:
        return _parsed("unparsable", "no </verksted-result> after the last <verksted-result>")
    body = text[start:close.start()]
    if len(body) > BLOCK_LIMIT:
        return _parsed("unparsable", f"the block is longer than {BLOCK_LIMIT} characters")
    summary: list[str] = []
    items: list[str] = []
    seen: set[str] = set()
    section: Optional[str] = None
    none = False
    for number, raw in enumerate(body.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or _FENCE.match(line):
            continue
        key = _KEY.match(line)
        if key:
            name, value = key.group(1).lower(), key.group(2).strip()
            if name in seen:
                return _parsed("unparsable", f"{name}: appears twice")
            seen.add(name)
            section = name
            if name == "summary":
                if value:
                    summary.append(value)
            elif value.rstrip(".").lower() == "none":
                none = True
            elif value:
                items.append(_unwrap(value))
            continue
        if section == "summary":
            summary.append(line)
        elif section == "outputs":
            item = _ITEM.match(line)
            if not item:
                return _parsed("unparsable", f"line {number} after outputs: is not a '- path' line")
            if none:
                return _parsed("unparsable", "outputs: none is followed by paths")
            items.append(_unwrap(item.group(1)))
        else:
            return _parsed("unparsable", f"line {number} comes before summary:")
    for name in ("summary", "outputs"):
        if name not in seen:
            return _parsed("unparsable", f"the block has no {name}: line")
    text_summary = "\n".join(summary).strip()
    if not text_summary:
        return _parsed("unparsable", "summary: is empty")
    if _PLACEHOLDER.match(text_summary) or any(_PLACEHOLDER.match(i) for i in items):
        return _parsed("unparsable", "the block still holds the template's <…> placeholder")
    truncated = False
    if len(text_summary) > SUMMARY_LIMIT:
        text_summary, truncated = text_summary[:SUMMARY_LIMIT], True
    if len(items) > ITEMS_LIMIT:
        items, truncated = items[:ITEMS_LIMIT], True
    return _parsed("ok", None, text_summary, items, truncated)


def _missing(item: str, reason: str) -> dict[str, Any]:
    return {"path": item[:PATH_LIMIT], "reason": reason}


def _check(item: str, root: str, backend: Any, stat: Callable[[str], Any]) -> tuple[bool, dict[str, Any]]:
    pm = backend.pathmod
    if not item or len(item) > PATH_LIMIT or _CONTROL.search(item):
        return False, _missing(item, "bad_path")
    if backend.windows:
        if item.startswith(("\\\\", "//")):
            return False, _missing(item, "bad_path")  # UNC, device or \\?\ form: v1 is local folders only
        drive, rest = pm.splitdrive(item)
        if drive and not rest.startswith(("\\", "/")):
            return False, _missing(item, "bad_path")  # drive-relative ("C:x")
        if not drive and item.startswith(("\\", "/")):
            return False, _missing(item, "bad_path")  # root-relative ("\\x"): which drive?
        joined = item if drive else pm.join(root, item)
    else:
        if _DRIVE.match(item) or item.startswith("\\\\"):
            return False, _missing(item, "bad_path")  # a Windows path on a POSIX backend: never translated
        joined = item if item.startswith("/") else pm.join(root, item)
    try:
        real = backend.realpath(joined)
    except (OSError, RuntimeError, ValueError):
        return False, _missing(item, "not_found")
    if backend.windows and real.startswith("\\\\?\\") and _DRIVE.match(real[4:]):
        real = real[4:]
    if not backend.inside(root, real):
        return False, _missing(item, "outside_workspace")  # also a symlink or junction leading out
    if backend.isfile(real):
        kind = "file"
    elif backend.isdir(real):
        kind = "folder"
    else:
        return False, _missing(item, "not_file_or_folder")
    size = mtime = None
    try:
        st = stat(real)
        mtime = float(st.st_mtime)
        size = int(st.st_size) if kind == "file" else None
    except (OSError, ValueError, AttributeError):
        pass
    relative = "." if backend.key(real) == backend.key(root) else pm.relpath(real, root).replace("\\", "/")
    return True, {"path": real, "relative": relative, "kind": kind, "size": size, "mtime": mtime}


def verify(parsed: dict[str, Any], root: str, backend: Any, *,
           stat: Optional[Callable[[str], Any]] = None) -> dict[str, Any]:
    """Check each path of a parsed block against the workspace ``root`` on ``backend``.

    The answer is what the store keeps as the result reference: ``outputs`` (``path`` canonical,
    ``relative`` with ``/``, ``kind``, ``size`` of a file, ``mtime``) and ``missing`` (``path`` as
    named, ``reason``: ``bad_path``, ``not_found``, ``outside_workspace``,
    ``not_file_or_folder``). Duplicates collapse by canonical path.
    """
    import os
    stat = stat or os.stat
    out = {k: parsed.get(k) for k in ("block", "detail", "summary", "truncated")}
    out.update(root=root, outputs=[], missing=[])
    if parsed.get("block") != "ok":
        return out
    try:
        canonical_root = backend.realpath(root)
    except (OSError, RuntimeError, ValueError):
        canonical_root = root
    if backend.windows and canonical_root.startswith("\\\\?\\") and _DRIVE.match(canonical_root[4:]):
        canonical_root = canonical_root[4:]
    seen: set[str] = set()
    for item in parsed.get("items") or []:
        ok, entry = _check(item, canonical_root, backend, stat)
        if not ok:
            out["missing"].append(entry)
            continue
        key = backend.key(entry["path"])
        if key not in seen:
            seen.add(key)
            out["outputs"].append(entry)
    return out


def check(final_response: Any, root: str, backend: Any, **kwargs: Any) -> dict[str, Any]:
    """Parse and verify in one step, as the plugin API does at ``finished``."""
    return verify(parse(final_response), root, backend, **kwargs)
