"""Filesystem stubs for ``paths.Backend``, so the Windows and WSL path rules run on Linux.

Simulated with ntpath — not exercised on Windows. ``WinFS.realpath`` imitates CPython's
``ntpath.realpath`` on Windows as far as these tests need it: ``GetFinalPathNameByHandle`` gives
the true case of every component, expands 8.3 short names and follows junctions and symlinks;
the ``\\\\?\\`` prefix stays only when the input carried it; a mapped network drive resolves to
its ``\\\\server\\share`` path. What a real Windows does beyond this (NTFS case-folding tables, long
path limits, reparse points of other kinds) is the Windows acceptance run's job.
"""

from __future__ import annotations

import ntpath
import posixpath
from typing import Optional

from verksted import paths

PREFIX = "\\\\?\\"


class WinFS:
    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: dict[str, bytes] = {}
        self.links: dict[str, str] = {}      # junction or symlink (true-case path) -> target
        self.short: dict[str, str] = {}      # 8.3 component (upper case) -> long component
        self.mapped: dict[str, str] = {}     # "Z:" -> "\\\\server\\share"
        self.long_only: set[str] = set()     # dirs reachable only with the \\?\ prefix

    # -- building --
    def mkdir(self, path: str) -> str:
        cur = path
        while True:
            self.dirs.add(cur)
            parent = ntpath.dirname(cur)
            if parent == cur:
                return path
            cur = parent

    def write(self, path: str, data: bytes | str) -> None:
        self.mkdir(ntpath.dirname(path))
        self.files[path] = data.encode("utf-8") if isinstance(data, str) else data

    def link(self, path: str, target: str) -> None:
        self.mkdir(ntpath.dirname(path))
        self.links[path] = target

    # -- lookups --
    def _entries(self):
        return [*self.dirs, *self.files, *self.links]

    def _match(self, path: str) -> Optional[str]:
        low = path.lower()
        for entry in self._entries():
            if entry.lower() == low:
                return entry
        return None

    def _resolve(self, path: str, strict: bool) -> str:
        had_prefix = path.startswith(PREFIX)
        p = (path[4:] if had_prefix else path).replace("/", "\\")
        drive, rest = ntpath.splitdrive(p)
        if drive.upper() in self.mapped:
            return self.mapped[drive.upper()] + rest
        parts = [c for c in rest.split("\\") if c and c != "."]
        cur = drive.upper() + "\\"
        for i, comp in enumerate(parts):
            if comp == "..":
                cur = ntpath.dirname(cur)
                continue
            comp = self.short.get(comp.upper(), comp)
            found = self._match(ntpath.join(cur, comp))
            if found is None:
                if strict:
                    raise FileNotFoundError(2, "The system cannot find the file specified", path)
                cur = ntpath.join(cur, comp, *parts[i + 1:])
                break
            cur = found
            if cur in self.links:
                cur = self._resolve(self.links[cur], strict)
                if cur.startswith(PREFIX):
                    cur = cur[4:]
        return PREFIX + cur if had_prefix else cur

    # -- the Backend interface --
    def realpath(self, path: str) -> str:
        return self._resolve(path, True)

    def realpath_loose(self, path: str) -> str:
        return self._resolve(path, False)

    def isdir(self, path: str) -> bool:
        if path.startswith("\\\\") and not path.startswith(PREFIX):
            return False
        plain = path[4:] if path.startswith(PREFIX) else path
        found = self._match(self._resolve(plain, False))
        if found is None or found not in self.dirs:
            return False
        return path.startswith(PREFIX) or found not in self.long_only

    def isfile(self, path: str) -> bool:
        found = self._match(self._resolve(path, False))
        return found is not None and found in self.files

    def lexists(self, path: str) -> bool:
        parent = self._resolve(ntpath.dirname(path), False)
        return self._match(ntpath.join(parent, ntpath.basename(path))) is not None

    def read_bytes(self, path: str, limit: int) -> bytes:
        return self.files[self._match(self._resolve(path, False))][: limit + 1]

    def backend(self) -> paths.Backend:
        return paths.Backend(pathmod=ntpath, platform="win32", realpath=self.realpath,
                             realpath_loose=self.realpath_loose, isdir=self.isdir, isfile=self.isfile,
                             lexists=self.lexists, read_bytes=self.read_bytes)


class WslFS:
    """A WSL backend's POSIX space: ``/mnt/<drive>/`` is case-insensitive (drvfs default) and
    returns the spelling it was given, like Linux ``realpath`` does there."""

    def __init__(self, dirs: list[str]) -> None:
        self.dirs = set()
        for d in dirs:
            cur = d
            while cur not in ("/", ""):
                self.dirs.add(cur)
                cur = posixpath.dirname(cur)
            self.dirs.add("/")

    def _exists(self, path: str) -> bool:
        if path.lower().startswith("/mnt/"):
            return path.lower() in {d.lower() for d in self.dirs}
        return path in self.dirs

    def realpath(self, path: str) -> str:
        norm = posixpath.normpath(path)
        if not self._exists(norm):
            raise FileNotFoundError(2, "No such file or directory", path)
        return norm

    def backend(self, translate, distro: str = "Ubuntu") -> paths.Backend:
        return paths.Backend(pathmod=posixpath, platform="linux", realpath=self.realpath,
                             realpath_loose=posixpath.normpath, isdir=self._exists, isfile=lambda p: False,
                             lexists=lambda p: self._exists(posixpath.normpath(p)), wsl=paths.Wsl(translate, distro))
