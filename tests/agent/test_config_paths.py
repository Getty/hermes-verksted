"""Minimal folder checks and .verksted.yaml resolution of the step-1 prototype."""

from __future__ import annotations

import os

import pytest

from verksted import config, paths


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


def test_no_config_file_is_the_default_case(tmp_path):
    resolved = config.resolve(str(tmp_path), parent_profile="default")
    assert resolved == {"version": 1, "profile": "default", "toolsets": None, "files": []}
    assert config.start_toolsets(resolved) == (None, None)


def test_toolsets_list_becomes_request_and_expectation(tmp_path):
    (tmp_path / ".verksted.yaml").write_text("version: 1\ntoolsets: [terminal, file]\n")
    resolved = config.resolve(str(tmp_path), parent_profile="default")
    assert resolved["toolsets"] == ["terminal", "file"]
    assert resolved["files"] == [str(tmp_path / ".verksted.yaml")]
    requested, expected = config.start_toolsets(resolved)
    assert requested == ["terminal", "file", "no_mcp"]
    assert expected == ["file", "terminal"]
    (tmp_path / ".verksted.yaml").write_text("toolsets: []\n")
    assert config.start_toolsets(config.resolve(str(tmp_path), parent_profile="default")) == (["no_mcp"], [])


def test_fingerprint_is_stable_and_changes_with_the_config(tmp_path):
    a = {"version": 1, "profile": "default", "toolsets": ["file"], "files": []}
    b = dict(a, toolsets=["terminal"])
    assert config.fingerprint(a) == config.fingerprint(dict(reversed(list(a.items()))))
    assert config.fingerprint(a) != config.fingerprint(b)
    assert config.fingerprint(a).startswith("sha256:")


@pytest.mark.parametrize("text, code", [
    ("toolsets: [file, file]\n", "bad_toolsets"),
    ("toolsets: [all]\n", "bad_toolsets"),
    ("toolsets: ['*']\n", "bad_toolsets"),
    ("toolsets: [hermes-cli]\n", "bad_toolsets"),
    ("toolsets: [no_mcp]\n", "bad_toolsets"),
    ("toolsets: file\n", "bad_toolsets"),
    ("toolsets: [3]\n", "bad_toolsets"),
    ("version: 2\n", "bad_config"),
    ("model: x\n", "bad_config"),
    ("- a\n- b\n", "bad_config"),
    ("toolsets: [\n", "bad_config"),
    ("profile: other\n", "profile_switch_unsupported"),
])
def test_bad_configs_are_refused(tmp_path, text, code):
    (tmp_path / ".verksted.yaml").write_text(text)
    with pytest.raises(config.ConfigRefused) as err:
        config.resolve(str(tmp_path), parent_profile="default")
    assert err.value.code == code


def test_naming_the_calling_profile_is_allowed(tmp_path):
    (tmp_path / ".verksted.yaml").write_text("profile: work\n")
    assert config.resolve(str(tmp_path), parent_profile="work")["profile"] == "work"
