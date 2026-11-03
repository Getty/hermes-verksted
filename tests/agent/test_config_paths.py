"""Folder checks of the step-1 prototype that k6 keeps. The .verksted.yaml tests moved to
test_config.py (k5), the backend path rules are in test_paths.py (k6)."""

from __future__ import annotations

import os

import pytest

from verksted import paths


def test_folder_must_be_absolute_existing_and_a_directory(tmp_path):
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder("relative/dir")
    assert err.value.code == "bad_folder"
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder(str(tmp_path / "missing"))
    assert err.value.code == "folder_missing"
    (tmp_path / "f.txt").write_text("x")
    with pytest.raises(paths.PathRefused) as err:
        paths.canonical_folder(str(tmp_path / "f.txt"))
    assert err.value.code == "not_a_folder"
    for bad in ("", "   ", None, 7, f" {tmp_path}"):
        with pytest.raises(paths.PathRefused):
            paths.canonical_folder(bad)


def test_folder_is_canonicalised(tmp_path):
    real = tmp_path / "real dir ü"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    canonical, key = paths.canonical_folder(str(link) + "/./")
    assert canonical == str(real.resolve())
    assert key == os.path.normcase(canonical)


def test_relevant_files_stay_inside_the_folder(tmp_path):
    assert paths.relative_files(str(tmp_path), None) == []
    assert paths.relative_files(str(tmp_path), ["a.txt", "sub/b.md"]) == ["a.txt", "sub/b.md"]
    for bad in (["../x"], ["/etc/passwd"], [""], "a.txt", [3]):
        with pytest.raises(paths.PathRefused):
            paths.relative_files(str(tmp_path), bad)
