"""Verksted plugin API, mounted by Hermes under ``/api/plugins/verksted``.

The Desktop part reaches it through ``ctx.rest`` to list claimable runs, claim one (lease plus
claim generation) and record each start step. Host authentication is the dashboard's own
middleware, which runs before this router (a native ``X-Hermes-Gateway-Ticket`` from Desktop, or
the session token); this module adds no credential of its own and reads the authenticated
principal from ``request.state.native_http_principal``.

Hermes imports this file standalone by path, possibly in another process than the agent plugin,
so the shared ``store.py`` one folder up is loaded by path too. Request and response bodies
never use a top-level ``profile`` key: for native grants the host checks such a key against the
grant's home and answers 403 ``profile_scope_mismatch``.

SQLite work runs in the threadpool (``run_in_threadpool`` copies the request context, so the
host's per-request profile scope still applies), never on the server's event loop.

Business refusals answer HTTP 200 with ``{"ok": false, "code": ...}`` so the Desktop part always
gets the reason; ``ctx.rest`` turns non-2xx answers into bare errors.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

LEASE_DEFAULT_S = 180.0
LEASE_MIN_S = 30.0
LEASE_MAX_S = 600.0
OWNER_MAX = 128


def _load(name: str):
    path = Path(__file__).resolve().parent.parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"verksted_dashboard_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"verksted: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses (paths.Backend) look their module up there
    spec.loader.exec_module(module)
    return module


_vstore = _load("store")
_vpaths = _load("paths")    # the backend's path rules, for the output check (card k10)
_vresult = _load("result")
router = APIRouter()


def _store():
    """The store of the profile this request is scoped to (the host binds it per request)."""
    return _vstore.Store.for_active_profile()


def _profile_home() -> str:
    from hermes_constants import get_hermes_home
    return str(get_hermes_home().resolve())


def _same_home(a: str, b: str) -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(p))
    return norm(a) == norm(b)


def _principal(request: Request) -> dict[str, Any]:
    native = getattr(request.state, "native_http_principal", None)
    if isinstance(native, dict):
        return {k: native.get(k) for k in ("instance_id", "profile_id", "subject", "purpose")}
    return {"auth": "token"}


def _refuse(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, **extra}


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _owner(body: dict[str, Any]) -> str:
    owner = body.get("owner")
    if not isinstance(owner, str) or not owner or len(owner) > OWNER_MAX:
        raise _vstore.StoreError("bad_owner", "owner must be a non-empty string of at most 128 characters")
    return owner


def _generation(body: dict[str, Any]) -> int:
    gen = body.get("generation")
    if isinstance(gen, bool) or not isinstance(gen, int):
        raise _vstore.StoreError("bad_generation", "generation must be the integer the claim returned")
    return gen


def _lease(body: dict[str, Any]) -> float:
    raw = body.get("lease_s", LEASE_DEFAULT_S)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raw = LEASE_DEFAULT_S
    return max(LEASE_MIN_S, min(LEASE_MAX_S, float(raw)))


def _session_profile(run: dict[str, Any]) -> tuple[str, bool]:
    """The profile the child session runs in, frozen at run (card k5), and whether it is another
    one than the parent's."""
    resolved = run.get("resolved_config") or {}
    profile = resolved.get("profile") or run["parent_profile"]
    return profile, profile != run["parent_profile"]


def _summary(run: dict[str, Any], now: float) -> dict[str, Any]:
    detail = run.get("state_detail") or {}
    resolved = run.get("resolved_config") or {}
    session_profile, switched = _session_profile(run)
    out = {
        "run_id": run["run_id"],
        "state": run["state"],
        "folder": run["target_dir"],
        "task_preview": (run.get("task") or "")[:200],
        "parent_profile": run["parent_profile"],
        # The Desktop part starts the session on this profile's route of its own connection. Verksted's
        # own toolset is tolerated only in the parent's profile, where the recursion guard sees children.
        "session_profile": session_profile,
        "own_toolset_tolerated": not switched,
        "config": {"workspace": resolved.get("workspace"), "toolsets": resolved.get("toolsets"),
                   "ceilings": resolved.get("ceilings") or [],
                   "files": [f.get("path") if isinstance(f, dict) else f for f in resolved.get("files") or []],
                   "fingerprint": run.get("config_fingerprint")},
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "last_known_at": run.get("last_known_at"),
        "expected_toolsets": run.get("expected_toolsets"),
        "effective_toolsets": run.get("effective_toolsets"),
        "session_id": run.get("session_id"),
        "admission_id": run.get("admission_id"),
        "identity": {k: run.get(k) for k in ("connection_id", "route_profile", "target_profile",
                                             "profile_id", "instance_id")},
        "claim": {"owner": run.get("claim_owner"), "generation": run.get("claim_generation"),
                  "expires_at": run.get("claim_expires_at"),
                  "live": (run.get("claim_expires_at") or 0) > now},
        "outcome": run.get("outcome"),
        "turn": run.get("turn_no") or 1,
        # A stop the tool stored (card k8): the window that holds the run asks Hermes to stop it.
        "cancel_requested": run.get("cancel_requested_at") is not None,
    }
    if run["state"] == "running":
        out["receipt_status"] = detail.get("receipt_status")
        out["prompts"] = detail.get("prompts") or []
    if run["state"] in ("refused", "unknown", "cancelled"):
        out["reason"] = detail.get("reason")
        out["detail"] = detail.get("detail")
    if run["state"] == "finished":
        result = _vstore.result_view(run.get("result_ref"))
        out["result_preview"] = (result.get("summary") or result.get("final_response") or "")[:500]
        out["result_block"] = result["block"]
        out["outputs"] = [{"relative": o.get("relative"), "path": o.get("path"), "kind": o.get("kind")}
                          for o in result["outputs"]]
        out["missing"] = result["missing"]
        out["unanswered"] = result["unanswered"]
        out["blocked"] = result["blocked"]
    return out


def _work(run: dict[str, Any], now: float) -> dict[str, Any]:
    """What a claimant needs to start or reconcile: the stored calls, exactly as stored, and the
    stored identity including the principal subject it was requested under (lists leave it out)."""
    summary = _summary(run, now)
    return {**summary, "identity": {**summary["identity"], "subject": run.get("subject")},
            "request_id": run["request_id"], "create_params": run["create_params"],
            "submission_id": run["submission_id"], "prompt_text": run["prompt_text"]}


def _now() -> float:
    return time.time()


def _call(fn, *args: Any, **kwargs: Any):
    return run_in_threadpool(fn, *args, **kwargs)


def _list(claimable_only: bool) -> dict[str, Any]:
    store, now = _store(), _now()
    runs = store.claimable_runs(limit=20) if claimable_only else store.list_runs(limit=50)
    return {"ok": True, "profile_home": _profile_home(), "runs": [_summary(r, now) for r in runs]}


def _claim(run_id: str, body: dict[str, Any], principal: dict[str, Any]) -> dict[str, Any]:
    owner = _owner(body)
    store = _store()
    # The route's runtime.describe profile_id; null only when describe failed (a legacy route),
    # where the claim exists to record the refusal.
    profile_id = body.get("profile_id")
    if profile_id is not None:
        if not isinstance(profile_id, str) or not profile_id:
            return _refuse("bad_profile_id", "profile_id must be the route's runtime.describe profile_id or null")
        run = store.get_run(run_id)
        if run is not None and _session_profile(run)[1]:
            # A switched run (k5): the route must serve the session profile frozen at run, while the
            # store stays this (the parent's) profile's.
            home = (run.get("resolved_config") or {}).get("profile_home") or ""
            if not home or not _same_home(profile_id, home):
                return _refuse("profile_mismatch", "the route's gateway serves another profile home than the "
                                                   "run's session profile", profile_home=home)
        else:
            home = _profile_home()
            if not _same_home(profile_id, home):
                return _refuse("profile_mismatch", "the route's gateway serves another profile home than this store",
                               profile_home=home)
    granted = store.claim(run_id, owner, principal=principal, lease_s=_lease(body))
    # The subject this claim is authenticated as: the Desktop part compares it with the stored one
    # before it repeats any start call (the store compares it again on every step).
    return {"ok": True, "generation": granted["generation"], "expires_at": granted["expires_at"],
            "mode": granted["mode"], "subject": principal.get("subject"), "run": _work(granted["run"], _now())}


def _renew(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **_store().renew(run_id, _owner(body), _generation(body), lease_s=_lease(body))}


def _release(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **_store().release(run_id, _owner(body), _generation(body))}


def _step(run_id: str, body: dict[str, Any], principal: dict[str, Any]) -> dict[str, Any]:
    name = body.get("step")
    if not isinstance(name, str):
        return _refuse("bad_step", "step must be a state name")
    data = dict(body.get("data")) if isinstance(body.get("data"), dict) else {}
    store = _store()
    if name == "finished":
        # The output check (card k10) runs here, on the backend, when the run finishes: the final
        # answer's closing block is parsed and each named path checked against the workspace root
        # frozen at run. Whatever result the Desktop part sent along is replaced.
        data.pop("result", None)
        run = store.get_run(run_id)
        if run is not None:
            root = (run.get("resolved_config") or {}).get("workspace") or run["target_dir"]
            data["result"] = _vresult.check(data.get("final_response"), root, _vpaths.LOCAL)
    run = store.record_step(run_id, _owner(body), _generation(body), name, data, principal=principal)
    return {"ok": True, "run": _summary(run, _now())}


async def _answer(fn, *args: Any) -> dict[str, Any]:
    try:
        return await _call(fn, *args)
    except _vstore.StoreError as exc:
        return _refuse(exc.code, str(exc), **exc.extra)


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    return await _answer(_list, False)


@router.get("/runs/claimable")
async def claimable() -> dict[str, Any]:
    return await _answer(_list, True)


@router.post("/runs/{run_id}/claim")
async def claim(run_id: str, request: Request) -> dict[str, Any]:
    return await _answer(_claim, run_id, await _body(request), _principal(request))


@router.post("/runs/{run_id}/renew")
async def renew(run_id: str, request: Request) -> dict[str, Any]:
    return await _answer(_renew, run_id, await _body(request))


@router.post("/runs/{run_id}/release")
async def release(run_id: str, request: Request) -> dict[str, Any]:
    return await _answer(_release, run_id, await _body(request))


@router.post("/runs/{run_id}/step")
async def step(run_id: str, request: Request) -> dict[str, Any]:
    return await _answer(_step, run_id, await _body(request), _principal(request))
