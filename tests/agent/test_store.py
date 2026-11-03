"""The run and claim store: atomic claims, generation fencing, store-before-start."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from verksted.store import FINAL_STATES, Store, StoreError

IDENTITY = {"connection_id": "local", "route_profile": "default", "target_profile": "default",
            "profile_id": "/homes/default", "instance_id": "inst-1"}
PRINCIPAL = {"subject": "user-1", "instance_id": "inst-1", "profile_id": "/homes/default"}


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def run_record(n: int = 1, folder: str = "/work/a", **over):
    rid = f"vk-{n:04d}"
    rec = {
        "run_id": rid, "parent_session_id": "parent-sid", "parent_session_key": "route-key",
        "parent_profile": "default", "parent_profile_home": "/homes/default", "parent_source": "desktop",
        "backend_platform": "linux", "target_dir": folder, "target_key": folder, "task": "do it",
        "resolved_config": {"version": 1, "profile": "default", "toolsets": ["file"], "files": []},
        "config_fingerprint": "sha256:x", "expected_toolsets": ["file"],
        "request_id": f"{rid}.create",
        "create_params": {"cwd": folder, "cwd_explicit": True, "request_id": f"{rid}.create",
                          "title": "Verksted · a", "toolsets": ["file", "no_mcp"]},
        "submission_id": f"{rid}.submit.1", "prompt_text": "do it",
    }
    rec.update(over)
    return rec


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return Store.at_path(tmp_path / "data.db", clock=clock)


def test_create_run_stores_every_start_id_before_anything_is_requested(store):
    rec = run_record()
    run = store.create_run(rec, max_active=4)
    assert run["state"] == "waiting_for_gui"
    again = store.get_run(rec["run_id"])
    for key in ("request_id", "submission_id", "prompt_text", "create_params", "resolved_config",
                "config_fingerprint", "expected_toolsets", "parent_session_key", "parent_profile_home"):
        assert again[key] == rec[key], key
    assert again["claim_generation"] == 0 and again["claim_owner"] is None
    assert again["session_id"] is None and again["admission_id"] is None


def test_second_active_run_in_the_same_folder_is_refused_and_names_the_holder(store):
    store.create_run(run_record(1), max_active=4)
    with pytest.raises(StoreError) as err:
        store.create_run(run_record(2), max_active=4)
    assert err.value.code == "folder_busy" and err.value.extra["run_id"] == "vk-0001"


def test_partial_unique_index_holds_even_without_the_precheck(store, tmp_path):
    store.create_run(run_record(1), max_active=4)
    conn = sqlite3.connect(tmp_path / "data.db")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    copy = ", ".join({"run_id": "'vk-x'", "request_id": "'vk-x.create'", "submission_id": "'vk-x.submit.1'"}.get(c, c)
                     for c in cols)
    with pytest.raises(sqlite3.IntegrityError, match="target_key"):
        conn.execute(f"INSERT INTO runs ({', '.join(cols)}) SELECT {copy} FROM runs WHERE run_id='vk-0001'")
    conn.execute("UPDATE runs SET state='finished' WHERE run_id='vk-0001'")
    conn.execute(f"INSERT INTO runs ({', '.join(cols)}) SELECT {copy} FROM runs WHERE run_id='vk-0001'")
    conn.close()


def test_a_final_run_frees_its_folder(store):
    store.create_run(run_record(1), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    store.record_step("vk-0001", "w1", claim["generation"], "refused", {"reason": "legacy_route"})
    assert store.create_run(run_record(2), max_active=4)["state"] == "waiting_for_gui"


def test_active_run_limit(store):
    for n in range(3):
        store.create_run(run_record(n, folder=f"/work/{n}"), max_active=3)
    with pytest.raises(StoreError) as err:
        store.create_run(run_record(9, folder="/work/9"), max_active=3)
    assert err.value.code == "too_many_runs"


def test_claim_raises_the_generation_and_refuses_a_live_claim_of_another_owner(store, clock):
    store.create_run(run_record(), max_active=4)
    first = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    assert first["generation"] == 1 and first["mode"] == "start"
    with pytest.raises(StoreError) as err:
        store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)
    assert err.value.code == "claimed"
    clock.t += 61
    second = store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)
    assert second["generation"] == 2


def test_stale_generation_is_locked_out(store, clock):
    store.create_run(run_record(), max_active=4)
    old = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    clock.t += 61
    store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", old["generation"], "create_requested", {"identity": IDENTITY},
                          principal=PRINCIPAL)
    assert err.value.code == "stale_generation"
    # The same owner re-claiming also supersedes its own older generation.
    clock.t += 61
    newer = store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w2", newer["generation"] - 1, "create_requested", {"identity": IDENTITY},
                          principal=PRINCIPAL)
    assert err.value.code == "stale_generation"


def test_an_expired_lease_allows_no_step(store, clock):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    clock.t += 60
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", claim["generation"], "create_requested", {"identity": IDENTITY},
                          principal=PRINCIPAL)
    assert err.value.code == "lease_expired"


def test_release_keeps_the_generation(store, clock):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    store.release("vk-0001", "w1", claim["generation"])
    assert store.claimable_runs()[0]["run_id"] == "vk-0001"
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", claim["generation"], "create_requested", {"identity": IDENTITY},
                          principal=PRINCIPAL)
    assert err.value.code == "lease_expired"
    assert store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)["generation"] == 2


def test_concurrent_claims_grant_exactly_one(tmp_path):
    path = tmp_path / "race.db"
    Store.at_path(path).create_run(run_record(), max_active=4)
    results: list[str] = []
    barrier = threading.Barrier(8)

    def claimant(i: int) -> None:
        store = Store.at_path(path)  # one store per "window", as separate processes would have
        barrier.wait()
        try:
            store.claim("vk-0001", f"w{i}", principal=PRINCIPAL, lease_s=60)
            results.append("granted")
        except StoreError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=claimant, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("granted") == 1
    assert set(results) == {"granted", "claimed"}
    assert Store.at_path(path).get_run("vk-0001")["claim_generation"] == 1


def _to_running(store, claim, *, identity=IDENTITY, principal=PRINCIPAL):
    g = claim["generation"]
    store.record_step("vk-0001", "w1", g, "create_requested", {"identity": identity}, principal=principal)
    store.record_step("vk-0001", "w1", g, "session_created", {"identity": identity, "session_id": "child-1"},
                      principal=principal)
    store.record_step("vk-0001", "w1", g, "toolsets_checked",
                      {"identity": identity, "enabled_toolsets": ["file"], "check": {"tools_total": 3}},
                      principal=principal)
    store.record_step("vk-0001", "w1", g, "submit_requested", {"identity": identity}, principal=principal)
    return store.record_step("vk-0001", "w1", g, "running", {"identity": identity, "admission_id": "adm-1"},
                             principal=principal)


def test_the_start_path_records_each_step_in_order(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    run = _to_running(store, claim)
    assert run["state"] == "running" and run["session_id"] == "child-1" and run["admission_id"] == "adm-1"
    assert run["subject"] == "user-1" and run["connection_id"] == "local" and run["instance_id"] == "inst-1"
    assert run["effective_toolsets"] == ["file"]
    done = store.record_step("vk-0001", "w1", claim["generation"], "finished",
                             {"identity": IDENTITY, "outcome": "completed", "final_response": "x" * 40000},
                             principal=PRINCIPAL)
    assert done["state"] == "finished" and done["outcome"] == "completed"
    assert done["result_ref"]["truncated"] is True and len(done["result_ref"]["final_response"]) == 32 * 1024
    assert done["result_ref"]["session_id"] == "child-1"


def test_steps_out_of_order_are_refused(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", claim["generation"], "running",
                          {"identity": IDENTITY, "admission_id": "a"}, principal=PRINCIPAL)
    assert err.value.code == "bad_transition"


def test_a_session_step_under_another_identity_is_refused(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    g = claim["generation"]
    store.record_step("vk-0001", "w1", g, "create_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
    other_conn = dict(IDENTITY, connection_id="remote-1")
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", g, "session_created", {"identity": other_conn, "session_id": "s"},
                          principal=PRINCIPAL)
    assert err.value.code == "identity_mismatch" and err.value.extra["fields"] == ["connection_id"]
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", g, "session_created", {"identity": IDENTITY, "session_id": "s"},
                          principal=dict(PRINCIPAL, subject="someone-else"))
    assert err.value.extra["fields"] == ["subject"]
    # A new gateway instance (restart) is the same identity: idempotency is durable across restarts.
    restarted = dict(IDENTITY, instance_id="inst-2")
    assert store.record_step("vk-0001", "w1", g, "session_created", {"identity": restarted, "session_id": "s"},
                             principal=PRINCIPAL)["state"] == "session_created"
    # Closing as unknown needs no identity.
    assert store.record_step("vk-0001", "w1", g, "unknown", {"reason": "identity_changed"})["state"] == "unknown"


def test_admission_id_of_a_running_run_cannot_change(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    _to_running(store, claim)
    with pytest.raises(StoreError) as err:
        store.record_step("vk-0001", "w1", claim["generation"], "running",
                          {"identity": IDENTITY, "admission_id": "adm-2"}, principal=PRINCIPAL)
    assert err.value.code == "bad_data"
    run = store.record_step("vk-0001", "w1", claim["generation"], "running",
                            {"identity": IDENTITY, "receipt_status": "started",
                             "prompts": [{"kind": "approval", "prompt_id": "p1", "command": "rm x",
                                          "choices": ["once", "deny"], "timeout_s": 300},
                                         {"kind": "bogus", "prompt_id": "p2"}]},
                            principal=PRINCIPAL)
    assert run["admission_id"] == "adm-1"
    assert run["state_detail"]["prompts"] == [{"kind": "approval", "prompt_id": "p1", "command": "rm x",
                                               "timeout_s": 300}]


def test_a_final_run_cannot_be_claimed(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    store.record_step("vk-0001", "w1", claim["generation"], "refused", {"reason": "x"})
    with pytest.raises(StoreError) as err:
        store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    assert err.value.code == "final"
    assert store.get_run("vk-0001")["state"] in FINAL_STATES


def test_reconcile_mode_after_the_first_request(store, clock):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    store.record_step("vk-0001", "w1", claim["generation"], "create_requested", {"identity": IDENTITY},
                      principal=PRINCIPAL)
    clock.t += 61
    again = store.claim("vk-0001", "w2", principal=PRINCIPAL, lease_s=60)
    assert again["mode"] == "reconcile"
    assert again["run"]["create_params"]["request_id"] == "vk-0001.create"


def test_child_sessions_and_compaction_aliases(store):
    store.create_run(run_record(), max_active=4)
    claim = store.claim("vk-0001", "w1", principal=PRINCIPAL, lease_s=60)
    _to_running(store, claim)
    assert store.is_child_session("child-1")
    assert store.is_child_session("", "nobody", "child-1")
    assert not store.is_child_session("parent-sid")
    assert store.add_session_alias("child-1", "child-1b")
    assert store.add_session_alias("child-1b", "child-1c")
    assert store.is_child_session("child-1c")
    assert store.resolve_session("child-1c") == "child-1"
    # A parent's rotation is followed too, so the parent keeps its handles.
    assert store.add_session_alias("parent-sid", "parent-sid-2")
    assert store.resolve_session("parent-sid-2") == "parent-sid"
    assert not store.is_child_session("parent-sid-2")
    assert not store.add_session_alias("stranger", "stranger-2")


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "new.db"
    Store.at_path(path).list_runs()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError) as err:
        Store.at_path(path).list_runs()
    assert err.value.code == "schema_too_new"


def test_opening_a_current_store_writes_nothing(tmp_path):
    # k3 fix 9c: every tool call opens a fresh Store; a current schema must not cost a write txn.
    path = tmp_path / "data.db"
    Store.at_path(path).list_runs()
    seen = []

    def connect():
        conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        conn.set_trace_callback(seen.append)
        return conn

    assert Store(connect).list_runs() == []
    assert not [s for s in seen if s.lstrip().upper().startswith(("BEGIN", "CREATE", "INSERT"))], seen


# -- schema 2 (cards k8, k10) ---------------------------------------------------------------

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def _v1_store(tmp_path, clock):
    """A schema-1 file built by the committed schema-1 code (fixtures/make_store_v1.py)."""
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript((FIXTURES / "store_v1.sql").read_text())
    conn.close()
    return path, Store.at_path(path, clock=clock)


def test_a_schema_1_store_is_migrated_in_place(tmp_path, clock):
    path, store = _v1_store(tmp_path, clock)
    runs = {r["run_id"]: r for r in store.list_runs()}
    assert {k: r["state"] for k, r in runs.items()} == {
        "vk-0001": "finished", "vk-0002": "running", "vk-0003": "waiting_for_gui"}
    assert all(r["turn_no"] == 1 and r["cancel_requested_at"] is None for r in runs.values())
    assert runs["vk-0001"]["result_ref"]["final_response"].startswith("Done.")
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    index_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='runs_one_active_per_folder'").fetchone()[0]
    assert "send_waiting" in index_sql
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    conn.close()
    # The migrated store works: the running run keeps its claim fencing, the folder lock still holds.
    with pytest.raises(StoreError) as err:
        store.create_run(run_record(9, folder="/work/busy"), max_active=8)
    assert err.value.code == "folder_busy" and err.value.extra["run_id"] == "vk-0002"
    assert store.start_send("vk-0001", task="more", prompt_text="more", max_active=8)["state"] == "send_waiting"


def test_a_failed_migration_leaves_the_schema_1_store_untouched(tmp_path, clock, monkeypatch):
    path, _ = _v1_store(tmp_path, clock)
    import verksted.store as vstore
    monkeypatch.setattr(vstore, "_MIGRATE_1_TO_2", vstore._MIGRATE_1_TO_2[:2] + ("THIS IS NOT SQL",))
    with pytest.raises(sqlite3.OperationalError):
        Store.at_path(path, clock=clock).list_runs()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    assert "turn_no" not in [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    conn.close()


def _waiting(store, n=1, folder="/work/a"):
    store.create_run(run_record(n, folder=folder), max_active=8)
    return f"vk-{n:04d}"


@pytest.mark.parametrize("upto", ["waiting_for_gui", "create_requested", "session_created", "toolsets_checked"])
def test_a_stop_before_the_submit_cancels_at_once_and_locks_the_desktop_out(store, upto):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    g = claim["generation"]
    steps = [("create_requested", {"identity": IDENTITY}),
             ("session_created", {"identity": IDENTITY, "session_id": "child-1"}),
             ("toolsets_checked", {"identity": IDENTITY, "enabled_toolsets": ["file"]})]
    for name, data in steps[:["waiting_for_gui", "create_requested", "session_created", "toolsets_checked"].index(upto)]:
        store.record_step(rid, "w1", g, name, data, principal=PRINCIPAL)
    answer = store.request_cancel(rid)
    assert answer["cancel"] == "not_started" and answer["run"]["state"] == "cancelled"
    assert answer["run"]["state_detail"] == {"reason": "cancelled_before_submit", "after": upto}
    nxt = {"waiting_for_gui": "create_requested", "create_requested": "session_created",
           "session_created": "toolsets_checked", "toolsets_checked": "submit_requested"}[upto]
    with pytest.raises(StoreError) as err:
        store.record_step(rid, "w1", g, nxt, {"identity": IDENTITY, "session_id": "child-1"}, principal=PRINCIPAL)
    assert err.value.code == "bad_transition"
    # The folder is free again.
    assert store.create_run(run_record(2), max_active=8)["state"] == "waiting_for_gui"


def test_a_stop_after_the_submit_is_only_a_request_until_hermes_ends_the_turn(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    _to_running(store, claim)
    answer = store.request_cancel(rid)
    assert answer["cancel"] == "requested" and answer["run"]["state"] == "running"
    first = answer["run"]["cancel_requested_at"]
    assert first is not None
    assert store.request_cancel(rid)["run"]["cancel_requested_at"] == first  # idempotent
    # Hermes' terminal receipt ends it; the outcome says whether the stop was in time.
    done = store.record_step(rid, "w1", claim["generation"], "finished",
                             {"identity": IDENTITY, "outcome": "interrupted", "final_response": "stopped"},
                             principal=PRINCIPAL)
    assert done["state"] == "finished" and done["outcome"] == "interrupted"
    assert store.request_cancel(rid)["cancel"] == "already_final"


def test_only_a_requested_stop_lets_the_desktop_record_cancelled(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    g = claim["generation"]
    for name, data in [("create_requested", {"identity": IDENTITY}),
                       ("session_created", {"identity": IDENTITY, "session_id": "child-1"}),
                       ("toolsets_checked", {"identity": IDENTITY}),
                       ("submit_requested", {"identity": IDENTITY})]:
        store.record_step(rid, "w1", g, name, data, principal=PRINCIPAL)
    with pytest.raises(StoreError) as err:
        store.record_step(rid, "w1", g, "cancelled", {"reason": "never_admitted"})
    assert err.value.code == "bad_transition"
    store.request_cancel(rid)
    run = store.record_step(rid, "w1", g, "cancelled", {"reason": "never_admitted"})
    assert run["state"] == "cancelled" and run["state_detail"]["reason"] == "never_admitted"


def _finished(store, claim, *, outcome="completed", text="done", **extra):
    _to_running(store, claim)
    return store.record_step("vk-0001", "w1", claim["generation"], "finished",
                             {"identity": IDENTITY, "outcome": outcome, "final_response": text, **extra},
                             principal=PRINCIPAL)


def test_send_continues_a_finished_run_in_its_session_and_archives_the_turn(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    _finished(store, claim, text="first answer")
    store.release(rid, "w1", claim["generation"])
    run = store.start_send(rid, task="and now B", prompt_text="and now B\n<suffix>", max_active=8)
    assert run["state"] == "send_waiting" and run["turn_no"] == 2
    assert run["submission_id"] == "vk-0001.submit.2" and run["prompt_text"] == "and now B\n<suffix>"
    assert run["session_id"] == "child-1" and run["request_id"] == "vk-0001.create"
    assert run["admission_id"] is None and run["result_ref"] is None and run["outcome"] is None
    assert run["connection_id"] == "local" and run["subject"] == "user-1"  # identity frozen from turn 1
    [turn] = store.turns(rid)
    assert turn["turn"] == 1 and turn["submission_id"] == "vk-0001.submit.1" and turn["state"] == "finished"
    assert turn["result_ref"]["final_response"] == "first answer" and turn["admission_id"] == "adm-1"
    # The follow-up holds the folder and is claimed in start mode.
    with pytest.raises(StoreError) as err:
        store.create_run(run_record(2), max_active=8)
    assert err.value.code == "folder_busy"
    again = store.claim(rid, "w2", principal=PRINCIPAL, lease_s=60)
    assert again["mode"] == "start" and again["generation"] == claim["generation"] + 1
    g = again["generation"]
    with pytest.raises(StoreError) as err:
        store.record_step(rid, "w2", g, "submit_requested", {"identity": dict(IDENTITY, connection_id="other")},
                          principal=PRINCIPAL)
    assert err.value.code == "identity_mismatch"
    store.record_step(rid, "w2", g, "submit_requested", {"identity": IDENTITY}, principal=PRINCIPAL)
    run = store.record_step(rid, "w2", g, "running", {"identity": IDENTITY, "admission_id": "adm-2"},
                            principal=PRINCIPAL)
    assert run["admission_id"] == "adm-2"


@pytest.mark.parametrize("final,code", [("running", "run_active"), ("unknown", "outcome_unknown"),
                                        ("refused", "not_sendable"), ("cancelled_early", "not_sendable")])
def test_send_is_refused_unless_the_last_turn_ended_in_a_known_session(store, final, code):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    g = claim["generation"]
    if final == "running":
        _to_running(store, claim)
    elif final == "unknown":
        _to_running(store, claim)
        store.record_step(rid, "w1", g, "unknown", {"reason": "admission_unknown"})
    elif final == "refused":
        store.record_step(rid, "w1", g, "refused", {"reason": "legacy_route"})
    else:
        store.request_cancel(rid)  # before any session existed
    with pytest.raises(StoreError) as err:
        store.start_send(rid, task="t", prompt_text="t", max_active=8)
    assert err.value.code == code
    assert store.turns(rid) == []


def test_a_follow_up_stopped_before_its_submit_can_be_sent_again(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    _finished(store, claim)
    store.start_send(rid, task="t2", prompt_text="t2", max_active=8)
    assert store.request_cancel(rid)["cancel"] == "not_started"
    run = store.start_send(rid, task="t3", prompt_text="t3", max_active=8)
    assert run["turn_no"] == 3 and run["submission_id"] == "vk-0001.submit.3"
    assert [t["state"] for t in store.turns(rid)] == ["finished", "cancelled"]


def test_send_respects_the_folder_lock_and_the_active_limit(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    _finished(store, claim)
    store.create_run(run_record(2, folder="/work/a"), max_active=8)
    with pytest.raises(StoreError) as err:
        store.start_send(rid, task="t", prompt_text="t", max_active=8)
    assert err.value.code == "folder_busy" and err.value.extra["run_id"] == "vk-0002"
    claim2 = store.claim("vk-0002", "w1", principal=PRINCIPAL, lease_s=60)
    store.record_step("vk-0002", "w1", claim2["generation"], "refused", {"reason": "x"})
    store.create_run(run_record(3, folder="/work/other"), max_active=8)
    with pytest.raises(StoreError) as err:
        store.start_send(rid, task="t", prompt_text="t", max_active=1)
    assert err.value.code == "too_many_runs"
    assert store.start_send(rid, task="t", prompt_text="t", max_active=2)["state"] == "send_waiting"


def test_finished_stores_the_checked_result_reference_not_file_contents(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    check = {"block": "ok", "summary": "s" * 3000, "root": "/work",
             "outputs": [{"path": f"/work/f{i}", "relative": f"f{i}", "kind": "file", "size": 3, "mtime": 1.5,
                          "content": "never stored"} for i in range(60)],
             "missing": [{"path": "/etc/x", "reason": "outside_workspace"}], "truncated": False}
    done = _finished(store, claim, result=check,
                     unanswered=[{"question": "Which one?"}] * 10,
                     blocked=[{"command": "rm -rf x", "reason": "timeout"}])
    ref = done["result_ref"]
    assert ref["v"] == 2 and ref["block"] == "ok" and ref["root"] == "/work" and ref["checked_at"] == 1000.0
    assert len(ref["summary"]) == 2000 and ref["result_truncated"] is True
    assert len(ref["outputs"]) == 50 and "content" not in ref["outputs"][0]
    assert ref["outputs"][0] == {"path": "/work/f0", "relative": "f0", "kind": "file", "size": 3, "mtime": 1.5}
    assert ref["missing"] == [{"path": "/etc/x", "reason": "outside_workspace"}]
    assert len(ref["unanswered"]) == 8 and ref["blocked"] == [{"command": "rm -rf x", "reason": "timeout"}]
    assert ref["final_response"] == "done"


def test_finished_without_a_check_is_reported_missing_never_invented(store):
    rid = _waiting(store)
    claim = store.claim(rid, "w1", principal=PRINCIPAL, lease_s=60)
    ref = _finished(store, claim, outputs=[{"path": "/etc/passwd"}])["result_ref"]
    assert ref["block"] == "missing" and ref["outputs"] == [] and ref["summary"] is None
    assert ref["block_detail"] == "no result check was recorded"
