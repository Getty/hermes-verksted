"""Path checks on the executing backend (card k6, docs/architecture.md §6, invariant 12).

Three groups:

- the real Linux filesystem of this box;
- a Windows backend, simulated with ntpath — not exercised on Windows (``fakefs.WinFS``);
- a WSL backend, simulated with a POSIX stub and Hermes' own path translation
  (``hermes_constants.translate_cwd_for_wsl_backend`` at ad2b705, with ``is_wsl`` patched).
"""

from __future__ import annotations

import os
import unicodedata

import pytest

from fakefs import WinFS, WslFS
from verksted import paths

# -- the real Linux filesystem ------------------------------------------------------------------


def test_a_folder_with_space_and_umlaut_keeps_its_bytes(tmp_path):
    folder = tmp_path / "Mein Ordner ü"
    folder.mkdir()
    canonical, key = paths.canonical_folder(str(folder))
    assert canonical == str(folder) and key == canonical
    assert canonical.encode("utf-8") == os.fsencode(folder)


def test_nfc_and_nfd_spellings_are_never_rewritten(tmp_path):
    nfc = tmp_path / unicodedata.normalize("NFC", "Jörg")
    nfd = tmp_path / unicodedata.normalize("NFD", "Jörg")
    nfc.mkdir()
    nfd.mkdir()  # two folders on Linux: no normalisation either way
    assert paths.canonical_folder(str(nfc))[0] == str(nfc)
    assert paths.canonical_folder(str(nfd))[0] == str(nfd)
    assert paths.canonical_folder(str(nfc))[1] != paths.canonical_folder(str(nfd))[1]


def test_a_name_that_is_not_valid_unicode_is_refused(tmp_path):
    raw = os.fsencode(tmp_path) + b"/bad\xff name"
    os.mkdir(raw)
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder(os.fsdecode(raw))
    assert err.value.code == "bad_folder"


def test_a_windows_path_on_a_linux_backend_outside_wsl_is_refused():
    for raw in ("C:\\Work", "c:/work", "\\\\server\\share\\x", "\\\\wsl.localhost\\Ubuntu\\home\\u"):
        with pytest.raises(paths.PathRefused) as err:
            paths.canonical_folder(raw)
        assert err.value.code == "windows_path", raw


def test_files_that_leave_the_folder_through_a_symlink_are_refused(tmp_path):
    folder = tmp_path / "ws"
    (folder / "sub").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder / "escape").symlink_to(outside)
    (folder / "escape-file").symlink_to(outside / "secret.txt")
    (folder / "inner").symlink_to(folder / "sub")
    base = str(folder)
    assert paths.relative_files(base, ["inner/a.md", "sub/../a.md"]) == ["inner/a.md", "sub/../a.md"]
    for bad in (["escape/secret.txt"], ["escape-file"], ["sub/../../outside/x"], ["inner/../../outside"]):
        with pytest.raises(paths.PathRefused) as err:
            paths.relative_files(base, bad)
        assert err.value.code == "bad_files", bad


# -- a Windows backend, simulated with ntpath — not exercised on Windows --------------------------


@pytest.fixture
def win():
    fs = WinFS()
    fs.mkdir("C:\\Work\\Sub")
    fs.mkdir("C:\\Users\\Jörg\\Mein Ordner")
    fs.mkdir("C:\\Program Files\\App")
    fs.short["PROGRA~1"] = "Program Files"
    fs.mkdir("D:\\Other")
    fs.link("C:\\Work\\out", "D:\\Other")            # a junction out of the workspace
    fs.link("C:\\Elsewhere\\into", "C:\\Work\\Sub")   # a junction into it
    fs.mapped["Z:"] = "\\\\server\\share"
    return fs


def test_win_drive_letter_case_and_trailing_separator_are_the_same_folder(win):
    """Simulated with ntpath — not exercised on Windows."""
    b = win.backend()
    a = paths.canonical_folder("C:\\Work", b)
    assert paths.canonical_folder("c:\\work\\", b) == a
    assert paths.canonical_folder("c:/WORK/", b) == a
    assert a == ("C:\\Work", "c:\\work")


def test_win_space_and_umlaut_are_kept_exactly(win):
    """Simulated with ntpath — not exercised on Windows."""
    canonical, key = paths.canonical_folder("c:\\users\\jörg\\mein ordner", win.backend())
    assert canonical == "C:\\Users\\Jörg\\Mein Ordner" and key == "c:\\users\\jörg\\mein ordner"
    nfd = unicodedata.normalize("NFD", "C:\\Users\\Jörg\\Mein Ordner")
    with pytest.raises(paths.PathRefused) as err:  # NFD is another name, never folded into NFC
        paths.canonical_folder(nfd, win.backend())
    assert err.value.code == "folder_missing"


def test_win_short_names_and_junctions_resolve_to_the_real_folder(win):
    """Simulated with ntpath — not exercised on Windows."""
    b = win.backend()
    assert paths.canonical_folder("C:\\PROGRA~1\\App", b)[0] == "C:\\Program Files\\App"
    assert paths.canonical_folder("C:\\Work\\out", b)[0] == "D:\\Other"
    # A junction into the workspace is the folder it points to: the same key, so the same lock.
    assert paths.canonical_folder("C:\\Elsewhere\\into", b) == paths.canonical_folder("C:\\Work\\Sub", b)


def test_win_long_path_prefix_is_accepted_and_stored_plain(win):
    """Simulated with ntpath — not exercised on Windows."""
    b = win.backend()
    assert paths.canonical_folder("\\\\?\\C:\\Work", b) == ("C:\\Work", "c:\\work")
    assert paths.canonical_folder("//?/c:/work/sub", b)[0] == "C:\\Work\\Sub"
    long_dir = "C:\\Work\\" + "x" * 250
    win.mkdir(long_dir)
    win.long_only.add(long_dir)
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder("\\\\?\\" + long_dir, b)
    assert err.value.code == "path_too_long"


@pytest.mark.parametrize("raw, code", [
    ("\\\\server\\share\\proj", "unc_path"),
    ("//server/share/proj", "unc_path"),
    ("\\\\?\\UNC\\server\\share\\proj", "unc_path"),
    ("\\\\wsl.localhost\\Ubuntu\\home\\u", "unc_path"),
    ("\\\\.\\PhysicalDrive0", "device_path"),
    ("\\\\?\\Volume{0000}\\x", "device_path"),
    ("Z:\\proj", "unc_path"),            # a mapped network drive resolves to its share
    ("C:Work", "bad_folder"),            # drive-relative
    ("\\Work", "bad_folder"),            # root-relative
    ("Work", "bad_folder"),
    ("C:\\Missing", "folder_missing"),
])
def test_win_paths_outside_v1_are_refused(win, raw, code):
    """Simulated with ntpath — not exercised on Windows."""
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder(raw, win.backend())
    assert err.value.code == code


def test_win_files_stay_inside_the_folder(win):
    """Simulated with ntpath — not exercised on Windows."""
    b = win.backend()
    assert paths.relative_files("C:\\Work", ["Sub\\a.md", "sub/b.md", "Sub\\..\\c.md"], b) == \
        ["Sub\\a.md", "sub/b.md", "Sub\\..\\c.md"]
    for bad in (["..\\x"], ["C:x"], ["C:\\Work\\a"], ["\\a"], ["/a"], ["out\\secret.txt"],
                ["\\\\server\\share\\a"], ["Sub\\..\\..\\x"]):
        with pytest.raises(paths.PathRefused) as err:
            paths.relative_files("C:\\Work", bad, b)
        assert err.value.code == "bad_files", bad


# -- a WSL backend: Hermes' own translation, then the ordinary checks ---------------------------


@pytest.fixture
def wsl(monkeypatch):
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "is_wsl", lambda: True)
    fs = WslFS(["/mnt/c/Work/Sub", "/home/u/ws"])
    return fs.backend(hermes_constants.translate_cwd_for_wsl_backend, distro="Ubuntu")


def test_wsl_a_windows_drive_path_is_mapped_and_checked(wsl):
    assert paths.canonical_folder("C:\\Work", wsl) == ("/mnt/c/Work", "/mnt/c/work")
    # drvfs is case-insensitive: every spelling of the folder is one lock key.
    assert paths.canonical_folder("c:\\work\\", wsl)[1] == "/mnt/c/work"
    assert paths.canonical_folder("/mnt/c/WORK/Sub", wsl)[1] == "/mnt/c/work/sub"
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder("D:\\Nowhere", wsl)  # mapped, then checked: it does not exist
    assert err.value.code == "folder_missing"


def test_wsl_a_share_path_must_name_this_distro(wsl):
    assert paths.canonical_folder("\\\\wsl.localhost\\Ubuntu\\home\\u\\ws", wsl)[0] == "/home/u/ws"
    assert paths.canonical_folder("\\\\wsl$\\ubuntu\\home\\u\\ws", wsl)[0] == "/home/u/ws"
    for raw in ("\\\\wsl.localhost\\Debian\\home\\u\\ws", "\\\\server\\share\\x"):
        with pytest.raises(paths.PathRefused) as err:
            paths.canonical_folder(raw, wsl)
        assert err.value.code == "windows_path", raw


def test_wsl_posix_paths_stay_case_sensitive(wsl):
    assert paths.canonical_folder("/home/u/ws", wsl) == ("/home/u/ws", "/home/u/ws")
