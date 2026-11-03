""".verksted.yaml resolution (card k5, docs/architecture.md §2/§3, invariants 7 and 8): the walk
from the workspace root, inheritance and ceilings, strict keys, allowed profiles, the frozen
state with file hashes and fingerprint."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from conftest import REPO
from fakefs import WinFS
from verksted import config

DEMO = REPO / "examples" / "verksted-demo"
SETTINGS = config.SETTINGS_PATH


def resolve(folder, *, settings=None, profiles=None, parent="default", backend=None, **kw):
    extra = {"backend": backend} if backend is not None else {}
    return config.resolve(str(folder), parent_profile=parent, parent_profile_home=f"/homes/{parent}",
                          settings=settings or {}, profile_home=(profiles or {}).get, **extra, **kw)


def refused(folder, code, **kw):
    with pytest.raises(config.ConfigRefused) as err:
        resolve(folder, **kw)
    assert err.value.code == code, str(err.value)
    return str(err.value)


def write(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / config.CONFIG_NAME
    path.write_text(text, encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir()
    return folder


# -- the frozen state ---------------------------------------------------------------------------


def test_the_default_case_resolves_to_the_calling_profile_without_a_list(ws):
    assert resolve(ws) == {
        "version": 1, "workspace": str(ws), "registered": False, "profile": "default",
        "profile_home": "/homes/default", "profile_switched": False, "toolsets": None,
        "ceilings": [], "files": [],
    }


def test_the_files_read_are_frozen_with_their_content_hashes(ws):
    root = write(ws, "version: 1\ntoolsets: [file, web]\n")
    child = write(ws / "step", "toolsets: [file]\n")
    resolved = resolve(ws / "step")
    assert resolved["files"] == [{"path": str(root), "sha256": sha(root)},
                                 {"path": str(child), "sha256": sha(child)}]
    before = config.fingerprint(resolved)
    child.write_text("toolsets: [web]\n")
    assert config.fingerprint(resolve(ws / "step")) != before  # any file of the chain moves it
    child.write_text("toolsets: [file]\n")
    assert config.fingerprint(resolve(ws / "step")) == before


def test_the_fingerprint_is_stable_and_changes_with_the_config():
    a = {"version": 1, "profile": "default", "toolsets": ["file"], "files": []}
    b = dict(a, toolsets=["terminal"])
    assert config.fingerprint(a) == config.fingerprint(dict(reversed(list(a.items()))))
    assert config.fingerprint(a) != config.fingerprint(b)
    assert config.fingerprint(a).startswith("sha256:")


def test_a_list_becomes_the_request_and_the_expectation(ws):
    write(ws, "version: 1\ntoolsets: [terminal, file]\n")
    assert config.start_toolsets(resolve(ws)) == (["terminal", "file", "no_mcp"], ["file", "terminal"])


# -- inheritance and ceilings -------------------------------------------------------------------


def test_a_child_without_a_list_inherits_the_nearest_one(ws):
    root = write(ws, "toolsets: [file, web, clarify]\n")
    write(ws / "a", "version: 1\n")
    resolved = resolve(ws / "a" / "b")
    assert resolved["toolsets"] == ["file", "web", "clarify"]
    assert resolved["ceilings"] == [{"source": str(root), "toolsets": ["file", "web", "clarify"]}]
    assert resolved["workspace"] == str(ws)


def test_a_nearer_list_narrows_and_every_list_above_stays_a_ceiling(ws):
    root = write(ws, "toolsets: [file, web, clarify]\n")
    mid = write(ws / "a", "toolsets: [file, clarify]\n")
    leaf = write(ws / "a" / "b", "toolsets: []\n")
    resolved = resolve(ws / "a" / "b")
    assert resolved["toolsets"] == []
    assert [c["source"] for c in resolved["ceilings"]] == [str(root), str(mid), str(leaf)]
    assert config.start_toolsets(resolved) == (["no_mcp"], [])


@pytest.mark.parametrize("child", ["toolsets: [terminal]\n", "toolsets: [file, terminal]\n"])
def test_a_list_that_widens_a_ceiling_is_refused_not_clipped(ws, child):
    write(ws, "toolsets: [file, web]\n")
    write(ws / "a", child)
    message = refused(ws / "a", "toolsets_widen")
    assert "terminal" in message and str(ws / config.CONFIG_NAME) in message


def test_a_grandchild_cannot_widen_past_its_grandparent(ws):
    write(ws, "toolsets: [file, web]\n")
    write(ws / "a", "version: 1\n")
    write(ws / "a" / "b", "toolsets: [file]\n")
    write(ws / "a" / "b" / "c", "toolsets: [web]\n")  # within the root, beyond its parent
    refused(ws / "a" / "b" / "c", "toolsets_widen")


def test_the_plugin_ceiling_bounds_every_list_and_needs_an_explicit_one(ws):
    settings = {"toolset_ceiling": ["file", "web"]}
    message = refused(ws, "toolsets_required", settings=settings)  # D1 (a): no list under a ceiling
    assert "toolset_ceiling" in message
    write(ws, "toolsets: [web]\n")
    resolved = resolve(ws, settings=settings)
    assert resolved["toolsets"] == ["web"]
    assert resolved["ceilings"][0] == {"source": f"{SETTINGS}.toolset_ceiling", "toolsets": ["file", "web"]}
    write(ws, "toolsets: [web, terminal]\n")
    assert f"{SETTINGS}.toolset_ceiling" in refused(ws, "toolsets_widen", settings=settings)


# -- strict keys and values ---------------------------------------------------------------------


@pytest.mark.parametrize("text, code", [
    ("toolsets: [file, file]\n", "bad_toolsets"),
    ("toolsets: [all]\n", "bad_toolsets"),
    ("toolsets: ['*']\n", "bad_toolsets"),
    ("toolsets: [hermes-cli]\n", "bad_toolsets"),
    ("toolsets: [no_mcp]\n", "bad_toolsets"),
    ("toolsets: [verksted]\n", "bad_toolsets"),
    ("toolsets: [' file']\n", "bad_toolsets"),
    ("toolsets: file\n", "bad_toolsets"),
    ("toolsets: {file: true}\n", "bad_toolsets"),
    ("toolsets: [3]\n", "bad_toolsets"),
    ("toolsets:\n", "bad_config"),
    ("toolsets: null\n", "bad_config"),
    ("profile:\n", "bad_config"),
    ("version: 2\n", "bad_config"),
    ("version: true\n", "bad_config"),
    ("version: '1'\n", "bad_config"),
    ("version: 1.0\n", "bad_config"),
    ("profile: Research\n", "bad_config"),
    ("profile: [a]\n", "bad_config"),
    ("profile: ../x\n", "bad_config"),
    ("toolsets: [file]\ntoolsets: [web]\n", "bad_config"),
    ("api_key: sk-x\n", "bad_config"),
    ("base_url: http://evil\n", "bad_config"),
    ("provider: openrouter\n", "bad_config"),
    ("model: x\n", "bad_config"),
    ("env: {A: b}\n", "bad_config"),
    ("mcp_servers: {x: {}}\n", "bad_config"),
    ("tools: [file]\n", "bad_config"),
    ("- a\n- b\n", "bad_config"),
    ("toolsets: [\n", "bad_config"),
])
def test_bad_configs_are_refused(ws, text, code):
    write(ws, text)
    refused(ws, code)


def test_credentials_and_backend_addresses_are_refused_by_name(ws):
    write(ws, "toolsets: [file]\nbase_url: http://x\napi_key: k\n")
    message = refused(ws, "bad_config")
    assert "api_key" in message and "base_url" in message and "profile" in message


def test_a_bad_file_anywhere_in_the_chain_refuses_the_run(ws):
    write(ws, "provider: x\n")
    write(ws / "a", "toolsets: [file]\n")
    refused(ws / "a", "bad_config")


@pytest.mark.parametrize("make", [
    lambda p: p.mkdir(),                                       # a folder named .verksted.yaml
    lambda p: p.symlink_to(p.parent / "missing.yaml"),          # a dangling link
    lambda p: p.write_bytes(b"toolsets: [file]\n" + b"#" * (64 * 1024)),  # too large
    lambda p: p.write_bytes(b"toolsets: [f\xffile]\n"),         # not UTF-8
])
def test_a_config_that_cannot_be_read_fails_closed(ws, make):
    make(ws / config.CONFIG_NAME)
    refused(ws, "bad_config")


def test_a_byte_order_mark_and_comments_are_fine(ws):
    (ws / config.CONFIG_NAME).write_bytes("\ufeff# just a note\nversion: 1\n".encode("utf-8"))
    assert resolve(ws)["toolsets"] is None


# -- the workspace root -------------------------------------------------------------------------


def test_without_registration_the_topmost_file_is_the_root_and_applies(tmp_path):
    top = write(tmp_path / "top", "toolsets: [file]\n")
    (tmp_path / "top" / "a" / "b").mkdir(parents=True)
    resolved = resolve(tmp_path / "top" / "a" / "b")
    assert resolved["workspace"] == str(tmp_path / "top")
    assert resolved["files"] == [{"path": str(top), "sha256": sha(top)}]


def test_a_registered_workspace_is_required_when_any_is_registered(tmp_path, ws):
    settings = {"workspaces": [str(ws)]}
    (tmp_path / "elsewhere").mkdir()
    message = refused(tmp_path / "elsewhere", "not_in_workspace", settings=settings)
    assert f"{SETTINGS}.workspaces" in message
    (ws / "a").mkdir()
    resolved = resolve(ws / "a", settings=settings)
    assert resolved["workspace"] == str(ws) and resolved["registered"] is True


def test_a_registered_walk_starts_at_the_workspace(tmp_path):
    write(tmp_path, "toolsets: [file]\n")  # above the registered workspace: not part of the walk
    ws = tmp_path / "ws"
    write(ws, "toolsets: [web]\n")
    assert resolve(ws, settings={"workspaces": [str(ws)]})["toolsets"] == ["web"]


def test_the_outermost_registration_wins_so_no_ancestor_is_skipped(ws):
    write(ws, "toolsets: [file]\n")
    inner = ws / "inner"
    write(inner, "toolsets: [file, terminal]\n")
    for order in ([str(inner), str(ws)], [str(ws), str(inner)]):
        refused(inner, "toolsets_widen", settings={"workspaces": order})


def test_a_registration_through_a_symlink_is_the_real_folder(tmp_path, ws):
    link = tmp_path / "ws-link"
    link.symlink_to(ws)
    assert resolve(ws, settings={"workspaces": [str(link)]})["workspace"] == str(ws)


def test_a_registered_workspace_that_is_not_there_is_skipped(tmp_path, ws):
    settings = {"workspaces": [str(tmp_path / "unplugged"), str(ws)]}
    assert resolve(ws, settings=settings)["workspace"] == str(ws)


@pytest.mark.parametrize("settings", [
    {"workspaces": "/x"}, {"workspaces": [""]}, {"workspaces": ["relative"]}, {"workspaces": [7]},
    {"allowed_profiles": "research"}, {"allowed_profiles": ["Research"]}, {"allowed_profiles": ["../x"]},
    {"toolset_ceiling": "file"}, {"toolset_ceiling": ["all"]}, {"toolset_ceiling": ["file", "file"]},
])
def test_malformed_plugin_settings_refuse_every_run(ws, settings):
    refused(ws, "bad_settings", settings=settings)


def test_the_walk_is_bounded(tmp_path):
    deep = tmp_path.joinpath(*["d"] * (config.MAX_DEPTH + 1))
    deep.mkdir(parents=True)
    refused(deep, "folder_too_deep")
    refused(deep, "folder_too_deep", settings={"workspaces": [str(tmp_path)]})


# -- profiles -----------------------------------------------------------------------------------


def test_another_profile_must_be_allowed_and_exist(ws):
    write(ws, "profile: research\n")
    message = refused(ws, "profile_not_allowed")
    assert f"{SETTINGS}.allowed_profiles" in message
    refused(ws, "profile_missing", settings={"allowed_profiles": ["research"]})
    resolved = resolve(ws, settings={"allowed_profiles": ["research"]}, profiles={"research": "/homes/research"})
    assert resolved["profile"] == "research" and resolved["profile_home"] == "/homes/research"
    assert resolved["profile_switched"] is True


def test_the_calling_profile_is_always_allowed(ws):
    write(ws, "profile: work\n")
    resolved = resolve(ws, parent="work")
    assert resolved["profile"] == "work" and resolved["profile_switched"] is False
    assert resolved["profile_home"] == "/homes/work"


def test_a_profile_is_inherited_and_the_nearest_one_wins(ws):
    allowed = {"allowed_profiles": ["research", "writer"]}
    profiles = {"research": "/homes/research", "writer": "/homes/writer"}
    write(ws, "profile: research\ntoolsets: [file, web]\n")
    (ws / "a").mkdir()
    write(ws / "b", "profile: writer\n")
    assert resolve(ws / "a", settings=allowed, profiles=profiles)["profile"] == "research"
    inherited = resolve(ws / "b", settings=allowed, profiles=profiles)
    assert inherited["profile"] == "writer" and inherited["toolsets"] == ["file", "web"]


def test_a_profile_switch_keeps_the_inherited_ceiling(ws):
    write(ws, "toolsets: [file]\n")
    write(ws / "a", "profile: research\ntoolsets: [file, terminal]\n")
    refused(ws / "a", "toolsets_widen", settings={"allowed_profiles": ["research"]},
            profiles={"research": "/homes/research"})


# -- the demo workspace -------------------------------------------------------------------------


@pytest.mark.parametrize("registered", [False, True])
def test_the_demo_workspace_resolves_under_the_new_rules(registered):
    settings = {"workspaces": [str(DEMO)]} if registered else {}
    root = resolve(DEMO, settings=settings)
    research = resolve(DEMO / "workflow" / "10-research", settings=settings)
    writing = resolve(DEMO / "workflow" / "20-write", settings=settings)
    assert root["toolsets"] == ["file", "web", "clarify"]
    assert research["toolsets"] == ["file", "web", "clarify"]  # inherited from the workspace root
    assert writing["toolsets"] == ["file", "clarify"]          # narrowed within the root's ceiling
    assert [c["source"] for c in writing["ceilings"]] == [str(DEMO / config.CONFIG_NAME),
                                                           str(DEMO / "workflow" / "20-write" / config.CONFIG_NAME)]
    assert {r["workspace"] for r in (root, research, writing)} == {str(DEMO)}


# -- a Windows backend, simulated with ntpath — not exercised on Windows --------------------------


def test_win_the_walk_and_the_registration_compare_case_insensitively():
    """Simulated with ntpath — not exercised on Windows."""
    fs = WinFS()
    fs.mkdir("C:\\Work\\Step One")
    fs.write("C:\\Work\\.verksted.yaml", "toolsets: [file, web]\n")
    fs.write("C:\\Work\\Step One\\.verksted.yaml", "toolsets: [file]\n")
    b = fs.backend()
    resolved = resolve("C:\\Work\\Step One", settings={"workspaces": ["c:\\work\\"]}, backend=b)
    assert resolved["workspace"] == "C:\\Work" and resolved["toolsets"] == ["file"]
    assert [f["path"] for f in resolved["files"]] == ["C:\\Work\\.verksted.yaml", "C:\\Work\\Step One\\.verksted.yaml"]
    fs.mkdir("D:\\Other")
    refused("D:\\Other", "not_in_workspace", settings={"workspaces": ["C:\\Work"]}, backend=b)
