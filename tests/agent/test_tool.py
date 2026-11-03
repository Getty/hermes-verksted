"""The verksted tool handler: parent binding, recursion guard, folder lock, wait bound."""

from __future__ import annotations

import json
import sqlite3

import pytest

from fakefs import WinFS
from verksted import paths, tool
from verksted import result as result_mod
from verksted.store import Store

PRINCIPAL = {"subject": "user-1"}
IDENTITY = {"connection_id": "local", "route_profile": "default", "target_profile": "default",
            "profile_id": "/homes/default", "instance_id": "i1"}


class FakeHost:
    """Virtual clock: sleep advances it; the store sees the same wall time."""

    def __init__(self, store_path, *, session_id="parent-sid", session_key="route-parent", chat_id="",
                 profile="default", profile_home="/homes/default"):
        self.t = 10_000.0
        self.store_obj = Store.at_path(store_path, clock=self.now)
        self.store_obj_path = store_path
        self.ctx = dict(session_id=session_id, session_key=session_key, chat_id=chat_id, source="desktop",
                        profile=profile, profile_home=profile_home, backend_platform="linux")
        self.rings = 0
        self.slept = 0.0
        self.store_reads = 0
        self.interrupt_after = None
        self.interrupt_checks = 0
        self.yield_after = None  # a mid-turn user message: Hermes asks the tool thread to yield
        self.on_sleep = None
        self.kwargs_seen = []
        self.plugin_settings = {}  # plugins.entries.verksted.settings of the calling profile
        self.profiles = {}         # live profiles next to the caller: name -> home
        self.path_backend = paths.Backend()

    def caller(self, kwargs):
        self.kwargs_seen.append(dict(kwargs))
        return dict(self.ctx, session_id=kwargs.get("session_id") or self.ctx["session_id"])

    def store(self):
        host = self

        class Counting:
            def __getattr__(self, name):
                attr = getattr(host.store_obj, name)
                if name == "get_run":
                    def counted(*a, **k):
                        host.store_reads += 1
                        return attr(*a, **k)
                    return counted
                return attr
        return Counting()

    def settings(self):
        return dict(self.plugin_settings)

    def profile_home(self, name):
        return self.profiles.get(name)

    def backend(self):
        return self.path_backend

    def ring(self):
        self.rings += 1

    def is_interrupted(self):
        self.interrupt_checks += 1
        return self.interrupt_after is not None and self.slept >= self.interrupt_after

    def yield_requested(self):
        if self.yield_after is not None and self.slept >= self.yield_after:
            self.yield_after = None  # consumed, like tools.interrupt.consume_yield
            return True
        return False

    def sleep(self, s):
        self.t += s
        self.slept += s
        if self.on_sleep:
            self.on_sleep(self)

    def monotonic(self):
        return self.t

    def now(self):
        return self.t


def call(host, args, **kwargs):
    kwargs.setdefault("session_id", host.ctx["session_id"])
    return json.loads(tool.make_handler(host)(args, **kwargs))


@pytest.fixture
def host(tmp_path):
    return FakeHost(tmp_path / "data.db")


def run_now(host, folder, **extra):
    return call(host, {"action": "run", "folder": str(folder), "task": "Fix the README", "wait_seconds": 0, **extra})


def test_run_stores_the_start_and_rings_the_doorbell(host, workspace):
    out = run_now(host, workspace, files=["README.md"])
    assert out["ok"] and out["state"] == "waiting_for_gui" and out["label"] == "wartet auf Hermes Desktop"
    assert out["handle"].startswith("vk-") and host.rings == 1
    run = host.store_obj.get_run(out["handle"])
    assert run["request_id"] == f"{out['handle']}.create"
    assert run["submission_id"] == f"{out['handle']}.submit.1"
    # No title: session.create resolves a title before request_id (k3 fix 1), so only the
    # request_id may decide which session a (repeated) create lands on.
    assert run["create_params"] == {"cwd": str(workspace.resolve()), "cwd_explicit": True,
                                    "request_id": run["request_id"]}
    assert run["prompt_text"].startswith("Fix the README") and "- README.md" in run["prompt_text"]
    assert run["expected_toolsets"] is None and run["resolved_config"]["toolsets"] is None
    assert run["parent_session_id"] == "parent-sid" and run["parent_session_key"] == "route-parent"
    assert run["parent_profile"] == "default" and run["parent_profile_home"] == "/homes/default"


def test_toolsets_from_the_folder_config_go_into_the_create_params(host, workspace):
    (workspace / ".verksted.yaml").write_text("toolsets: [file]\n")
    run = host.store_obj.get_run(run_now(host, workspace)["handle"])
    assert run["create_params"]["toolsets"] == ["file", "no_mcp"]
    assert run["expected_toolsets"] == ["file"]
    assert run["config_fingerprint"].startswith("sha256:")


def test_parent_ids_never_come_from_arguments(host, workspace):
    out = call(host, {"action": "run", "folder": str(workspace), "task": "x", "session_id": "FORGED"})
    assert out["error"]["code"] == "bad_args" and "session_id" in out["error"]["message"]
    out = call(host, {"action": "run", "folder": str(workspace), "task": "x", "profile": "other"})
    assert out["error"]["code"] == "bad_args"
    assert host.store_obj.list_runs() == []


def test_parent_binding_is_checked_on_every_access(host, workspace, tmp_path):
    handle = run_now(host, workspace)["handle"]
    assert call(host, {"action": "status", "handle": handle})["ok"]
    stranger = FakeHost(tmp_path / "data.db", session_id="other-sid", session_key="route-other")
    assert call(stranger, {"action": "status", "handle": handle})["error"]["code"] == "not_found"
    assert call(host, {"action": "status", "handle": "vk-nope"})["error"]["code"] == "not_found"
    other_home = FakeHost(tmp_path / "data.db", profile_home="/homes/other")
    assert call(other_home, {"action": "status", "handle": handle})["error"]["code"] == "not_found"


def test_the_binding_survives_a_compaction(host, workspace, tmp_path):
    handle = run_now(host, workspace)["handle"]
    # Same route key, rotated session id: still the parent.
    rotated = FakeHost(tmp_path / "data.db", session_id="parent-sid-2")
    assert call(rotated, {"action": "status", "handle": handle})["ok"]
    # No route key (a surface without one): follows the post_compaction alias.
    keyless = FakeHost(tmp_path / "keyless.db", session_key="")
    h2 = run_now(keyless, workspace)["handle"]
    moved = FakeHost(tmp_path / "keyless.db", session_id="parent-sid-3", session_key="")
    assert call(moved, {"action": "status", "handle": h2})["error"]["code"] == "not_found"
    keyless.store_obj.add_session_alias("parent-sid", "parent-sid-3")
    assert call(moved, {"action": "status", "handle": h2})["ok"]


def test_no_session_context_is_refused(tmp_path, workspace):
    lonely = FakeHost(tmp_path / "data.db", session_id="", session_key="")
    out = call(lonely, {"action": "run", "folder": str(workspace), "task": "x"}, session_id="")
    assert out["error"]["code"] == "no_session"


def _make_child(host, handle, child_sid):
    s = host.store_obj
    g = s.claim(handle, "w1", principal=PRINCIPAL, lease_s=600)["generation"]
    s.record_step(handle, "w1", g, "create_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
    s.record_step(handle, "w1", g, "session_created", {"identity": IDENTITY, "session_id": child_sid},
                  principal=PRINCIPAL)
    return g


@pytest.mark.parametrize("how", ["session_id", "session_key", "chat_id", "alias"])
def test_a_verksted_child_cannot_start_runs(host, workspace, tmp_path, how):
    handle = run_now(host, workspace)["handle"]
    _make_child(host, handle, "local-child")
    other = tmp_path / "other"
    other.mkdir()
    ctx = {"session_id": "x", "session_key": "y", "chat_id": ""}
    if how == "alias":
        host.store_obj.add_session_alias("local-child", "local-child-rotated")
        ctx["session_id"] = "local-child-rotated"
    else:
        ctx[how] = "local-child"
    child = FakeHost(tmp_path / "data.db", **ctx)
    out = call(child, {"action": "run", "folder": str(other), "task": "again", "wait_seconds": 0},
               session_id=ctx["session_id"])
    assert out["error"]["code"] == "recursion_refused"


def test_a_second_run_in_the_same_folder_is_refused(host, workspace, tmp_path):
    first = run_now(host, workspace)["handle"]
    again = run_now(host, workspace)
    assert again["error"]["code"] == "folder_busy" and again["error"]["handle"] == first
    # Another parent learns that the folder is busy, not which run holds it.
    stranger = FakeHost(tmp_path / "data.db", session_id="s2", session_key="k2")
    out = run_now(stranger, workspace)
    assert out["error"]["code"] == "folder_busy" and "handle" not in out["error"]
    # The same folder through a symlink is the same folder.
    link = tmp_path / "ws-link"
    link.symlink_to(workspace)
    assert run_now(host, link)["error"]["code"] == "folder_busy"


def test_win_one_active_run_per_folder_whatever_the_spelling(host):
    """Simulated with ntpath — not exercised on Windows (k6)."""
    fs = WinFS()
    fs.mkdir("C:\\Work")
    fs.link("C:\\Elsewhere\\into", "C:\\Work")
    host.path_backend = fs.backend()
    first = run_now(host, "C:\\Work")
    run = host.store_obj.get_run(first["handle"])
    assert run["target_dir"] == "C:\\Work" and run["target_key"] == "c:\\work"
    assert run["create_params"]["cwd"] == "C:\\Work"
    for spelling in ("c:\\work\\", "C:/WORK", "\\\\?\\C:\\Work", "C:\\Elsewhere\\into"):
        assert run_now(host, spelling)["error"]["code"] == "folder_busy", spelling


def test_status_shows_the_frozen_config_and_ignores_later_file_changes(host, workspace):
    path = workspace / ".verksted.yaml"
    path.write_text("toolsets: [file, web]\n")
    out = run_now(host, workspace)
    stored = host.store_obj.get_run(out["handle"])
    assert out["config"] == {"workspace": str(workspace), "toolsets": ["file", "web"],
                             "ceilings": [{"source": str(path), "toolsets": ["file", "web"]}],
                             "files": stored["resolved_config"]["files"], "profile_switched": False,
                             "fingerprint": stored["config_fingerprint"]}
    path.write_text("toolsets: [terminal]\n")  # a later edit does not reach a stored run
    status = call(host, {"action": "status", "handle": out["handle"]})
    assert status["config"] == out["config"] and status["toolsets"] == ["file", "web"]
    assert host.store_obj.get_run(out["handle"])["create_params"]["toolsets"] == ["file", "web", "no_mcp"]


def test_a_run_in_an_allowed_profile_keeps_the_parent_store_and_binding(host, workspace):
    (workspace / ".verksted.yaml").write_text("profile: research\ntoolsets: [file]\n")
    host.plugin_settings = {"allowed_profiles": ["research"]}
    assert run_now(host, workspace)["error"]["code"] == "profile_missing"
    host.profiles = {"research": "/homes/research"}
    out = run_now(host, workspace)
    assert out["ok"] and out["profile"] == "research" and out["config"]["profile_switched"] is True
    run = host.store_obj.get_run(out["handle"])  # the parent's store, the parent's binding
    assert run["parent_profile"] == "default" and run["parent_profile_home"] == "/homes/default"
    assert run["resolved_config"]["profile_home"] == "/homes/research"
    assert call(host, {"action": "status", "handle": out["handle"]})["profile"] == "research"


def test_registered_workspaces_come_from_the_plugin_settings(host, workspace, tmp_path):
    host.plugin_settings = {"workspaces": [str(workspace)]}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = run_now(host, elsewhere)
    assert out["error"]["code"] == "not_in_workspace" and host.store_obj.list_runs() == []
    # A link inside the workspace that points out of it is the folder it points to: refused too.
    (workspace / "out").symlink_to(elsewhere)
    assert run_now(host, workspace / "out")["error"]["code"] == "not_in_workspace"
    assert host.store_obj.list_runs() == []
    host.plugin_settings = {"workspaces": "not a list"}
    assert run_now(host, workspace)["error"]["code"] == "bad_settings"


def test_send_and_cancel_check_the_parent_binding(host, workspace, tmp_path):
    handle = run_now(host, workspace)["handle"]
    stranger = FakeHost(tmp_path / "data.db", session_id="other-parent", session_key="route-other")
    for args in ({"action": "cancel", "handle": handle}, {"action": "send", "handle": handle, "task": "x"},
                 {"action": "cancel", "handle": "vk-missing"}):
        assert call(stranger, args)["error"]["code"] == "not_found"
    assert host.store_obj.get_run(handle)["state"] == "waiting_for_gui"


def _to_running(host, handle):
    g = _make_child(host, handle, "local-child")
    s = host.store_obj
    s.record_step(handle, "w1", g, "toolsets_checked", {"identity": IDENTITY}, principal=PRINCIPAL)
    s.record_step(handle, "w1", g, "submit_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
    s.record_step(handle, "w1", g, "running", {"identity": IDENTITY, "admission_id": "adm"}, principal=PRINCIPAL)
    return g


def _finish(host, handle, g, *, outcome="completed", text="done", result=None):
    host.store_obj.record_step(handle, "w1", g, "finished",
                               {"identity": IDENTITY, "outcome": outcome, "final_response": text,
                                **({"result": result} if result else {})}, principal=PRINCIPAL)


def test_the_submitted_prompt_asks_for_the_result_block(host, workspace):
    (workspace / "README.md").write_text("x")
    run = host.store_obj.get_run(run_now(host, workspace, files=["README.md"])["handle"])
    root = run["resolved_config"]["workspace"]
    assert run["prompt_text"].endswith(result_mod.request_text(root))
    assert f"relative to the folder you work in, {workspace.resolve()}" in run["prompt_text"]


def test_cancel_before_the_gui_ends_the_run_at_once(host, workspace):
    handle = run_now(host, workspace)["handle"]
    rings = host.rings
    out = call(host, {"action": "cancel", "handle": handle})
    assert out["cancel"] == "not_started" and out["state"] == "cancelled" and out["label"] == "abgebrochen"
    assert out["reason"] == "cancelled_before_submit" and host.rings == rings  # nobody to ask
    assert "nothing ran" in out["hint"]
    assert run_now(host, workspace)["ok"]  # the folder is free


def test_cancel_of_a_running_turn_is_only_requested_until_hermes_confirms(host, workspace):
    handle = run_now(host, workspace)["handle"]
    g = _to_running(host, handle)
    out = call(host, {"action": "cancel", "handle": handle, "wait_seconds": 0})
    assert out["cancel"] == "requested" and out["state"] == "running" and out["label"] == "wird gestoppt"
    assert out["cancel_requested"] is True and "only when Hermes confirms" in out["hint"]
    assert host.rings == 2

    def hermes_stops(h):
        if h.slept >= 4 and h.store_obj.get_run(handle)["state"] == "running":
            h.store_obj.renew(handle, "w1", g, lease_s=600)
            _finish(h, handle, g, outcome="interrupted", text="Operation interrupted")
    host.on_sleep = hermes_stops
    out = call(host, {"action": "cancel", "handle": handle})
    assert out["cancel"] == "confirmed" and out["state"] == "finished" and out["outcome"] == "interrupted"
    assert "partial" in out["hint"]


def test_a_stop_after_the_turn_finished_is_too_late(host, workspace):
    handle = run_now(host, workspace)["handle"]
    _finish(host, handle, _to_running(host, handle))
    out = call(host, {"action": "cancel", "handle": handle})
    assert out["cancel"] == "too_late" and out["outcome"] == "completed"


def test_send_continues_a_finished_run_in_the_same_session(host, workspace):
    handle = run_now(host, workspace)["handle"]
    _finish(host, handle, _to_running(host, handle), text="first")
    rings = host.rings
    out = call(host, {"action": "send", "handle": handle, "task": "Now the CHANGES file", "wait_seconds": 0})
    assert out["ok"] and out["state"] == "send_waiting" and out["turn"] == 2 and host.rings == rings + 1
    assert out["session_id"] == "local-child" and out["handle"] == handle
    run = host.store_obj.get_run(handle)
    assert run["submission_id"] == f"{handle}.submit.2" and run["task"] == "Now the CHANGES file"
    assert run["prompt_text"].startswith("Now the CHANGES file")
    assert run["prompt_text"].endswith(result_mod.request_text(run["resolved_config"]["workspace"]))
    assert [t["turn"] for t in host.store_obj.turns(handle)] == [1]


def test_send_is_refused_while_the_run_is_active_or_its_outcome_unknown(host, workspace):
    handle = run_now(host, workspace)["handle"]
    assert call(host, {"action": "send", "handle": handle, "task": "x"})["error"]["code"] == "run_active"
    g = _to_running(host, handle)
    out = call(host, {"action": "send", "handle": handle, "task": "x"})
    assert out["error"]["code"] == "run_active" and out["error"]["state"] == "running"
    host.store_obj.record_step(handle, "w1", g, "unknown", {"reason": "admission_unknown"})
    assert call(host, {"action": "send", "handle": handle, "task": "x"})["error"]["code"] == "outcome_unknown"
    assert call(host, {"action": "send", "handle": handle, "task": " "})["error"]["code"] == "bad_args"


def test_an_unknown_outcome_tells_how_to_resolve_it_in_the_child(host, workspace):
    handle = run_now(host, workspace)["handle"]
    g = _to_running(host, handle)
    host.store_obj.record_step(handle, "w1", g, "unknown", {"reason": "admission_unknown"})
    out = call(host, {"action": "status", "handle": handle})
    assert "Session öffnen" in out["hint"] and "discard the lost prompt or send it again" in out["hint"]
    assert "Nothing is retried" in out["hint"]


def test_the_finished_view_reports_the_checked_outputs(host, workspace):
    handle = run_now(host, workspace)["handle"]
    check = {"block": "ok", "summary": "Fixed it.", "root": str(workspace), "truncated": False,
             "outputs": [{"path": f"{workspace}/README.md", "relative": "README.md", "kind": "file", "size": 4,
                          "mtime": 0.0}],
             "missing": [{"path": "ghost.md", "reason": "not_found"}]}
    _finish(host, handle, _to_running(host, handle), text="answer", result=check)
    out = call(host, {"action": "status", "handle": handle})
    assert out["result_block"] == "ok" and out["summary"] == "Fixed it." and out["result"] == "answer"
    assert out["outputs"] == [{"path": f"{workspace}/README.md", "relative": "README.md", "kind": "file",
                               "size": 4, "modified_at": "1970-01-01T00:00:00+00:00"}]
    assert out["missing"] == [{"path": "ghost.md", "reason": "not_found"}]
    assert out["result_truncated"] is False and "result_block_detail" not in out
    assert "not who wrote it" in out["hint"]


def test_a_finished_run_without_a_block_says_so_and_names_no_files(host, workspace):
    handle = run_now(host, workspace)["handle"]
    check = {"block": "unparsable", "detail": "no </verksted-result> after the last <verksted-result>",
             "summary": None, "outputs": [], "missing": []}
    _finish(host, handle, _to_running(host, handle), text="I wrote a.md", result=check)
    out = call(host, {"action": "status", "handle": handle})
    assert out["result_block"] == "unparsable" and out["outputs"] == [] and out["summary"] is None
    assert out["result_block_detail"].startswith("no </verksted-result>")
    assert "Do not guess files" in out["hint"]


def test_unanswered_questions_and_blocked_commands_are_reported(host, workspace):
    handle = run_now(host, workspace)["handle"]
    g = _to_running(host, handle)
    host.store_obj.record_step(handle, "w1", g, "finished",
                               {"identity": IDENTITY, "outcome": "completed", "final_response": "x",
                                "unanswered": [{"question": "Which file?"}],
                                "blocked": [{"command": "rm -rf build", "reason": "timeout"}]},
                               principal=PRINCIPAL)
    out = call(host, {"action": "status", "handle": handle})
    assert out["unanswered"] == [{"question": "Which file?"}]
    assert out["blocked"] == [{"command": "rm -rf build", "reason": "timeout"}]
    assert "got no answer" in out["hint"] and "did not run" in out["hint"]


def test_a_result_recorded_by_schema_1_is_shown_without_a_block(host, workspace):
    handle = run_now(host, workspace)["handle"]
    g = _to_running(host, handle)
    _finish(host, handle, g, text="old answer")
    conn = sqlite3.connect(host.store_obj_path)
    conn.execute("UPDATE runs SET result_ref=? WHERE run_id=?",
                 (json.dumps({"session_id": "s", "admission_id": "a", "final_response": "old answer",
                              "truncated": False}), handle))
    conn.commit()
    conn.close()
    out = call(host, {"action": "status", "handle": handle})
    assert out["result_block"] == "missing" and "earlier Verksted build" in out["result_block_detail"]
    assert out["result"] == "old answer" and out["outputs"] == []


def test_bad_folder_and_config_answer_structured_errors(host, workspace, tmp_path):
    assert call(host, {"action": "run", "folder": "rel", "task": "x"})["error"]["code"] == "bad_folder"
    (workspace / ".verksted.yaml").write_text("profile: elsewhere\n")
    out = call(host, {"action": "run", "folder": str(workspace), "task": "x"})
    assert out["error"]["code"] == "profile_not_allowed"  # k5: switching exists, only to allowed profiles
    assert call(host, {"action": "run", "folder": str(tmp_path), "task": "  "})["error"]["code"] == "bad_args"


def test_wait_is_bounded_at_240_seconds_and_polls_the_interrupt(host, workspace):
    handle = run_now(host, workspace)["handle"]
    start = host.t
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 240})
    waited = host.t - start
    assert out["ok"] and out["wait_end"] == "timeout" and out["still_running"] is True
    assert 240 <= waited <= 240.5
    # The interrupt is checked at least four times a second, the store about once a second.
    assert host.interrupt_checks >= 240 * 4
    assert 200 <= host.store_reads <= 260


def test_a_too_long_wait_is_clamped(host, workspace):
    handle = run_now(host, workspace)["handle"]
    start = host.t
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 10_000})
    assert out["ok"] and host.t - start <= tool.MAX_WAIT_S + 0.5


def test_an_interrupt_ends_the_wait_quickly(host, workspace):
    handle = run_now(host, workspace)["handle"]
    host.interrupt_after = 5.0
    start = host.t
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 240})
    assert out["wait_end"] == "interrupted" and host.t - start <= 5.5


def test_a_mid_turn_user_message_ends_the_wait(host, workspace):
    # k3 fix 9b: Hermes' redirect() asks the tool worker to yield (agent/interrupt_control.py:346-359);
    # the wait gives the turn back instead of parking the user's message behind it.
    handle = run_now(host, workspace)["handle"]
    host.yield_after = 3.0
    start = host.t
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 60})
    assert out["wait_end"] == "yielded" and host.t - start <= 3.5
    assert out["still_running"] and "message" in out["hint"]


def test_a_store_failure_answers_a_structured_error_with_the_handle_once_there_is_one(host, workspace, monkeypatch):
    # k3 fix 9a: a locked or broken store file is an answer, not an exception out of the handler.
    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(host.store_obj, "create_run", locked)
    out = run_now(host, workspace)
    assert out["ok"] is False and out["error"]["code"] == "store_unavailable"
    assert "handle" not in out["error"] and host.store_obj.list_runs() == []
    monkeypatch.undo()

    monkeypatch.setattr(host.store_obj, "get_run", locked)
    out = call(host, {"action": "run", "folder": str(workspace), "task": "x", "wait_seconds": 5})
    [stored] = host.store_obj.list_runs()
    assert out["ok"] is False and out["error"]["code"] == "store_unavailable"
    assert out["error"]["handle"] == stored["run_id"]


def test_run_waits_and_returns_the_finished_result(host, workspace):
    def desktop_part(h):
        runs = h.store_obj.list_runs()
        if not runs or h.slept < 3:
            return
        run = runs[0]
        if run["state"] == "waiting_for_gui":
            g = _make_child(h, run["run_id"], "local-child")
            s = h.store_obj
            s.record_step(run["run_id"], "w1", g, "toolsets_checked", {"identity": IDENTITY}, principal=PRINCIPAL)
            s.record_step(run["run_id"], "w1", g, "submit_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
            s.record_step(run["run_id"], "w1", g, "running", {"identity": IDENTITY, "admission_id": "adm"},
                          principal=PRINCIPAL)
            s.record_step(run["run_id"], "w1", g, "finished",
                          {"identity": IDENTITY, "outcome": "completed", "final_response": "README fixed"},
                          principal=PRINCIPAL)
    host.on_sleep = desktop_part
    out = call(host, {"action": "run", "folder": str(workspace), "task": "Fix it", "wait_seconds": 60})
    assert out["state"] == "finished" and out["wait_end"] == "final" and out["still_running"] is False
    assert out["result"] == "README fixed" and out["outcome"] == "completed" and out["label"] == "fertig"
    assert out["session_id"] == "local-child"


def test_wait_returns_when_the_child_starts_waiting_for_the_user(host, workspace):
    handle = run_now(host, workspace)["handle"]
    g = _make_child(host, handle, "local-child")
    s = host.store_obj
    s.record_step(handle, "w1", g, "toolsets_checked", {"identity": IDENTITY}, principal=PRINCIPAL)
    s.record_step(handle, "w1", g, "submit_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
    s.record_step(handle, "w1", g, "running", {"identity": IDENTITY, "admission_id": "adm"}, principal=PRINCIPAL)

    def ask(h):
        if h.slept >= 10 and not h.store_obj.get_run(handle)["state_detail"].get("prompts"):
            h.store_obj.renew(handle, "w1", g, lease_s=600)
            h.store_obj.record_step(handle, "w1", g, "running",
                                    {"identity": IDENTITY, "receipt_status": "started",
                                     "prompts": [{"kind": "clarify", "prompt_id": "c1", "question": "Which file?"}]},
                                    principal=PRINCIPAL)
    host.on_sleep = ask
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 120})
    assert out["wait_end"] == "needs_user" and out["waiting_for"] == "clarify" and out["label"] == "Rückfrage"
    assert out["prompts"][0]["question"] == "Which file?" and "Session öffnen" in out["hint"]
    # Already waiting when the call starts: the wait does not return at once.
    host.on_sleep = None
    start = host.t
    out = call(host, {"action": "wait", "handle": handle, "wait_seconds": 30})
    assert out["wait_end"] == "timeout" and host.t - start >= 30


def test_status_marks_a_started_run_without_a_live_claim_as_stale(host, workspace):
    handle = run_now(host, workspace)["handle"]
    _make_child(host, handle, "local-child")
    assert call(host, {"action": "status", "handle": handle})["stale"] is False
    host.t += 601
    out = call(host, {"action": "status", "handle": handle})
    assert out["stale"] is True and "veraltet" in out["hint"]


def test_schema_matches_the_handler():
    props = tool.SCHEMA["parameters"]["properties"]
    assert set(props) == tool._ARG_KEYS
    assert props["action"]["enum"] == list(tool.ACTIONS)
    assert props["wait_seconds"]["maximum"] == tool.MAX_WAIT_S == 240
    assert tool.SCHEMA["parameters"]["additionalProperties"] is False
