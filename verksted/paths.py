"""Target folder checks, done where the session will run: on the backend that executes the tool
(docs/architecture.md §6, card k6).

Every check runs through a :class:`Backend`: the path flavour (``ntpath`` or ``posixpath``) and
the filesystem calls of the backend process. The default is this process; the tests inject
``ntpath`` with a filesystem stub so the Windows rules also run on Linux (simulated, not
exercised on Windows).

Rules (the choices are Verksted's, recorded on k6):

- A folder is an absolute, existing directory, canonicalised with ``realpath`` (symlinks,
  junctions, 8.3 short names where the OS expands them). Names stay byte-exact: no Unicode
  normalisation either way, and a name that is not valid Unicode is refused (it could not be
  stored or sent to the gateway).
- Windows: a drive path. A ``\\\\?\\X:\\`` prefix is accepted and stored without the prefix,
  since Hermes' tools need the plain form; a folder reachable only with the prefix is refused.
  UNC paths (``\\\\server\\share``, ``\\\\?\\UNC\\``, ``\\\\wsl.localhost\\``), device paths
  (``\\\\.\\``) and folders whose real path is one of these (a mapped network drive) are
  refused: v1 covers local folders (§7 "Umfang von Version 1").
- A Linux backend given a Windows path: inside WSL it is mapped with Hermes' own helper and the
  mapped path is checked like any other; elsewhere it is refused. A ``\\\\wsl.localhost\\<distro>``
  path must name the distro the backend runs in.
- The comparison key ("one active run per folder") is ``normcase`` of the canonical path:
  case-folded on Windows, and on a WSL backend for the case-insensitive Windows drives under
  ``/mnt/<drive>/``.
"""

from __future__ import annotations

import ntpath
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

MAX_FILES = 50

_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WSL_UNC = re.compile(r"^[\\/]{2}wsl(?:\.localhost|\$)[\\/]([^\\/]+)(?:[\\/]|$)", re.IGNORECASE)
_WSL_DRIVE = re.compile(r"^/mnt/[A-Za-z](?:/|$)")


class PathRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_capped(path: str, limit: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit + 1)


@dataclass(frozen=True)
class Wsl:
    """A WSL backend: ``translate`` maps a Windows path into the WSL path space (Hermes'
    ``translate_cwd_for_wsl_backend``), ``distro`` is the distro this backend runs in."""
    translate: Callable[[str], str]
    distro: str = ""


@dataclass(frozen=True)
class Backend:
    pathmod: Any = os.path
    platform: str = sys.platform
    realpath: Callable[[str], str] = lambda p: os.path.realpath(p, strict=True)  # raises when missing
    realpath_loose: Callable[[str], str] = os.path.realpath
    isdir: Callable[[str], bool] = os.path.isdir
    isfile: Callable[[str], bool] = os.path.isfile
    lexists: Callable[[str], bool] = os.path.lexists
    read_bytes: Callable[[str, int], bytes] = _read_capped  # at most limit + 1 bytes
    wsl: Optional[Wsl] = None

    @property
    def windows(self) -> bool:
        return self.pathmod is ntpath

    @property
    def sep(self) -> str:
        return self.pathmod.sep

    def key(self, canonical: str) -> str:
        if self.wsl is not None and _WSL_DRIVE.match(canonical):
            return canonical.lower()  # drvfs: Windows drives are case-insensitive by default
        return self.pathmod.normcase(canonical)

    def inside(self, base: str, path: str) -> bool:
        """``path`` is ``base`` or below it; both canonical."""
        kb, kp = self.key(base), self.key(path)
        if kp == kb:
            return True
        prefix = kb if kb.endswith(self.sep) else kb + self.sep
        return kp.startswith(prefix)


LOCAL = Backend()


def _text(raw: object, what: str = "folder") -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise PathRefused("bad_folder", f"{what} must be a non-empty absolute path")
    if raw != raw.strip() or "\x00" in raw:
        raise PathRefused("bad_folder", f"{what} must not have surrounding whitespace or NUL bytes")
    return raw


def _storable(path: str) -> None:
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PathRefused("bad_folder", f"folder name is not valid Unicode and cannot be stored: {path!r}") from exc


def _windows_input(raw: str) -> str:
    slashed = raw.replace("/", "\\")
    if slashed.startswith("\\\\?\\") or slashed.startswith("\\\\.\\"):
        rest = slashed[4:]
        if slashed.startswith("\\\\.\\"):
            raise PathRefused("device_path", f"device paths are not workspace folders: {raw!r}")
        if rest[:4].upper() == "UNC\\":
            raise PathRefused("unc_path", f"network paths are not supported in v1 (local folders only): {raw!r}")
        if not _DRIVE_PATH.match(rest):
            raise PathRefused("device_path", f"only drive paths may carry the \\\\?\\ prefix: {raw!r}")
        return slashed  # NTFS names never contain "/", and after the prefix Win32 would not convert it
    if slashed.startswith("\\\\"):
        raise PathRefused("unc_path", f"network and WSL share paths are not supported in v1 (local folders "
                                      f"only); give a folder on a local drive: {raw!r}")
    if not _DRIVE_PATH.match(raw):
        raise PathRefused("bad_folder", f"folder must be an absolute path with a drive letter on this "
                                        f"Windows backend, like C:\\Work: {raw!r}")
    return raw


def _windows_plain(real: str, backend: Backend, raw: str) -> str:
    """The canonical drive form of a Windows ``realpath`` result."""
    if real.startswith("\\\\?\\"):
        rest = real[4:]
        if rest[:4].upper() == "UNC\\":
            raise PathRefused("unc_path", f"{raw!r} resolves to a network path ({real!r}); v1 supports local folders only")
        if not _DRIVE_PATH.match(rest):
            raise PathRefused("device_path", f"{raw!r} resolves to a device path ({real!r})")
        if not backend.isdir(rest):
            raise PathRefused("path_too_long", f"{raw!r} is reachable only with the \\\\?\\ prefix (longer "
                                               "than the Windows path limit?); Hermes' tools need a plain "
                                               "path. Use a shorter folder path.")
        return rest
    if real.startswith("\\\\") or real.startswith("//"):
        raise PathRefused("unc_path", f"{raw!r} resolves to a network path ({real!r}); v1 supports local folders only")
    if not _DRIVE_PATH.match(real):
        raise PathRefused("bad_folder", f"{raw!r} does not resolve to a drive path ({real!r})")
    return real


def _posix_input(raw: str, backend: Backend) -> str:
    windowsish = _DRIVE_PATH.match(raw) or raw.startswith("\\\\") or _WSL_UNC.match(raw)
    if windowsish:
        if backend.wsl is None:
            raise PathRefused("windows_path", f"{raw!r} is a Windows path, but this Hermes backend runs on "
                                              f"{backend.platform} with its own path space; give the folder "
                                              "as a path on the backend")
        unc = _WSL_UNC.match(raw)
        if unc:
            distro = backend.wsl.distro
            if not distro or unc.group(1).lower() != distro.lower():
                raise PathRefused("windows_path", f"{raw!r} names WSL distro {unc.group(1)!r}, but this backend "
                                                  f"runs in {distro or 'an unknown distro'!r}")
        translated = backend.wsl.translate(raw)
        if not isinstance(translated, str) or not translated.startswith("/"):
            raise PathRefused("windows_path", f"{raw!r} cannot be mapped into this WSL backend's path space")
        return translated
    if not raw.startswith("/"):
        raise PathRefused("bad_folder", f"folder must be an absolute path on the backend: {raw!r}")
    return raw


def canonical_folder(raw: object, backend: Backend = LOCAL) -> tuple[str, str]:
    """``(canonical path, comparison key)`` for a target folder, or :class:`PathRefused`."""
    raw = _text(raw)
    path = _windows_input(raw) if backend.windows else _posix_input(raw, backend)
    try:
        real = backend.realpath(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathRefused("folder_missing", f"folder does not exist on the backend: {raw!r} ({exc})") from exc
    if backend.windows:
        real = _windows_plain(real, backend, raw)
    if not backend.isdir(real):
        raise PathRefused("not_a_folder", f"not a folder: {real!r}")
    _storable(real)
    return real, backend.key(real)


def relative_files(folder: str, raw: object, backend: Backend = LOCAL) -> list[str]:
    """Validate the optional list of relevant files: relative paths that stay inside ``folder``
    (canonical), also after following symlinks or junctions."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FILES:
        raise PathRefused("bad_files", f"files must be a list of at most {MAX_FILES} relative paths")
    pm = backend.pathmod
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise PathRefused("bad_files", "each file must be a non-empty relative path")
        if backend.windows:
            absolute = pm.isabs(item) or item.startswith(("/", "\\")) or bool(pm.splitdrive(item)[0])
        else:
            absolute = item.startswith("/")
        if absolute:
            raise PathRefused("bad_files", f"file must be relative to the folder: {item!r}")
        joined = pm.join(folder, item)
        real = backend.realpath_loose(joined)
        if backend.windows and real.startswith("\\\\?\\") and _DRIVE_PATH.match(real[4:]):
            real = real[4:]
        if not (backend.inside(folder, pm.normpath(joined)) and backend.inside(folder, real)):
            raise PathRefused("bad_files", f"file leaves the folder: {item!r}")
        out.append(item)
    return out
