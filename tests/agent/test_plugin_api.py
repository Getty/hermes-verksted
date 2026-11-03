"""The plugin API over HTTP: loaded by path the way the dashboard host loads it, mounted under
/api/plugins/verksted, with a test middleware standing in for the host's authenticated principal.
The real host (auth middleware, mount, profile scope) is covered by test_hermes_contract.py."""

from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from conftest import PACKAGE
from test_store import IDENTITY, Clock, run_record

BASE = "/api/plugins/verksted"


def _load_plugin_api():
    spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_verksted",
                                                  PACKAGE / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Api:
    def __init__(self, client: TestClient, store, clock: Clock, home: str, module) -> None:
        self.client, self.store, self.clock, self.home, self.module = client, store, clock, home, module

    def get(self, path: str, subject: str | None = None) -> dict:
        res = self.client.get(BASE + path, headers=_headers(subject))
        assert res.status_code == 200, res.text
        return res.json()

    def post(self, path: str, body=None, subject: str | None = None, raw: bytes | None = None) -> dict:
        kwargs = {"content": raw} if raw is not None else {"json": body}
        res = self.client.post(BASE + path, headers=_headers(subject), **kwargs)
        assert res.status_code == 200, res.text
        return res.json()

    def claim(self, run_id: str, owner: str = "win-a", subject: str | None = "user-1", **body) -> dict:
        return self.post(f"/runs/{run_id}/claim", {"owner": owner, "profile_id": self.home, **body}, subject)

    def step(self, run_id: str, generation: int, step: str, data=None, owner: str = "win-a",
             subject: str | None = "user-1") -> dict:
        return self.post(f"/runs/{run_id}/step",
                         {"owner": owner, "generation": generation, "step": step, "data": data or {}}, subject)


def _headers(subject: str | None) -> dict:
    return {"X-Test-Subject": subject} if subject else {}


@pytest.fixture
def api(tmp_path, monkeypatch):
    module = _load_plugin_api()
    clock = Clock()
    # The store class the module loaded by path itself: its StoreError is the one the routes catch.
    store = module._vstore.Store.at_path(tmp_path / "plugin-data" / "verksted" / "data.db", clock=clock)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(module, "_store", lambda: store)
    monkeypatch.setattr(module, "_profile_home", lambda: str(home.resolve()))
    monkeypatch.setattr(module, "_now", clock)

    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):  # what the host's native-auth middleware sets
        subject = request.headers.get("X-Test-Subject")
        if subject:
            request.state.native_http_principal = {"instance_id": "inst-1", "profile_id": str(home.resolve()),
                                                   "subject": subject, "purpose": "plugin", "secret": "x"}
        return await call_next(request)

    app.include_router(module.router, prefix=BASE)
    with TestClient(app) as client:
        yield Api(client, store, clock, str(home.resolve()), module)


def test_lists_show_summaries_and_only_a_claim_hands_out_the_stored_calls(api):
    a = api.store.create_run(run_record(1, "/work/a"), max_active=4)
    b = api.store.create_run(run_record(2, "/work/b"), max_active=4)
    granted = api.claim(b["run_id"])
    assert granted["ok"] and granted["generation"] == 1 and granted["mode"] == "start"

    claimable = api.get("/runs/claimable")
    assert claimable["ok"] and claimable["profile_home"] == api.home
    assert [r["run_id"] for r in claimable["runs"]] == [a["run_id"]]  # b holds a live claim
    assert {r["run_id"] for r in api.get("/runs")["runs"]} == {a["run_id"], b["run_id"]}
    for summary in claimable["runs"] + api.get("/runs")["runs"]:
        assert not {"create_params", "prompt_text", "request_id", "submission_id"} & set(summary)

    work = granted["run"]
    rec = run_record(2, "/work/b")
    for key in ("create_params", "prompt_text", "request_id", "submission_id"):
        assert work[key] == rec[key], key
    assert work["claim"]["owner"] == "win-a" and work["claim"]["live"] is True


def test_no_body_carries_a_top_level_profile_key(api):
    """The host checks a top-level ``profile`` key of native requests against the grant's home."""
    run = api.store.create_run(run_record(), max_active=4)
    answers = [api.get("/runs"), api.get("/runs/claimable"), api.claim(run["run_id"])]
    answers.append(api.step(run["run_id"], 1, "create_requested", {"identity": IDENTITY}))
    assert all("profile" not in answer for answer in answers)


@pytest.mark.parametrize("body, raw", [({}, None), ({"owner": ""}, None), ({"owner": 7}, None),
                                       ({"owner": "x" * 129}, None), (None, b"not json"), (None, b"[1]")])
def test_a_claim_needs_an_owner(api, body, raw):
    run = api.store.create_run(run_record(), max_active=4)
    answer = api.post(f"/runs/{run['run_id']}/claim", body, raw=raw)
    assert answer == {**answer, "ok": False, "code": "bad_owner"}
    assert api.store.get_run(run["run_id"])["claim_generation"] == 0


def test_a_route_of_another_profile_home_cannot_claim(api, tmp_path):
    run = api.store.create_run(run_record(), max_active=4)
    answer = api.claim(run["run_id"], profile_id=str(tmp_path / "elsewhere"))
    assert answer["code"] == "profile_mismatch" and answer["profile_home"] == api.home
    assert api.claim(run["run_id"], profile_id="")["code"] == "bad_profile_id"
    assert api.claim(run["run_id"], profile_id=["x"])["code"] == "bad_profile_id"
    assert api.store.get_run(run["run_id"])["claim_generation"] == 0

    link = tmp_path / "home-link"
    link.symlink_to(api.home)
    assert api.claim(run["run_id"], profile_id=str(link))["ok"]  # the same home, spelled differently


def _switched_record(n, folder, research_home):
    return run_record(n, folder, resolved_config={
        "version": 1, "workspace": folder, "registered": False, "profile": "research",
        "profile_home": research_home, "profile_switched": True, "toolsets": ["file"],
        "ceilings": [{"source": f"{folder}/.verksted.yaml", "toolsets": ["file"]}],
        "files": [{"path": f"{folder}/.verksted.yaml", "sha256": "sha256:abc"}]})


def test_a_run_in_another_profile_is_claimed_with_that_profiles_route(api, tmp_path):
    research = tmp_path / "research-home"
    research.mkdir()
    run = api.store.create_run(_switched_record(1, "/work/a", str(research.resolve())), max_active=4)
    # The parent's own route serves the store's home, not the session profile: refused.
    answer = api.claim(run["run_id"], profile_id=api.home)
    assert answer["code"] == "profile_mismatch" and answer["profile_home"] == str(research.resolve())
    granted = api.claim(run["run_id"], profile_id=str(research))
    assert granted["ok"] and granted["run"]["session_profile"] == "research"
    assert granted["run"]["own_toolset_tolerated"] is False
    assert granted["run"]["parent_profile"] == "default"


def test_summaries_show_the_resolved_state(api):
    same = api.store.create_run(run_record(1, "/work/a"), max_active=4)
    switched = api.store.create_run(_switched_record(2, "/work/b", "/homes/research"), max_active=4)
    runs = {r["run_id"]: r for r in api.get("/runs")["runs"]}
    assert runs[same["run_id"]]["session_profile"] == "default"
    assert runs[same["run_id"]]["own_toolset_tolerated"] is True
    config = runs[switched["run_id"]]["config"]
    assert config == {"workspace": "/work/b", "toolsets": ["file"],
                      "ceilings": [{"source": "/work/b/.verksted.yaml", "toolsets": ["file"]}],
                      "files": ["/work/b/.verksted.yaml"], "fingerprint": "sha256:x"}


def test_a_legacy_route_claims_without_profile_id_to_record_the_refusal(api):
    run = api.store.create_run(run_record(), max_active=4)
    granted = api.claim(run["run_id"], profile_id=None)
    assert granted["ok"]
    closed = api.step(run["run_id"], granted["generation"], "refused",
                      {"reason": "legacy_route", "detail": "runtime.describe is not available"})
    assert closed["ok"] and closed["run"]["state"] == "refused" and closed["run"]["reason"] == "legacy_route"


def test_a_live_claim_of_another_window_wins_until_its_lease_expires(api):
    run = api.store.create_run(run_record(), max_active=4)
    first = api.claim(run["run_id"], owner="win-a", lease_s=60)
    refused = api.claim(run["run_id"], owner="win-b")
    assert refused["code"] == "claimed" and refused["expires_at"] == first["expires_at"]
    api.clock.t += 61
    second = api.claim(run["run_id"], owner="win-b")
    assert second["ok"] and second["generation"] == 2


def test_lease_is_clamped(api):
    run = api.store.create_run(run_record(), max_active=4)
    t = api.clock.t
    assert api.claim(run["run_id"], lease_s=1)["expires_at"] == t + api.module.LEASE_MIN_S
    assert api.claim(run["run_id"], lease_s=10**6)["expires_at"] == t + api.module.LEASE_MAX_S
    assert api.claim(run["run_id"], lease_s="soon")["expires_at"] == t + api.module.LEASE_DEFAULT_S


def test_steps_are_fenced_by_owner_generation_and_lease(api):
    run = api.store.create_run(run_record(), max_active=4)
    rid = run["run_id"]
    old = api.claim(rid, lease_s=60)["generation"]
    new = api.claim(rid, lease_s=60)["generation"]  # the same window claimed again: generation 2
    assert new == old + 1
    stale = api.step(rid, old, "create_requested", {"identity": IDENTITY})
    assert stale["code"] == "stale_generation" and stale["generation"] == new
    assert api.step(rid, new, "create_requested", {"identity": IDENTITY}, owner="win-b")["code"] == "stale_generation"
    for bad in ("1", None, True, 1.5):
        assert api.post(f"/runs/{rid}/step", {"owner": "win-a", "generation": bad, "step": "refused",
                                              "data": {"reason": "x"}})["code"] == "bad_generation"
    api.clock.t += 61
    assert api.step(rid, new, "create_requested", {"identity": IDENTITY})["code"] == "lease_expired"
    assert api.store.get_run(rid)["state"] == "waiting_for_gui"


def test_steps_follow_the_start_path_and_keep_the_identity(api):
    run = api.store.create_run(run_record(), max_active=4)
    rid = run["run_id"]
    gen = api.claim(rid)["generation"]
    jumped = api.step(rid, gen, "running", {"identity": IDENTITY, "admission_id": "adm-1"})
    assert jumped["code"] == "bad_transition" and jumped["state"] == "waiting_for_gui"
    for bad in (7, None, "not-a-state"):
        assert api.post(f"/runs/{rid}/step", {"owner": "win-a", "generation": gen,
                                              "step": bad})["code"] == "bad_step"

    assert api.step(rid, gen, "create_requested", {"identity": IDENTITY})["run"]["state"] == "create_requested"
    other_user = api.step(rid, gen, "session_created", {"identity": IDENTITY, "session_id": "s-1"},
                          subject="user-2")
    assert other_user["code"] == "identity_mismatch" and other_user["fields"] == ["subject"]
    other_route = api.step(rid, gen, "session_created",
                           {"identity": {**IDENTITY, "connection_id": "remote-1"}, "session_id": "s-1"})
    assert other_route["code"] == "identity_mismatch" and other_route["fields"] == ["connection_id"]

    created = api.step(rid, gen, "session_created",
                       {"identity": IDENTITY, "session_id": "s-1", "enabled_toolsets": ["file"]})
    assert created["run"]["session_id"] == "s-1" and created["run"]["effective_toolsets"] == ["file"]
    api.step(rid, gen, "toolsets_checked", {"identity": IDENTITY, "enabled_toolsets": ["file"]})
    api.step(rid, gen, "submit_requested", {"identity": IDENTITY})
    running = api.step(rid, gen, "running", {"identity": IDENTITY, "admission_id": "adm-1", "prompts": [
        {"kind": "approval", "prompt_id": "p-1", "command": "rm -rf build", "secret": "dropped"}]})
    assert running["run"]["admission_id"] == "adm-1"
    assert running["run"]["prompts"] == [{"kind": "approval", "prompt_id": "p-1", "command": "rm -rf build"}]
    done = api.step(rid, gen, "finished", {"identity": IDENTITY, "outcome": "completed", "final_response": "hi"})
    assert done["run"]["state"] == "finished" and done["run"]["result_preview"] == "hi"
    assert api.claim(rid)["code"] == "final"


def test_the_principal_of_the_host_auth_is_stored_with_the_claim(api):
    a = api.store.create_run(run_record(1, "/work/a"), max_active=4)
    b = api.store.create_run(run_record(2, "/work/b"), max_active=4)
    api.claim(a["run_id"], subject="user-1")
    api.claim(b["run_id"], subject=None)  # the session token: no native principal
    # Only the four principal fields are kept; anything else the host puts there is dropped.
    assert api.store.get_run(a["run_id"])["claim_principal"] == {
        "instance_id": "inst-1", "profile_id": api.home, "subject": "user-1", "purpose": "plugin"}
    assert api.store.get_run(b["run_id"])["claim_principal"] == {"auth": "token"}


def test_a_claim_names_its_principal_subject_and_the_work_carries_the_stored_one(api):
    # k3 fix 5: the Desktop part compares the subject before any re-send, so it must see both.
    run = api.store.create_run(run_record(), max_active=4)
    rid = run["run_id"]
    gen = api.claim(rid, subject="user-1")["generation"]
    api.step(rid, gen, "create_requested", {"identity": IDENTITY})
    api.post(f"/runs/{rid}/release", {"owner": "win-a", "generation": gen})
    again = api.claim(rid, owner="win-b", subject="user-2")
    assert again["subject"] == "user-2"
    assert again["run"]["identity"]["subject"] == "user-1"
    assert "subject" not in api.get("/runs")["runs"][0]["identity"]  # lists never show it


def test_renew_and_release(api):
    run = api.store.create_run(run_record(), max_active=4)
    rid = run["run_id"]
    gen = api.claim(rid, lease_s=60)["generation"]
    api.clock.t += 50
    renewed = api.post(f"/runs/{rid}/renew", {"owner": "win-a", "generation": gen, "lease_s": 60})
    assert renewed["ok"] and renewed["expires_at"] == api.clock.t + 60
    assert api.post(f"/runs/{rid}/renew", {"owner": "win-a", "generation": gen - 1})["code"] == "stale_generation"
    assert api.post(f"/runs/{rid}/release", {"owner": "win-a", "generation": gen})["ok"]
    assert [r["run_id"] for r in api.get("/runs/claimable")["runs"]] == [rid]
    assert api.post(f"/runs/{rid}/renew", {"owner": "win-a", "generation": gen})["code"] == "lease_expired"
    assert api.post("/runs/vk-missing/release", {"owner": "win-a", "generation": 1})["code"] == "not_found"


def test_store_work_never_runs_on_the_event_loop(api, monkeypatch):
    seen = []
    real = api.store

    def store():
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("worker thread")
        return real

    monkeypatch.setattr(api.module, "_store", store)
    run = real.create_run(run_record(), max_active=4)
    api.get("/runs")
    api.get("/runs/claimable")
    gen = api.claim(run["run_id"])["generation"]
    api.step(run["run_id"], gen, "create_requested", {"identity": IDENTITY})
    api.post(f"/runs/{run['run_id']}/renew", {"owner": "win-a", "generation": gen})
    api.post(f"/runs/{run['run_id']}/release", {"owner": "win-a", "generation": gen})
    assert seen == ["worker thread"] * 6


def test_refusals_are_answered_with_their_reason(api):
    assert api.claim("vk-missing") == {**api.claim("vk-missing"), "ok": False, "code": "not_found"}
    run = api.store.create_run(run_record(), max_active=4)
    gen = api.claim(run["run_id"])["generation"]
    api.step(run["run_id"], gen, "refused", {"reason": "toolsets_exceeded", "detail": "web",
                                             "extra": ["web"]})
    summary = api.get("/runs")["runs"][0]
    assert summary["state"] == "refused" and summary["reason"] == "toolsets_exceeded"
    assert json.dumps(summary)  # plain JSON
