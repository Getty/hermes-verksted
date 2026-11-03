"""k4 phase 2: the real `verksted` package (agent tool + plugin API + Desktop part) on the Linux harness.

EVERY test here is a LINUX STAND-IN: real Hermes backend at the pinned revision, real agent plugin and
plugin API, the real desktop/plugin.js - but the Desktop renderer is the Node shim (tests/e2e/shim/window.mjs).
None of them is "Desktop tested"; docs/windows-acceptance.md holds the scenarios the maintainer runs on Windows.
Time is driven by `pump()`, which fires the Desktop part's interval callbacks (manual interval mode).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from conftest import FIXTURES
from vk_e2e.backend import REPO, HarnessError
from vk_e2e.flows import create_session, submit, wait_terminal
from vk_e2e.model import Call, Clarify, Final, Terminal, WriteFile

PLUGINS = {"verksted": REPO / "verksted", "e2eprobe": FIXTURES / "e2eprobe"}
FORBIDDEN = {"approval.respond", "clarify.respond", "clarify.lock", "prompt.cancel", "session.interrupt"}
ALLOWED = {"runtime.describe", "session.create", "session.info", "session.resume", "tools.show", "prompt.submit",
           "prompt.receipt"}


@pytest.fixture
def env(shared_e2e_factory):
    e = shared_e2e_factory(PLUGINS)
    e.model.rules.clear()
    e.model.requests.clear()
    yield e
    e.close_clients()


def pump(win, cond, timeout=150, what="condition"):
    """Fire the Desktop part's timers (poll / renew / track) until cond() is truthy."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        win.tick()
        got = cond()
        if got:
            return got
        time.sleep(1)
    raise HarnessError(f"timed out waiting for {what}; window errors: {win.state()['errors']}")


def runs(win) -> list[dict]:
    return win.rest("/runs")["runs"]


def run_by_state(win, handle, *states):
    """The run `handle` (a run_id), once it is in one of `states`. Runs of earlier tests share the store."""
    def look():
        hit = [r for r in runs(win) if r["run_id"] == handle and r["state"] in states]
        return hit[0] if hit else None
    return look


def parent_turn(env, win, text, sub):
    sid = create_session(win, request_id=f"parent-{sub}", cwd=str(env.work))["session_id"]
    adm = submit(win, sid, text, f"sub-{sub}")
    return sid, adm


def tool_result(receipt) -> dict:
    """The parent scripted model answers 'RESULT:<tool json>'."""
    text = receipt["result"]["final_response"]
    assert text.startswith("RESULT:"), text
    return json.loads(text[len("RESULT:"):])


def folder_for(env, name) -> Path:
    f = env.work / f"Ärger {name}"  # space and umlaut
    f.mkdir(exist_ok=True)
    return f


def store_rows(env, sql, *args):
    db = env.backend.home / "plugin-data" / "verksted" / "data.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def parent_run(env, tag, folder, task, *, wait=100):
    env.model.when(f"PARENT_{tag}", Call("verksted", {"action": "run", "folder": str(folder), "task": task,
                                                      "wait_seconds": wait}),
                   Final(lambda c: "RESULT:" + c.last_tool))


def sent_methods(win):
    return [c["method"] for c in win.rpc_calls()]


def assert_never_answers(win):
    methods = set(sent_methods(win))
    assert not methods & FORBIDDEN, f"the Desktop part sent {methods & FORBIDDEN}"
    assert methods <= ALLOWED | {"client.capabilities"}, methods - ALLOWED


# 1 ------------------------------------------------------------------------------------------------------
def test_1_happy_path(env):
    win = env.window("verksted")
    folder = folder_for(env, "happy")
    env.model.when("CHILD_HAPPY", WriteFile(str(folder / "result.txt"), "made by the child"),
                   Final("child finished: result.txt written"))
    parent_run(env, "HAPPY", folder, "CHILD_HAPPY write result.txt")
    psid, adm = parent_turn(env, win, "PARENT_HAPPY go", "happy")
    receipt = pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                          include_result=True))["status"] == "terminal" and r,
                   what="parent turn")
    out = tool_result(receipt)
    assert out["ok"] and out["state"] == "finished" and out["outcome"] == "completed", out
    assert "child finished" in out["result"]
    assert (folder / "result.txt").read_text() == "made by the child"
    child = out["session_id"]
    # parent binding is the REAL parent session, not an argument and not the child
    rows = store_rows(env, "SELECT parent_session_id, target_dir, session_id FROM runs WHERE run_id=?", out["handle"])
    assert rows[0]["parent_session_id"] == psid and rows[0]["session_id"] == child != psid
    assert rows[0]["target_dir"] == str(folder)
    # start order and rewrites (docs/hermes-interfaces.md §1a, k3 decision 6)
    mine = [c for c in win.rpc_calls() if c["params_in"].get("cwd") == str(folder) or c["params_in"].get("session_id") == child]
    assert [c["method"] for c in mine][:4] == ["session.create", "tools.show", "session.info", "prompt.submit"]
    create = mine[0]
    assert "source" not in create["params_in"] and create["params_wire"]["source"] == "gui"
    assert create["params_wire"]["request_id"].endswith(".create") and create["params_wire"]["cwd_explicit"] is True
    assert win.request_profile("session.info", session_id=child)["source"] == "gui"
    assert_never_answers(win)


# 2 ------------------------------------------------------------------------------------------------------
def test_2_rueckfrage_answered_by_the_user(env):
    win = env.window("verksted")
    folder = folder_for(env, "ask")
    env.model.when("CHILD_ASK", Clarify("What is the project called?"), Final(lambda c: "named: " + c.last_tool))
    parent_run(env, "ASK", folder, "CHILD_ASK find out the name")
    psid, adm = parent_turn(env, win, "PARENT_ASK go", "ask")
    receipt = pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                          include_result=True))["status"] == "terminal" and r,
                   what="parent turn (returns when the child needs the user)")
    out = tool_result(receipt)
    assert out["state"] == "running" and out["label"] == "Rückfrage" and out["waiting_for"] == "clarify", out
    child, handle = out["session_id"], out["handle"]
    # the Desktop UI shows it and 'Session öffnen' opens the child on the stored route
    assert "Rückfrage" in win.render("status")["text"]
    page = win.render("page")
    assert "Rückfrage" in page["text"] and "Session öffnen" in page["buttons"]
    win.click("page", "Session öffnen")
    call = win.state()["openSessionCalls"][-1]
    assert call["storedSessionId"] == child
    assert call["options"] == {"route": {"connectionId": "local", "mode": "local", "primary": True,
                                         "profile": "default", "targetProfile": "default"}, "intent": "tab"}
    # the USER answers in the native chat of the child
    user = env.user()
    user.open_chat(child)
    user.answer_clarify(child, "Anna")
    done = pump(win, run_by_state(win, handle, "finished"), what="run finished")
    assert done["run_id"] == handle and done["outcome"] == "completed" and "Anna" in done["result_preview"]
    assert_never_answers(win)
    assert not [r for r in win.state()["serverRequests"] if r["answered"]]


# 3 ------------------------------------------------------------------------------------------------------
def test_3_denied_approval_does_not_execute(env):
    win = env.window("verksted")
    folder = folder_for(env, "deny")
    target = folder / "denied.txt"
    # `chmod -R 777` is on Hermes' dangerous list (approval prompt); the write after it proves execution.
    env.model.when("CHILD_DENY", Terminal(f"chmod -R 777 '{folder}' ; echo WROTE > '{target}'"),
                   Final(lambda c: "tool said: " + c.last_tool))
    parent_run(env, "DENY", folder, "CHILD_DENY write denied.txt")
    psid, adm = parent_turn(env, win, "PARENT_DENY go", "deny")
    receipt = pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                          include_result=True))["status"] == "terminal" and r,
                   what="parent turn")
    out = tool_result(receipt)
    assert out["state"] == "running" and out["label"] == "Freigabe erforderlich" and out["waiting_for"] == "approval", out
    assert "Freigabe erforderlich" in win.render("status")["text"]
    child, handle = out["session_id"], out["handle"]
    user = env.user()
    user.open_chat(child)
    user.deny_approval(child)
    done = pump(win, run_by_state(win, handle, "finished"), what="run finished")
    assert not target.exists(), "the denied command must not have run"
    assert done["outcome"] == "completed", "k1 C8: a denied approval still ends completed"
    low = done["result_preview"].lower()
    assert ("denied" in low or "blocked" in low) and "wrote" not in low, done["result_preview"]
    # the parent can fetch the same result
    env.model.when("STATUS_DENY", Call("verksted", lambda c: {"action": "status", "handle": c.user.split()[-1]}),
                   Final(lambda c: "RESULT:" + c.last_tool))
    a2 = submit(win, psid, f"STATUS_DENY {out['handle']}", "sub-deny2")  # same parent session
    again = tool_result(wait_terminal(win, psid, a2["admission_id"]))
    assert again["state"] == "finished" and again["outcome"] == "completed" and not target.exists()
    assert_never_answers(win)


# 4 ------------------------------------------------------------------------------------------------------
def test_4_result_reaches_the_same_parent_after_a_new_window(env):
    win = env.window("verksted", name="first")
    folder = folder_for(env, "resume")
    env.model.when("CHILD_RES", Final("resumed result"))
    parent_run(env, "RES", folder, "CHILD_RES answer")
    psid, adm = parent_turn(env, win, "PARENT_RES go", "res")
    out = tool_result(pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                                include_result=True))["status"] == "terminal" and r))
    assert out["state"] == "finished"
    win.close()
    fresh = env.window("verksted", name="fresh")
    for _ in range(3):
        fresh.tick()
        time.sleep(1)
    methods = sent_methods(fresh)
    assert "session.create" not in methods and "prompt.submit" not in methods, methods
    # the same parent session still gets the result, via status with its handle
    env.model.when("STATUS_RES", Call("verksted", lambda c: {"action": "status", "handle": c.user.split()[-1]}),
                   Final(lambda c: "RESULT:" + c.last_tool))
    adm2 = submit(fresh, psid, f"STATUS_RES {out['handle']}", "sub-res2")
    again = tool_result(wait_terminal(fresh, psid, adm2["admission_id"]))
    assert again["state"] == "finished" and again["result"] == out["result"] == "resumed result"
    # a handle alone grants nothing: another parent session cannot read it
    env.model.when("STATUS_FOREIGN", Call("verksted", lambda c: {"action": "status", "handle": c.user.split()[-1]}),
                   Final(lambda c: "RESULT:" + c.last_tool))
    other, a3 = parent_turn(env, fresh, f"STATUS_FOREIGN {out['handle']}", "res3")
    foreign = tool_result(wait_terminal(fresh, other, a3["admission_id"]))
    assert foreign["ok"] is False and foreign["error"]["code"] == "not_found"


def test_4b_takeover_of_a_running_run_reconciles_without_a_second_start(env):
    """Window A starts the run and goes away mid-turn; its lease is expired (simulated by writing the
    store, a real lease is 180 s); window B reconciles from receipts. No second create / submit."""
    a = env.window("verksted", name="a")
    folder = folder_for(env, "takeover")
    env.model.when("CHILD_TAKE", Final("finished after takeover", gate="take"))
    parent_run(env, "TAKE", folder, "CHILD_TAKE answer", wait=200)
    psid, adm = parent_turn(env, a, "PARENT_TAKE go", "take")
    handle = pump(a, lambda: (store_rows(env, "SELECT run_id FROM runs WHERE target_dir=? AND state='running'", str(folder)) or [{}])[0].get("run_id"),
                  what="run running")
    assert env.model.gate_hit("take")
    creates, submits = len(a.rpc_calls("session.create")) - 1, len(a.rpc_calls("prompt.submit")) - 1  # minus the parent's own
    assert (creates, submits) == (1, 1)
    child = run_by_state(a, handle, "running")()["session_id"]
    a.close()
    now = time.time()
    db = env.backend.home / "plugin-data" / "verksted" / "data.db"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET claim_expires_at=? WHERE run_id=?", (now - 1, handle))
    conn.commit()
    conn.close()
    b = env.window("verksted", name="b")
    env.model.release("take")
    pump(b, run_by_state(b, handle, "finished"), what="run finished under window B")
    assert "session.create" not in sent_methods(b) and "prompt.submit" not in sent_methods(b), sent_methods(b)
    final = run_by_state(b, handle, "finished")()
    assert final["outcome"] == "completed" and "after takeover" in final["result_preview"]
    # the parent's blocking wait (still inside the original tool call) got the result
    out = tool_result(wait_terminal(b, psid, adm["admission_id"]))
    assert out["state"] == "finished" and out["session_id"] == child


# 5 ------------------------------------------------------------------------------------------------------
def test_5_toolset_exceeding_the_listed_set_is_refused_before_submit(env):
    win = env.window("verksted")
    folder = folder_for(env, "toolsets-refused")
    (folder / ".verksted.yaml").write_text("version: 1\ntoolsets: [file]\n", encoding="utf-8")
    env.model.when("CHILD_TS", Final("must never run"))
    parent_run(env, "TS", folder, "CHILD_TS answer", wait=60)
    psid, adm = parent_turn(env, win, "PARENT_TS go", "ts")
    out = tool_result(pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                                include_result=True))["status"] == "terminal" and r))
    assert out["state"] == "refused", out
    assert out["reason"] == "toolsets_exceeded" and "e2eprobe" in out["extra"], out
    child_calls = [c for c in win.rpc_calls() if c["params_in"].get("cwd") == str(folder)]
    assert child_calls, "the child session was created"
    assert [c for c in win.rpc_calls("prompt.submit") if "must never run" in str(c["params_in"].get("text", ""))] == []
    assert not [r for r in env.model.requests if "CHILD_TS" in r["user"]], "the child model was never asked"


def test_5b_listing_the_extra_toolset_passes(env):
    win = env.window("verksted")
    folder = folder_for(env, "toolsets-ok")
    (folder / ".verksted.yaml").write_text("version: 1\ntoolsets: [file, e2eprobe]\n", encoding="utf-8")
    env.model.when("CHILD_TS2", WriteFile(str(folder / "ok.txt"), "ok"), Final("allowed run done"))
    parent_run(env, "TS2", folder, "CHILD_TS2 write ok.txt")
    psid, adm = parent_turn(env, win, "PARENT_TS2 go", "ts2")
    out = tool_result(pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                                include_result=True))["status"] == "terminal" and r))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert (folder / "ok.txt").exists()
    assert set(out["effective_toolsets"]) >= {"file", "e2eprobe"}


# 6 ------------------------------------------------------------------------------------------------------
def test_6_route_without_durable_admission_is_refused_before_create(env):
    win = env.window("verksted")
    # strip the capability from the next runtime.describe reply, as a gateway that lacks it would answer
    caps = [c for c in win.request_profile("runtime.describe")["capabilities"] if c != "durable-admission-v1"]
    win.inject("runtime.describe", "merge_result", times=1, merge={"capabilities": caps})
    folder = folder_for(env, "gate")
    env.model.when("CHILD_GATE", Final("must never run"))
    parent_run(env, "GATE", folder, "CHILD_GATE answer", wait=60)
    psid, adm = parent_turn(env, win, "PARENT_GATE go", "gate")
    out = tool_result(pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                                include_result=True))["status"] == "terminal" and r))
    assert out["state"] == "refused" and out["reason"] == "legacy_route", out
    assert not [c for c in win.rpc_calls("session.create") if c["params_in"].get("cwd") == str(folder)]
    assert not [r for r in env.model.requests if "CHILD_GATE" in r["user"]]


# 7 ------------------------------------------------------------------------------------------------------
def test_7_lost_create_reply_repeats_the_identical_call(env):
    win = env.window("verksted")
    folder = folder_for(env, "lost")
    env.model.when("CHILD_LOST", Final("created once"))
    parent_run(env, "LOST", folder, "CHILD_LOST answer")
    psid = create_session(win, request_id="parent-lost", cwd=str(env.work))["session_id"]
    win.inject("session.create", "lose_reply", times=1)  # the plugin's create is executed, its answer lost
    adm = submit(win, psid, "PARENT_LOST go", "sub-lost")
    out = tool_result(pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                                include_result=True))["status"] == "terminal" and r))
    assert out["state"] == "finished" and out["result"] == "created once", out
    creates = [c for c in win.rpc_calls("session.create") if c["params_in"].get("cwd") == str(folder)]
    assert len(creates) == 2 and creates[0]["injected"] == "lose_reply"
    assert creates[0]["params_wire"] == creates[1]["params_wire"], "identical call, same request_id"
    assert creates[1]["result_session_id"] == out["session_id"]
    assert len(win.rpc_calls("prompt.submit")) == 2  # the parent's and exactly one for the child
    assert store_rows(env, "SELECT COUNT(*) AS n FROM runs WHERE target_dir=?", str(folder))[0]["n"] == 1


# tool_search path (default config) ----------------------------------------------------------------------
def test_8_default_config_reaches_verksted_through_tool_search(e2e):
    env = e2e(PLUGINS, tool_search="auto").start()
    win = env.window("verksted")
    folder = folder_for(env, "toolsearch")
    env.model.when("CHILD_TSE", Final("via tool_search"))
    args = {"action": "run", "folder": str(folder), "task": "CHILD_TSE answer", "wait_seconds": 100}
    env.model.when("PARENT_TSE", Call("tool_search", {"queries": ["verksted run folder task"]}),
                   Call("tool_call", {"calls": [{"name": "verksted", "arguments": args}]}),
                   Final(lambda c: "RESULT:" + c.last_tool))
    psid, adm = parent_turn(env, win, "PARENT_TSE go", "tse")
    receipt = pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                          include_result=True))["status"] == "terminal" and r)
    offered = env.model.requests[0]["tools"]
    assert "verksted" not in offered and "tool_search" in offered
    text = receipt["result"]["final_response"]
    payload = json.loads(text[len("RESULT:"):])
    # tool_call may wrap the underlying result; find the run result inside it
    blob = json.dumps(payload)
    assert '"state": "finished"' in blob.replace('\\"', '"'), blob[:400]
    assert "via tool_search" in blob
    rows = store_rows(env, "SELECT parent_session_id FROM runs")
    assert rows and rows[0]["parent_session_id"] == psid, "parent binding survives the tool_call bridge"


# k3 fix pass --------------------------------------------------------------------------------------------
def _terminal(win, psid, adm):
    return pump(win, lambda: (r := win.request_profile("prompt.receipt", session_id=psid, admission_id=adm["admission_id"],
                                                       include_result=True))["status"] == "terminal" and r)


def test_9_the_same_folder_name_in_two_parents_gets_two_sessions(env):
    # k3 fix 1: a titled session.create resolves the title before request_id, so the second
    # "Verksted · Ärger dup" landed on the first run's session.
    win = env.window("verksted")
    folders = []
    for parent in ("eins", "zwei"):
        f = env.work / parent / "Ärger dup"
        f.mkdir(parents=True, exist_ok=True)
        folders.append(f)
    outs = []
    for i, folder in enumerate(folders):
        env.model.when(f"CHILD_DUP{i}", Final(f"dup {i} done"))
        parent_run(env, f"DUP{i}", folder, f"CHILD_DUP{i} answer")
        psid, adm = parent_turn(env, win, f"PARENT_DUP{i} go", f"dup{i}")
        outs.append(tool_result(_terminal(win, psid, adm)))
    assert [o["state"] for o in outs] == ["finished", "finished"], outs
    assert outs[0]["session_id"] != outs[1]["session_id"]
    for i, (out, folder) in enumerate(zip(outs, folders)):
        assert out["result"] == f"dup {i} done"
        assert win.request_profile("session.info", session_id=out["session_id"])["cwd"] == str(folder)
    assert not [c for c in win.rpc_calls("session.create") if "title" in c["params_wire"] and
                c["params_in"].get("cwd") in {str(f) for f in folders}]


def test_10_an_empty_toolset_list_runs_with_verksted_own_tool_only(e2e):
    # k3 fix 3: with the plugin enabled the child keeps Verksted's own toolset; an empty selection
    # tolerates exactly that tool, by name, in tools.show.
    env = e2e({"verksted": REPO / "verksted"}).start()
    win = env.window("verksted")
    folder = folder_for(env, "empty")
    (folder / ".verksted.yaml").write_text("version: 1\ntoolsets: []\n", encoding="utf-8")
    env.model.when("CHILD_EMPTY", Final("answered without tools"))
    parent_run(env, "EMPTY", folder, "CHILD_EMPTY answer")
    psid, adm = parent_turn(env, win, "PARENT_EMPTY go", "empty")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["result"] == "answered without tools", out
    assert set(out["effective_toolsets"] or []) <= {"verksted"}, out
    child = [r for r in env.model.requests if "CHILD_EMPTY" in r["user"]]
    assert child and set(child[0]["tools"]) <= {"verksted"}, child[:1]


# 11 -----------------------------------------------------------------------------------------------------
def test_11_a_step_folder_inherits_the_workspace_list_and_cannot_widen_it(env):
    # k5 through the real backend: the walk from the topmost .verksted.yaml down to the step folder;
    # the inherited list is what session.create carries, a widening list is refused by the tool.
    win = env.window("verksted")
    root = folder_for(env, "inherit")
    (root / ".verksted.yaml").write_text("version: 1\ntoolsets: [file, e2eprobe]\n", encoding="utf-8")
    step = root / "workflow" / "10-step"
    step.mkdir(parents=True, exist_ok=True)
    env.model.when("CHILD_INH", WriteFile(str(step / "inh.txt"), "inherited"), Final("inherited run done"))
    parent_run(env, "INH", step, "CHILD_INH write inh.txt")
    psid, adm = parent_turn(env, win, "PARENT_INH go", "inh")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert (step / "inh.txt").read_text() == "inherited"
    assert out["config"]["workspace"] == str(root) and out["config"]["toolsets"] == ["file", "e2eprobe"]
    assert [f["path"] for f in out["config"]["files"]] == [str(root / ".verksted.yaml")]
    create = [c for c in win.rpc_calls("session.create") if c["params_in"].get("cwd") == str(step)]
    assert create and create[0]["params_wire"]["toolsets"] == ["file", "e2eprobe", "no_mcp"], create

    wide = root / "workflow" / "20-wide"
    wide.mkdir(parents=True, exist_ok=True)
    (wide / ".verksted.yaml").write_text("toolsets: [file, terminal]\n", encoding="utf-8")
    parent_run(env, "WIDE", wide, "CHILD_WIDE never", wait=0)
    psid2, adm2 = parent_turn(env, win, "PARENT_WIDE go", "wide")
    refused = tool_result(_terminal(win, psid2, adm2))
    assert refused["ok"] is False and refused["error"]["code"] == "toolsets_widen", refused
    assert not store_rows(env, "SELECT run_id FROM runs WHERE target_dir=?", str(wide))
