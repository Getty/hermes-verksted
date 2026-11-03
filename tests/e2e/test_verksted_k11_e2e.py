"""k11: the k8 / k10 / k9 scenarios that test_verksted_e2e.py leaves open.

Same rules as there: every test is a LINUX STAND-IN (real Hermes backend at the pinned revision ad2b705,
real agent plugin, real plugin API, the real desktop/plugin.js; the Desktop renderer is the Node shim).
None of them is "Desktop tested" and none was exercised on Windows - docs/windows-acceptance.md holds the
rows the maintainer runs there, each naming the test here that covers its logic.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from conftest import E2E_ROOT, FIXTURES
from test_verksted_e2e import (PLUGINS, _child_calls, _parent_call, _running, _terminal,
                               assert_never_answers, folder_for, parent_run, parent_turn, pump, run_by_state,
                               runs, sent_methods, store_rows, tool_result)
from vk_e2e.backend import REPO, VENV, HarnessError
from vk_e2e.flows import create_session, submit, wait_terminal
from vk_e2e.model import Call, Clarify, Final, Terminal, WriteFile

VERKSTED_ONLY = {"verksted": REPO / "verksted"}


@pytest.fixture
def env(shared_e2e_factory):
    e = shared_e2e_factory(PLUGINS)
    e.model.rules.clear()
    e.model.requests.clear()
    yield e
    e.close_clients()


def session_row(env, session_id):
    db = env.backend.home / "state.db"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, ended_at, end_reason FROM sessions WHERE id=?", (session_id,)).fetchall()
        return dict(rows[0]) if rows else None
    finally:
        conn.close()


def expire_claim(env, handle):
    conn = sqlite3.connect(env.backend.home / "plugin-data" / "verksted" / "data.db")
    conn.execute("UPDATE runs SET claim_expires_at=? WHERE run_id=?", (time.time() - 1, handle))
    conn.commit()
    conn.close()


# 17 -----------------------------------------------------------------------------------------------------
def test_17_a_rueckfrage_asked_with_no_viewer_is_reported_unanswered(env):
    """The child asks while no socket is attached to its session (the window dropped): Hermes ends the
    clarify at once as `unanswered` (session_server_requests.py:172-173), the turn goes on, and the finished
    run reports the question instead of presenting the assumed answer as a result."""
    win = env.window("verksted")
    folder = folder_for(env, "noviewer")
    env.model.when("CHILD_NOVIEW", Clarify("What is the project called?", gate="noviewer"),
                   Final(lambda c: "went on without an answer: " + c.last_tool))
    parent_run(env, "NOVIEW", folder, "CHILD_NOVIEW find out the name", wait=0)
    psid, adm = parent_turn(env, win, "PARENT_NOVIEW go", "noviewer")
    handle = tool_result(_terminal(win, psid, adm))["handle"]
    run = pump(win, _running(env, folder), what="child running")
    assert env.model.gate_hit("noviewer")
    win.disconnect()  # nobody is attached to the child session any more
    env.model.release("noviewer")
    time.sleep(3)  # the clarify is asked and settles at once
    win.reconnect()
    done = pump(win, run_by_state(win, handle, "finished"), what="run finished after the reconnect")
    assert done["outcome"] == "completed"
    out = _parent_call(env, win, psid, "STATUS_NOVIEW", {"action": "status", "handle": handle}, "noviewer2")
    assert out["state"] == "finished", out
    assert [u["question"] for u in out["unanswered"]] == ["What is the project called?"], out
    assert "blocked" not in out
    assert "question that got no answer" in out["hint"]
    assert "Rückfrage ohne Antwort: What is the project called?" in win.render("page")["text"]
    # nobody answered for the user, and nothing was left parked for a late viewer
    assert not [r for r in win.state()["serverRequests"] if r["answered"]]
    user = env.user()
    snap = user.open_chat(run["session_id"])
    assert snap.get("open_requests", []) == [] and not [p for p in snap.get("prompts", []) if p.get("kind") == "clarify"]
    assert_never_answers(win, env)


# 18 -----------------------------------------------------------------------------------------------------
def test_18_an_approval_nobody_answers_ends_blocked_and_the_command_does_not_run(e2e):
    env = e2e(PLUGINS, approvals_timeout=5).start()
    win = env.window("verksted")
    folder = folder_for(env, "silence")
    target = folder / "silence.txt"
    env.model.when("CHILD_SILENCE", Terminal(f"chmod -R 777 '{folder}' ; echo WROTE > '{target}'"),
                   Final(lambda c: "tool said: " + c.last_tool))
    parent_run(env, "SILENCE", folder, "CHILD_SILENCE write silence.txt", wait=0)
    psid, adm = parent_turn(env, win, "PARENT_SILENCE go", "silence")
    handle = tool_result(_terminal(win, psid, adm))["handle"]
    done = pump(win, run_by_state(win, handle, "finished"), timeout=120, what="run finished after approvals.timeout")
    assert done["outcome"] == "completed", "k1 C8: a blocked approval still ends completed"
    assert not target.exists(), "an approval nobody gave must not run the command"
    out = _parent_call(env, win, psid, "STATUS_SILENCE", {"action": "status", "handle": handle}, "silence2")
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert len(out["blocked"]) == 1 and out["blocked"][0]["reason"] == "timeout", out
    assert "chmod -R 777" in out["blocked"][0]["command"], out
    assert "unanswered" not in out
    assert "did not run" in out["hint"]
    assert "Nicht freigegeben: " in win.render("page")["text"]
    assert not [r for r in win.state()["serverRequests"] if r["answered"]]
    assert_never_answers(win, env)


# 19 -----------------------------------------------------------------------------------------------------
def test_19_two_concurrent_rueckfragen_are_answered_in_reverse_order_each_reaching_its_own_run(env):
    win = env.window("verksted")
    folders = [folder_for(env, "ask-one"), folder_for(env, "ask-two")]
    handles, children = [], []
    for i, (name, folder) in enumerate(zip(("ONE", "TWO"), folders)):
        env.model.when(f"CHILD_Q{name}", Clarify(f"Question {name}?"), Final(lambda c, n=name: f"run {n} got: {c.last_tool}"))
        parent_run(env, f"Q{name}", folder, f"CHILD_Q{name} ask", wait=0)
        psid, adm = parent_turn(env, win, f"PARENT_Q{name} go", f"q{name.lower()}")
        handles.append(tool_result(_terminal(win, psid, adm))["handle"])
    for folder in folders:
        children.append(pump(win, _running(env, folder), what=f"child in {folder.name} running")["session_id"])
    assert children[0] != children[1]
    user = env.user()
    for child in children:
        user.open_chat(child)
    first, second = (user.wait_prompt(c, "clarify") for c in children)  # both Rückfragen are open at once
    assert first["payload"] != second["payload"]
    user.answer_clarify(children[1], "answer-for-two")  # reverse order
    user.answer_clarify(children[0], "answer-for-one")
    for h in handles:
        pump(win, run_by_state(win, h, "finished"), what=f"run {h} finished")
    by_handle = {r["run_id"]: r for r in runs(win)}
    one, two = (by_handle[h] for h in handles)
    assert "answer-for-one" in one["result_preview"] and "answer-for-two" not in one["result_preview"], one["result_preview"]
    assert "answer-for-two" in two["result_preview"] and "answer-for-one" not in two["result_preview"], two["result_preview"]
    # what the child models were told, round 1 of each turn
    told = {r["user"].split()[0]: r["last_tool"] for r in env.model.requests if r["round"] == 1 and "CHILD_Q" in r["user"]}
    assert "answer-for-one" in told["CHILD_QONE"] and "answer-for-two" not in told["CHILD_QONE"], told
    assert "answer-for-two" in told["CHILD_QTWO"] and "answer-for-one" not in told["CHILD_QTWO"], told
    assert_never_answers(win, env)


# 20 -----------------------------------------------------------------------------------------------------
def test_20_takeover_in_create_requested_repeats_the_identical_create_and_starts_one_session(env):
    """Window A records create_requested, its session.create reaches Hermes and the reply is lost, then A
    is gone (its lease is expired by hand; a real one lasts 180 s). Window B takes the run over and repeats
    the identical create: same request_id, same params, one child session, one turn."""
    a = env.window("verksted", name="a")
    folder = folder_for(env, "takeover-create")
    env.model.when("CHILD_TKC", Final("created once, run once"))
    parent_run(env, "TKC", folder, "CHILD_TKC answer", wait=0)
    psid = create_session(a, request_id="parent-tkc", cwd=str(env.work))["session_id"]
    a.inject("session.create", "lose_reply", times=3, prefix={"request_id": "vk-"})
    adm = submit(a, psid, "PARENT_TKC go", "sub-tkc")
    handle = tool_result(wait_terminal(a, psid, adm["admission_id"]))["handle"]  # no tick: A never retries
    end = time.monotonic() + 60
    while time.monotonic() < end and not [c for c in a.rpc_calls("session.create") if str(c["params_in"].get("request_id", "")).startswith("vk-")]:
        time.sleep(0.3)
    time.sleep(1)
    row = store_rows(env, "SELECT state, session_id FROM runs WHERE run_id=?", handle)[0]
    assert row["state"] == "create_requested" and not row["session_id"], row
    mine = [c for c in a.rpc_calls("session.create") if str(c["params_in"].get("request_id", "")).startswith("vk-")]
    assert mine and all(c["injected"] == "lose_reply" for c in mine)
    a.close()
    expire_claim(env, handle)

    b = env.window("verksted", name="b")
    done = pump(b, run_by_state(b, handle, "finished", "unknown", "refused"), what="run finished under window B")
    assert done["state"] == "finished" and done["outcome"] == "completed" and done["result_preview"] == "created once, run once", done
    again = [c for c in b.rpc_calls("session.create") if str(c["params_in"].get("request_id", "")).startswith("vk-")]
    assert len(again) == 1 and again[0]["params_wire"]["request_id"] == f"{handle}.create"
    assert {json.dumps(c["params_wire"], sort_keys=True) for c in mine + again} and \
        len({json.dumps(c["params_wire"], sort_keys=True) for c in mine + again}) == 1, "identical call, same request_id"
    final = store_rows(env, "SELECT session_id, request_id FROM runs WHERE run_id=?", handle)[0]
    assert again[0]["result_session_id"] == final["session_id"]
    assert len([r for r in env.model.requests if "CHILD_TKC" in r["user"] and r["round"] == 0]) == 1, "one turn"
    assert len(_child_calls(b, "prompt.submit", handle)) == 1
    assert_never_answers(b, env)


# 21 -----------------------------------------------------------------------------------------------------
def test_21_cancel_before_any_desktop_window_ends_cancelled_and_nothing_reaches_hermes(env):
    client = env.window("e2eprobe", name="client")  # a Desktop window WITHOUT the Verksted part
    folder = folder_for(env, "cancel-early")
    env.model.when("CHILD_EARLY", Final("must never run"))
    parent_run(env, "EARLY", folder, "CHILD_EARLY answer", wait=0)
    psid, adm = parent_turn(env, client, "PARENT_EARLY go", "early")
    out = tool_result(wait_terminal(client, psid, adm["admission_id"]))
    assert out["state"] == "waiting_for_gui", out
    handle = out["handle"]
    cancel = _parent_call(env, client, psid, "CANCEL_EARLY", {"action": "cancel", "handle": handle, "wait_seconds": 0}, "early2", timeout=60)
    assert cancel["state"] == "cancelled" and cancel["cancel"] == "not_started", cancel
    assert cancel["reason"] == "cancelled_before_submit" and "nothing ran" in cancel["hint"], cancel
    row = store_rows(env, "SELECT state, session_id, admission_id FROM runs WHERE run_id=?", handle)[0]
    assert row == {"state": "cancelled", "session_id": None, "admission_id": None}, row
    # now a Verksted window appears: it must neither start nor stop anything for this run
    win = env.window("verksted", name="late")
    for _ in range(4):
        win.tick()
        time.sleep(1)
    methods = sent_methods(win)
    assert not [c for c in win.rpc_calls("session.create") if c["params_in"].get("cwd") == str(folder)], methods
    assert "prompt.submit" not in methods and "prompt.cancel" not in methods, methods
    assert not [r for r in env.model.requests if "CHILD_EARLY" in r["user"]], "the child model was never asked"
    assert store_rows(env, "SELECT state FROM runs WHERE run_id=?", handle)[0]["state"] == "cancelled"
    assert_never_answers(win, env)


# 22 / 23 ------------------------------------------------------------------------------------------------
def test_22_no_result_block_is_reported_missing_although_the_file_exists(env):
    win = env.window("verksted")
    folder = folder_for(env, "noblock")
    env.model.when("CHILD_NOBLOCK", WriteFile(str(folder / "made.md"), "made"), Final("I wrote made.md for you."))
    parent_run(env, "NOBLOCK", folder, "CHILD_NOBLOCK write made.md")
    psid, adm = parent_turn(env, win, "PARENT_NOBLOCK go", "noblock")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert out["result_block"] == "missing" and out["summary"] is None, out
    assert out["outputs"] == [] and out["missing"] == [], "never invented from the tool rows, never listed from the folder"
    assert (folder / "made.md").exists()
    assert out["result"] == "I wrote made.md for you."
    assert "no usable result block" in out["hint"] and "Do not guess" in out["hint"], out["hint"]


def test_23_an_unclosed_result_block_is_reported_unparsable(env):
    win = env.window("verksted")
    folder = folder_for(env, "unclosed")
    env.model.when("CHILD_UNCLOSED", WriteFile(str(folder / "half.md"), "half"),
                   Final("Done.\n<verksted-result>\nsummary: Wrote half.md.\noutputs:\n- half.md\n"))
    parent_run(env, "UNCLOSED", folder, "CHILD_UNCLOSED write half.md")
    psid, adm = parent_turn(env, win, "PARENT_UNCLOSED go", "unclosed")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert out["result_block"] == "unparsable" and out["result_block_detail"], out
    assert out["summary"] is None and out["outputs"] == [] and out["missing"] == [], "nothing taken from a broken block"
    assert "half.md" in out["result"], "the clipped final answer stays"
    assert "no usable result block" in out["hint"]


# 24 -----------------------------------------------------------------------------------------------------
def test_24_send_after_the_child_was_orphan_closed(e2e):
    """The child's only viewer (window A) goes away; Hermes ends the idle local session after
    sessions.orphan_end_grace_s (shortened here; default 300 s). A follow-up send from a new window must
    either run in the same session (Hermes lets an orphan-ended session be resumed) or be reported cleanly."""
    env = e2e(PLUGINS, config={"sessions": {"orphan_end_grace_s": 2}}).start()
    a = env.window("verksted", name="a")
    folder = folder_for(env, "orphan")
    env.model.when("CHILD_ORPH1", Final("first turn done"))
    env.model.when("CHILD_ORPH2", WriteFile(str(folder / "after.txt"), "later"),
                   Final("second turn done\n<verksted-result>\nsummary: Wrote after.txt.\noutputs:\n- after.txt\n</verksted-result>"))
    parent_run(env, "ORPH1", folder, "CHILD_ORPH1 answer")
    psid, adm = parent_turn(env, a, "PARENT_ORPH1 go", "orph1")
    first = tool_result(_terminal(a, psid, adm))
    assert first["state"] == "finished", first
    child, handle = first["session_id"], first["handle"]
    a.close()
    end = time.monotonic() + 40
    while time.monotonic() < end and not (session_row(env, child) or {}).get("ended_at"):
        time.sleep(0.5)
    row = session_row(env, child)
    assert row and row["ended_at"], f"the child session was not orphan-closed within 40 s: {row}"
    assert row["end_reason"] == "ws_orphan_reap", row

    b = env.window("verksted", name="b")
    second = _parent_call(env, b, psid, "PARENT_ORPH2", {"action": "send", "handle": handle, "task": "CHILD_ORPH2 write after.txt",
                                                        "wait_seconds": 100}, "orph2")
    # What is true at ad2b705 (asserted below, found by running it): see the card.
    assert second["ok"], second
    assert second["state"] == "finished" and second["turn"] == 2 and second["session_id"] == child, second
    assert second["summary"] == "Wrote after.txt." and [o["relative"] for o in second["outputs"]] == ["after.txt"]
    assert (folder / "after.txt").read_text() == "later"
    assert len([c for c in b.rpc_calls("session.create") if c["params_in"].get("cwd") == str(folder)]) == 0, "no new child session"
    assert [c["params_in"]["submission_id"] for c in _child_calls(b, "prompt.submit", handle)] == [f"{handle}.submit.2"]
    assert_never_answers(b, env)


# k9: MCP and the lean profile ---------------------------------------------------------------------------
FAKE_MCP = FIXTURES / "fake_mcp.py"


def _mcp_config(root: Path, **servers):
    marker = root / "mcp-spawns.txt"
    py = str(VENV / "bin" / "python")
    cfg = {}
    for name, enabled in servers.items():
        s = {"command": py, "args": ["-I", str(FAKE_MCP), str(marker), name]}
        if not enabled:
            s["enabled"] = False
        cfg[name] = s
    return marker, {"mcp_servers": cfg}


def _spawns(marker: Path) -> list[str]:
    return [l.split()[0] for l in marker.read_text().splitlines()] if marker.exists() else []


def _mcp_status(win, session_id) -> dict:
    return {s["name"]: s["status"] for s in win.request_profile("session.info", session_id=session_id).get("mcp_servers", [])}


def test_lean_profile_starts_no_unneeded_mcp(request, e2e):
    marker, cfg = _mcp_config(E2E_ROOT / request.node.name, unneeded=False)
    env = e2e(VERKSTED_ONLY, config=cfg).start()
    win = env.window("verksted")
    folder = folder_for(env, "lean")
    (folder / ".verksted.yaml").write_text("version: 1\ntoolsets: [file]\n", encoding="utf-8")
    env.model.when("CHILD_LEAN", WriteFile(str(folder / "lean.txt"), "lean"), Final("lean run done"))
    parent_run(env, "LEAN", folder, "CHILD_LEAN write lean.txt")
    psid, adm = parent_turn(env, win, "PARENT_LEAN go", "lean")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert (folder / "lean.txt").read_text() == "lean"
    assert _spawns(marker) == [], f"a disabled MCP server was started: {marker.read_text()}"
    assert _mcp_status(win, out["session_id"]) == {"unneeded": "disabled"}
    assert set(out["effective_toolsets"]) <= {"file", "verksted"} and "unneeded" not in out["effective_toolsets"], out["effective_toolsets"]
    assert not any("unneeded" in t for r in env.model.requests if "CHILD_LEAN" in r["user"] for t in r["tools"]), "no MCP tool offered"
    assert _spawns(marker) == []
    assert_never_answers(win, env)


def test_fat_profile_mcp_toolset_is_dropped_but_the_server_still_runs(request, e2e):
    """An enabled MCP server starts with the gateway, whatever any session asks for. Verksted sends
    `no_mcp` with a listed toolset list (verksted/config.py, decision M3), so the run is NOT refused, the child
    gets only the listed toolsets - and the profile's server is still there, spawned exactly once. A lean profile
    (no enabled server) is the only way to keep a server from starting."""
    marker, cfg = _mcp_config(E2E_ROOT / request.node.name, needed=True)
    env = e2e(VERKSTED_ONLY, config=cfg).start()
    assert _spawns(marker) == ["needed"], "the enabled server is spawned once, at gateway start"
    win = env.window("verksted")
    folder = folder_for(env, "fat")
    (folder / ".verksted.yaml").write_text("version: 1\ntoolsets: [file]\n", encoding="utf-8")
    env.model.when("CHILD_FAT", WriteFile(str(folder / "fat.txt"), "fat"), Final("fat run done"))
    parent_run(env, "FAT", folder, "CHILD_FAT write fat.txt")
    psid, adm = parent_turn(env, win, "PARENT_FAT go", "fat")
    out = tool_result(_terminal(win, psid, adm))
    assert out["state"] == "finished" and out["outcome"] == "completed", out
    assert set(out["effective_toolsets"]) <= {"file", "verksted"} and "needed" not in out["effective_toolsets"], out["effective_toolsets"]
    create = [c for c in win.rpc_calls("session.create") if c["params_in"].get("cwd") == str(folder)]
    assert "no_mcp" in create[0]["params_wire"]["toolsets"], create[0]["params_wire"]
    offered = [t for r in env.model.requests if "CHILD_FAT" in r["user"] for t in r["tools"]]
    assert offered and not any("needed" in t for t in offered), f"the child was offered an MCP tool: {offered}"
    assert _mcp_status(win, out["session_id"]).get("needed") == "connected", "no_mcp drops the toolset, not the process"
    assert _spawns(marker) == ["needed"], "the run spawned no second server"
    assert_never_answers(win, env)


# k9: what a waiting_for_gui answer says about the Desktop part ---------------------------------------------
def test_k9_waiting_answer_tells_never_polled_then_polled_and_nothing_starts(e2e):
    """Fresh home (own backend: the poll record is per profile and would leak from other tests).
    (a) only a window WITHOUT the Verksted part exists -> desktop_last_poll_s is null, hint says never polled;
    (b) a Verksted window polls once (nothing claimable then) and goes away -> a number, hint names the poll,
    the new run stays waiting_for_gui and nothing reaches Hermes. The 'no window seems connected' branch
    needs a poll older than 60 s and is NOT covered here (unit-tested in tests/agent/test_tool.py)."""
    env = e2e(PLUGINS).start()
    client = env.window("e2eprobe", name="client")  # parent chat; no Verksted part, so it never polls
    folder_a, folder_b = folder_for(env, "poll-a"), folder_for(env, "poll-b")
    env.model.when("CHILD_POLL", Final("must never run"))
    parent_run(env, "POLLA", folder_a, "CHILD_POLL a", wait=0)
    psid, adm = parent_turn(env, client, "PARENT_POLLA go", "polla")
    a = tool_result(wait_terminal(client, psid, adm["admission_id"]))
    assert a["state"] == "waiting_for_gui" and a["desktop_last_poll_s"] is None, a
    assert "no Verksted Desktop part has polled" in a["hint"], a["hint"]
    # free the folder; the run must not be started by the window that appears next
    c = _parent_call(env, client, psid, "CANCEL_POLLA", {"action": "cancel", "handle": a["handle"], "wait_seconds": 0}, "pollc")
    assert c["state"] == "cancelled", c

    win = env.window("verksted", name="polled")
    win.tick()
    assert not win.rpc_calls("session.create"), "nothing was claimable at that poll"
    win.close()  # the window went away
    b = _parent_call(env, client, psid, "PARENT_POLLB", {"action": "run", "folder": str(folder_b), "task": "CHILD_POLL b",
                                                         "wait_seconds": 0}, "pollb")
    assert b["state"] == "waiting_for_gui", b
    assert isinstance(b["desktop_last_poll_s"], int) and 0 <= b["desktop_last_poll_s"] < 60, b
    assert "polled this profile" in b["hint"] and "so it should start the run" in b["hint"], b["hint"]
    assert "no window seems" not in b["hint"] and "has polled this profile yet" not in b["hint"], b["hint"]
    assert store_rows(env, "SELECT state, session_id, admission_id FROM runs WHERE run_id=?", b["handle"])[0] == \
        {"state": "waiting_for_gui", "session_id": None, "admission_id": None}
    assert not [r for r in env.model.requests if "CHILD_POLL" in r["user"] and "PARENT" not in r["user"]], "no child asked"
    assert not [x for x in client.rpc_calls("session.create") if x["params_in"].get("cwd") in (str(folder_a), str(folder_b))]
    assert_never_answers(client, env)
