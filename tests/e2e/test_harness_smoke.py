"""Smoke test of the harness itself. No Verksted code is involved: the plugin under test is the
`e2eprobe` fixture. Every test here is a Linux stand-in (real Hermes backend, faked Desktop renderer).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from conftest import FIXTURES
from vk_e2e.backend import HarnessError
from vk_e2e.desktop import DesktopError, DesktopWindow
from vk_e2e.flows import create_session, submit, wait_terminal
from vk_e2e.model import Clarify, Final, Call, Terminal

PROBE = {"e2eprobe": FIXTURES / "e2eprobe"}


@pytest.fixture
def env(shared_e2e_factory):
    """One backend for the whole module (fast); each test gets clean model rules and closes its clients."""
    e = shared_e2e_factory(PROBE)
    e.model.rules.clear()
    e.model.requests.clear()
    yield e
    e.close_clients()


@pytest.fixture
def win(env):
    return env.window("e2eprobe")


def test_backend_speaks_canonical_protocol(env, win):
    rt = win.request_profile("runtime.describe")
    assert "durable-admission-v1" in rt["capabilities"]
    assert "tool" in rt["session_create"]["sources"]
    assert rt["profile_id"] == str(env.backend.home), "must be the throwaway home, not ~/.hermes"
    assert env.backend.home.is_relative_to(env.backend.root)
    assert win.state()["clarifyBatch"] is True, "window advertised client.capabilities after gateway.ready"


def test_host_shim_route_is_a_full_descriptor(win):
    deadline = time.monotonic() + 5
    while "routes" not in win.state()["storage"] and time.monotonic() < deadline:
        time.sleep(0.1)
    st = win.state()
    assert st["loaded"] and st["registrations"][0]["id"] == "e2eprobe:status"
    # profileRoutes() is the whole descriptor, not just a profile name (docs/hermes-interfaces.md §1)
    assert st["storage"]["routes"] == [{"connectionId": "local", "mode": "local", "primary": True,
                                        "profile": "default", "targetProfile": "default"}]


def test_plugin_api_requires_native_auth_and_sees_the_principal(env, win):
    status, body = env.backend.http("GET", "/api/plugins/e2eprobe/who", ticket=False)
    assert status == 401, body
    status, body = env.backend.http("GET", "/api/plugins/e2eprobe/who")
    assert status == 200 and body["principal"]["profile_id"] == str(env.backend.home)
    assert body["principal"]["capabilities"] == ["http:owner"]
    via_ctx = win.rest("/who")
    assert via_ctx["principal"]["instance_id"] == env.backend.endpoint["instance_id"]
    status, _ = env.backend.http("GET", "/api/plugins/nope/who")
    assert status == 404


def test_session_roundtrip_through_the_canonical_client(env, win):
    env.model.when("SAY_HI", Final("hello from the scripted model"))
    created = create_session(win, request_id="smoke-1", cwd=str(env.work), toolsets=["file"])
    sid = created["session_id"]
    # Canonical client rewrite (k1 §1a): `source: "tool"` never reaches the gateway.
    wire = win.rpc_calls("session.create")[0]["params_wire"]
    assert wire["source"] == "gui" and wire["request_id"] == "smoke-1"
    assert created["info"]["cwd"] == str(env.work)
    adm = submit(win, sid, "SAY_HI please", "sub-1")
    assert adm["status"] == "queued", "accepted is not done"
    again = submit(win, sid, "SAY_HI please", "sub-1")
    assert again["admission_id"] == adm["admission_id"], "same submission_id + text = same admission"
    receipt = wait_terminal(win, sid, adm["admission_id"])
    assert receipt["outcome"] == "completed"
    assert receipt["result"]["final_response"] == "hello from the scripted model"
    # create is idempotent on request_id (k1 C2)
    assert create_session(win, request_id="smoke-1", cwd=str(env.work), toolsets=["file"])["session_id"] == sid
    assert len(win.rpc_calls("session.create")) == 2


def test_resume_is_implicit_on_a_new_window(env):
    """A second window (no attach) submitting to the first window's session: the client resumes first."""
    env.model.when("SAY_HI", Final("ok"))
    a, b = env.window("e2eprobe", name="a"), env.window("e2eprobe", name="b")
    sid = create_session(a, request_id="smoke-2", cwd=str(env.work))["session_id"]
    adm = submit(b, sid, "SAY_HI", "sub-2")
    assert [c["method"] for c in b.rpc_calls()][:2] == ["session.resume", "prompt.submit"]
    assert wait_terminal(b, sid, adm["admission_id"])["outcome"] == "completed"


def test_plugin_event_reaches_the_desktop_part_and_the_interval_runs(env, win):
    """tool handler -> broadcast_plugin_event -> primary socket -> ctx.onEvent -> ctx.rest -> plugin API."""
    env.model.when("PING", Call("e2e_ping", {"note": "hello", "session_id": "FORGED"}), Final("pinged"))
    sid = create_session(win, request_id="smoke-3", cwd=str(env.work))["session_id"]
    adm = submit(win, sid, "PING now", "sub-3")
    ev = win.wait_event("plugin.e2eprobe.ping", match=lambda e: e["payload"]["note"] == "hello")
    assert ev["session_id"] in ("", None)
    assert wait_terminal(win, sid, adm["admission_id"])["outcome"] == "completed"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        store = env.backend.http("GET", "/api/plugins/e2eprobe/store")[1]
        if store["pongs"]:
            break
        time.sleep(0.2)
    assert {"type": "plugin.e2eprobe.ping", "note": "hello"} in store["pongs"]
    assert "e2e_ping" in env.model.requests[0]["tools"], "harness default: plugin tools are offered by name"
    # the tool saw the real calling session, not the forged argument (k1 C17)
    tool_out = [r["last_tool"] for r in env.model.requests if r["round"] == 1][0]
    assert '"kwargs_session_id": "FORGED"' not in tool_out and '"args_session_id": "FORGED"' in tool_out
    assert sid in tool_out or '"kwargs_session_id": "' in tool_out
    # manual interval mode: nothing ran until tick()
    assert win.state()["intervals"][0]["runs"] == 0
    before = env.backend.http("GET", "/api/plugins/e2eprobe/store")[1]["ticks"]
    win.tick()
    assert env.backend.http("GET", "/api/plugins/e2eprobe/store")[1]["ticks"] == before + 1


def test_clarify_is_answered_by_the_user_not_the_plugin(env, win):
    env.model.when("ASK_COLOR", Clarify("Which color?"), Final(lambda c: "user said: " + c.last_tool))
    sid = create_session(win, request_id="smoke-4", cwd=str(env.work))["session_id"]
    user = env.user()
    user.open_chat(sid)  # 'Session öffnen'
    adm = submit(win, sid, "ASK_COLOR", "sub-4")
    prompt = user.wait_prompt(sid, "clarify")
    answered = user.answer_clarify(sid, "teal")
    receipt = wait_terminal(win, sid, adm["admission_id"])
    assert receipt["outcome"] == "completed"
    assert "teal" in receipt["result"]["final_response"], (prompt["via"], receipt)
    # nothing in the window's own call log answered it
    assert not [c for c in win.rpc_calls() if c["method"] in ("clarify.respond", "clarify.lock", "approval.respond")]
    assert not [r for r in win.state()["serverRequests"] if r["answered"]], "the window's own side must not answer"


def _guarded_write(path: Path) -> str:
    # `chmod -R 777` is on the dangerous list (approval prompt); the write after it proves execution.
    return f"chmod -R 777 '{path.parent}' ; echo executed > '{path}'"


def test_approval_denied_by_the_user_does_not_execute(env, win):
    target = env.work / "denied.txt"
    env.model.when("WRITE_IT", Terminal(_guarded_write(target)), Final(lambda c: "tool said: " + c.last_tool))
    sid = create_session(win, request_id="smoke-5", cwd=str(env.work))["session_id"]
    user = env.user()
    user.open_chat(sid)
    adm = submit(win, sid, "WRITE_IT", "sub-5")
    user.deny_approval(sid)
    receipt = wait_terminal(win, sid, adm["admission_id"])
    assert receipt["outcome"] == "completed", "a denied approval still ends completed (k1 C8)"
    assert not target.exists(), "the denied command must not have run"
    assert "denied" in receipt["result"]["final_response"].lower() or "blocked" in receipt["result"]["final_response"].lower()


def test_control_run_approved_command_does_execute(env, win):
    """Control for the denial test: the same command, approved, writes the file. Without this the
    denial test could pass because the command can never run in this harness."""
    target = env.work / "approved.txt"
    env.model.when("WRITE_IT", Terminal(_guarded_write(target)), Final("written"))
    sid = create_session(win, request_id="smoke-6", cwd=str(env.work))["session_id"]
    user = env.user()
    user.open_chat(sid)
    adm = submit(win, sid, "WRITE_IT", "sub-6")
    user.approve_once(sid)
    assert wait_terminal(win, sid, adm["admission_id"])["outcome"] == "completed"
    assert target.read_text().strip() == "executed"


def test_lost_reply_injection_executes_but_hides_the_answer(env, win):
    """Fault injection used by phase 2: the gateway executed the create, the caller saw a timeout."""
    win.inject("session.create", "lose_reply")
    with pytest.raises(DesktopError, match="timed out"):
        create_session(win, request_id="smoke-7", cwd=str(env.work))
    sid = create_session(win, request_id="smoke-7", cwd=str(env.work))["session_id"]
    assert sid.startswith("local-")
    assert len(win.rpc_calls("session.create")) == 2


def test_shim_enforces_the_plugin_import_rule(env, tmp_path):
    bad = tmp_path / "plugin.js"
    bad.write_text("import fs from 'node:fs'\nexport default { id: 'x', register() {} }\n")
    with pytest.raises(HarnessError, match="may import only"):
        DesktopWindow(env.backend, "x", tmp_path, plugin_file=bad, name="bad1")
    bad.write_text("import { notInTheSdk } from '@hermes/plugin-sdk'\nexport default { id: 'x', register() {} }\n")
    with pytest.raises(HarnessError, match="not found in the pinned"):
        DesktopWindow(env.backend, "x", tmp_path, plugin_file=bad, name="bad2")


def test_unknown_host_member_fails_loudly(env, tmp_path):
    p = tmp_path / "plugin.js"
    p.write_text("import { host } from '@hermes/plugin-sdk'\nexport default { id: 'y', register() { host.noSuchThing() } }\n")
    with pytest.raises(HarnessError, match="host.noSuchThing is not implemented"):
        DesktopWindow(env.backend, "y", tmp_path, plugin_file=p, name="bad3")


def test_clarify_with_no_viewer_attached_is_lost_not_parked(env, win):
    """Characterization of the pinned Hermes, relevant to GUI-reconnect scenarios. The Desktop client adds
    `clarify_batch: true` to every prompt.submit, so a Rückfrage is a server->client request, and with no
    attached viewer the owner does not park it: the clarify tool gets 'unanswered' and the turn completes.
    (k1 §3 describes the per-question `clarify.request` event path, which parks until timeout; that is NOT the
    path a Desktop-submitted turn takes.)"""
    env.model.when("ASK_LOST", Clarify("Which?"), Final(lambda c: "tool said: " + c.last_tool))
    sid = create_session(win, request_id="smoke-lost", cwd=str(env.work))["session_id"]
    assert win.rpc_calls("session.create") and win.state()["clarifyBatch"] is True
    adm = submit(win, sid, "ASK_LOST", "sub-lost")
    assert win.rpc_calls("prompt.submit")[-1]["params_wire"]["clarify_batch"] is True
    win.disconnect()  # the window is gone before the question is asked
    time.sleep(3)
    win.reconnect()
    receipt = wait_terminal(win, sid, adm["admission_id"])
    assert receipt["outcome"] == "completed"
    assert '"status": "unanswered"' in receipt["result"]["final_response"]


def test_plugin_tool_is_deferred_behind_tool_search_by_default(e2e):
    """Characterization: with Hermes' default `tools.tool_search.enabled: auto` a plugin tool is NOT in the
    model-facing tool array (the model reaches it through tool_search/tool_describe/tool_call). The harness
    defaults to "off" so scripted models can call tools by name; this test pins why."""
    env = e2e(PROBE, tool_search="auto").start()
    env.model.when("HELLO", Final("hi"))
    win = env.window("e2eprobe")
    sid = create_session(win, request_id="smoke-ts", cwd=str(env.work))["session_id"]
    wait_terminal(win, sid, submit(win, sid, "HELLO", "sub-ts")["admission_id"])
    offered = env.model.requests[0]["tools"]
    assert "e2e_ping" not in offered and "tool_search" in offered


def test_late_viewer_finds_the_open_rueckfrage_in_open_requests(env, win):
    """'Session öffnen' after the question was asked: the new viewer sees the Rückfrage only in the resume
    snapshot's open_requests (prompts[] is empty for a batch clarify) and can answer it."""
    env.model.when("ASK_LATE", Clarify("Which one?"), Final(lambda c: "tool said: " + c.last_tool))
    sid = create_session(win, request_id="smoke-late", cwd=str(env.work))["session_id"]
    adm = submit(win, sid, "ASK_LATE", "sub-late")
    deadline = time.monotonic() + 60
    while not [r for r in win.state()["serverRequests"] if r["method"] == "clarify"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert win.state()["serverRequests"], "the window's own socket received the question"
    user = env.user()
    snap = user.open_chat(sid)
    assert snap["prompts"] == [] and [r["method"] for r in snap["open_requests"]] == ["clarify"]
    assert user.answer_clarify(sid, "late answer")["via"] == "open_requests"
    receipt = wait_terminal(win, sid, adm["admission_id"])
    assert "late answer" in receipt["result"]["final_response"]
