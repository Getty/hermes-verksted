"""The ``verksted`` tool: store a run for the Desktop part to start, report its state, wait for it,
continue it with a follow-up turn, stop it.

The handler never talks to the gateway. It stores the run with every id the start needs, rings
the doorbell and reads the store; the Desktop part does the native session calls. Everything
Hermes-specific is behind ``host`` (see ``hermes_host.HermesHost``), so tests drive the handler
with a fake host.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import config as vconfig
from . import paths as vpaths
from . import result as vresult
from .store import ACTIVE_STATES, FINAL_STATES, StoreError, result_view

TOOL_NAME = "verksted"
TOOLSET = "verksted"

MAX_WAIT_S = 240          # docs/hermes-interfaces.md C18: the per-call deadline is 420 s, async 300 s
DEFAULT_WAIT_S = {"run": 60, "wait": 120, "send": 60, "cancel": 30}
MAX_ACTIVE_RUNS = 4       # per profile store; arch §4 "Parallelität ... erhalten feste Grenzen"
TASK_LIMIT = 32 * 1024
STORE_POLL_S = 1.0
INTERRUPT_POLL_S = 0.25   # C18: at least once a second, so /stop returns within the 3 s grace

ACTIONS = ("run", "status", "wait", "send", "cancel")
_ARG_KEYS = {"action", "folder", "task", "files", "handle", "wait_seconds"}
_STARTING = ("create_requested", "session_created", "toolsets_checked", "submit_requested")

LABELS = {
    "waiting_for_gui": "wartet auf Hermes Desktop",
    "create_requested": "wird gestartet",
    "session_created": "wird gestartet",
    "toolsets_checked": "wird gestartet",
    "submit_requested": "wird gestartet",
    "running": "läuft",
    "finished": "fertig",
    "refused": "abgelehnt",
    "unknown": "unbekannt",
    "send_waiting": "Folgeauftrag wartet auf Hermes Desktop",
    "cancelled": "abgebrochen",
}
STOPPING_LABEL = "wird gestoppt"
# Outcomes of a turn Hermes stopped: "interrupted" (it had started), "cancelled" (still queued).
STOPPED_OUTCOMES = ("interrupted", "cancelled")
WAITING_LABELS = {"clarify": "Rückfrage", "approval": "Freigabe erforderlich"}

DESCRIPTION = (
    "Hand a bounded task to a separate, regular Hermes session that works in a workspace folder, "
    "and collect its result. Hermes Desktop with the Verksted part enabled starts the session; "
    "the user answers its questions and approvals there ('Session öffnen'), never through this tool. "
    "action=run needs folder (absolute path on this machine) and task (self-contained instructions); "
    "it returns a handle and waits up to wait_seconds (default 60, max 240) for the result. "
    "action=status reports a run's state at once; action=wait waits up to wait_seconds "
    "(default 120, max 240) for it to finish or to need the user. A run keeps going when a call "
    "returns; call wait again with the handle. action=send gives a finished run a follow-up task "
    "(handle, task) in the same child session; action=cancel asks to stop a run (handle). "
    "A finished run reports the child's summary and the output paths Verksted checked. "
    "Toolsets and profile come from the .verksted.yaml files from the workspace root down to the "
    "folder, frozen at run; status shows what was resolved."
)

SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS),
                       "description": "run, status, wait, send (follow-up task for a finished run) or cancel."},
            "folder": {"type": "string",
                       "description": "run: absolute path of the workspace folder on the machine Hermes runs on."},
            "task": {"type": "string",
                     "description": "run/send: the task for the child session, complete in itself."},
            "files": {"type": "array", "items": {"type": "string"}, "maxItems": 50,
                      "description": "run/send: optional relevant files, relative to folder."},
            "handle": {"type": "string", "description": "status/wait/send/cancel: the handle a run call returned."},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_S,
                             "description": "run/wait/send/cancel: how long this call may wait for the run "
                                            "(0 = return at once)."},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


class ToolRefused(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **extra}}


def make_handler(host: Any) -> Callable[..., str]:
    """The registry handler. ``**kwargs`` takes the dispatcher's context (``session_id``,
    ``task_id``, ``user_task``); parent ids never come from ``args``."""
    def handler(args: dict, **kwargs: Any) -> str:
        known: dict[str, str] = {}  # the run's handle, once one exists: the caller can still follow it
        try:
            return _dumps(_dispatch(host, args, kwargs, known))
        except ToolRefused as exc:
            return _dumps(_error(exc.code, str(exc), **exc.extra))
        except (vpaths.PathRefused, vconfig.ConfigRefused) as exc:
            return _dumps(_error(exc.code, str(exc)))
        except StoreError as exc:
            return _dumps(_error(exc.code, str(exc)))
        except sqlite3.Error as exc:  # a locked or broken store file (busy_timeout passed, disk, …)
            return _dumps(_error("store_unavailable", f"the Verksted store cannot be used right now: {exc}",
                                 **known))
    return handler


def _dispatch(host: Any, args: Any, kwargs: dict[str, Any], known: dict[str, str]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ToolRefused("bad_args", "arguments must be an object")
    unknown = sorted(set(args) - _ARG_KEYS)
    if unknown:
        raise ToolRefused("bad_args", f"unknown arguments {unknown}; the parent session and profile "
                                      "always come from the calling session, never from arguments")
    action = args.get("action")
    if action not in ACTIONS:
        raise ToolRefused("bad_args", f"action must be one of {list(ACTIONS)}")
    caller = host.caller(kwargs)
    if not (caller.get("session_id") or caller.get("session_key")):
        raise ToolRefused("no_session", "verksted needs a Hermes session context; none was passed to the tool")
    store = host.store()
    if action == "run":
        return _run(host, store, caller, args, known)
    run = _bound_run(store, caller, args.get("handle"))
    known["handle"] = run["run_id"]
    if action == "status":
        return _view(run, host.now())
    if action == "send":
        return _send(host, store, caller, run, args)
    if action == "cancel":
        return _cancel(host, store, run, args)
    seconds = _wait_seconds(args, "wait")
    run, why = _wait(host, store, run, seconds)
    return _view(run, host.now(), wait_end=why)


# -- run ----------------------------------------------------------------------------------------

def _run(host: Any, store: Any, caller: dict[str, Any], args: dict[str, Any],
         known: dict[str, str]) -> dict[str, Any]:
    if store.is_child_session(caller.get("session_id", ""), caller.get("session_key", ""),
                              caller.get("chat_id", "")):
        raise ToolRefused("recursion_refused", "this session is itself a Verksted child session; "
                                              "it cannot start further Verksted runs (depth 1)")
    task = _task(args, "run")
    seconds = _wait_seconds(args, "run")
    backend = host.backend()
    folder, key = vpaths.canonical_folder(args.get("folder"), backend)
    files = vpaths.relative_files(folder, args.get("files"), backend)
    resolved = vconfig.resolve(folder, parent_profile=caller["profile"], parent_profile_home=caller["profile_home"],
                               settings=host.settings(), profile_home=host.profile_home, backend=backend)
    requested, expected = vconfig.start_toolsets(resolved)

    run_id = "vk-" + uuid.uuid4().hex
    request_id = f"{run_id}.create"
    submission_id = f"{run_id}.submit.1"
    create_params: dict[str, Any] = {
        "cwd": folder,
        "cwd_explicit": True,
        "request_id": request_id,
    }  # no title: session.create resolves a title before request_id (gateway/session_controls.py:467-492)
    if requested is not None:
        create_params["toolsets"] = requested
    prompt = _prompt(task, files, folder, resolved.get("workspace") or folder)

    record = {
        "run_id": run_id,
        "parent_session_id": caller.get("session_id") or caller.get("session_key"),
        "parent_session_key": caller.get("session_key") or "",
        "parent_profile": caller["profile"],
        "parent_profile_home": caller["profile_home"],
        "parent_source": caller.get("source") or "",
        "backend_platform": caller["backend_platform"],
        "target_dir": folder,
        "target_key": key,
        "task": task,
        "resolved_config": resolved,
        "config_fingerprint": vconfig.fingerprint(resolved),
        "expected_toolsets": expected,
        "request_id": request_id,
        "create_params": create_params,
        "submission_id": submission_id,
        "prompt_text": prompt,
    }
    try:
        run = store.create_run(record, max_active=MAX_ACTIVE_RUNS)
    except StoreError as exc:
        if exc.code == "folder_busy":
            holder = exc.extra.get("run_id")
            extra = {"handle": holder} if holder and _owns(store, caller, store.get_run(holder)) else {}
            raise ToolRefused("folder_busy", "another Verksted run is still active in this folder; "
                                             "wait for it or pick another folder", **extra) from exc
        raise
    known["handle"] = run_id
    host.ring()
    if seconds:
        run, why = _wait(host, store, run, seconds)
        return _view(run, host.now(), wait_end=why)
    return _view(run, host.now())


def _task(args: dict[str, Any], action: str) -> str:
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ToolRefused("bad_args", f"{action} needs task: the instructions for the child session")
    if len(task.encode("utf-8")) > TASK_LIMIT:
        raise ToolRefused("bad_args", f"task is longer than {TASK_LIMIT} bytes; put details into a file in the folder")
    return task


def _prompt(task: str, files: list[str], folder: str, root: str) -> str:
    """The submitted text: the task, the relevant files, and Verksted's request for the closing
    result block (card k10). It is stored before the start, so a repeated submit is identical."""
    prompt = task.strip()
    if files:
        prompt += (f"\n\nRelevant files (relative to the folder you work in, {folder}):\n"
                   + "\n".join(f"- {f}" for f in files))
    return prompt + vresult.request_text(root)


# -- send and cancel ----------------------------------------------------------------------------

def _send(host: Any, store: Any, caller: dict[str, Any], run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """A follow-up turn in the run's child session, with the configuration frozen at run."""
    task = _task(args, "send")
    seconds = _wait_seconds(args, "send")
    folder = run["target_dir"]
    files = vpaths.relative_files(folder, args.get("files"), host.backend())
    root = (run.get("resolved_config") or {}).get("workspace") or folder
    try:
        run = store.start_send(run["run_id"], task=task, prompt_text=_prompt(task, files, folder, root),
                               max_active=MAX_ACTIVE_RUNS)
    except StoreError as exc:
        if exc.code == "folder_busy":
            holder = exc.extra.get("run_id")
            extra = {"handle": holder} if holder and _owns(store, caller, store.get_run(holder)) else {}
            raise ToolRefused("folder_busy", "another Verksted run is still active in this folder; "
                                             "wait for it first", **extra) from exc
        raise ToolRefused(exc.code, str(exc), **{k: v for k, v in exc.extra.items() if k == "state"}) from exc
    host.ring()
    if seconds:
        run, why = _wait(host, store, run, seconds)
        return _view(run, host.now(), wait_end=why)
    return _view(run, host.now())


def _cancel(host: Any, store: Any, run: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Store a stop request. A run whose prompt was not submitted yet ends ``cancelled`` at once;
    otherwise the window that holds the run asks Hermes to stop the turn, and the stop counts only
    when Hermes' terminal receipt says so (arch §5). Closing a tab is never a stop."""
    seconds = _wait_seconds(args, "cancel")
    answer = store.request_cancel(run["run_id"])
    run, why = answer["run"], None
    if answer["cancel"] == "requested":
        host.ring()
        if seconds:
            run, why = _wait(host, store, run, seconds)
    out = _view(run, host.now(), wait_end=why)
    out["cancel"] = _cancel_verdict(run)
    return out


def _cancel_verdict(run: dict[str, Any]) -> str:
    state = run["state"]
    if state in ("cancelled", "refused"):
        return "not_started"
    if state == "finished":
        return "confirmed" if run.get("outcome") in STOPPED_OUTCOMES else "too_late"
    if state == "unknown":
        return "unknown"
    return "requested"


# -- binding ------------------------------------------------------------------------------------

def _owns(store: Any, caller: dict[str, Any], run: Optional[dict[str, Any]]) -> bool:
    """The stored parent binding (arch §4: "Jeder Zugriff prüft die gespeicherte Elternbindung")."""
    if run is None or run.get("parent_profile_home") != caller.get("profile_home"):
        return False
    key = caller.get("session_key") or ""
    if run.get("parent_session_key") and key:
        return run["parent_session_key"] == key
    sid = caller.get("session_id") or ""
    return bool(sid) and store.resolve_session(sid) == run.get("parent_session_id")


def _bound_run(store: Any, caller: dict[str, Any], handle: Any) -> dict[str, Any]:
    if not isinstance(handle, str) or not handle:
        raise ToolRefused("bad_args", "this action needs the handle a run call returned")
    run = store.get_run(handle)
    if not _owns(store, caller, run):
        # A foreign handle answers exactly like a missing one: a handle alone grants nothing.
        raise ToolRefused("not_found", "no Verksted run with this handle belongs to this session")
    return run


# -- waiting ------------------------------------------------------------------------------------

def _wait_seconds(args: dict[str, Any], action: str) -> int:
    raw = args.get("wait_seconds", DEFAULT_WAIT_S[action])
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ToolRefused("bad_args", "wait_seconds must be a number of seconds")
    return int(max(0, min(MAX_WAIT_S, raw)))


def _waiting_for(run: dict[str, Any]) -> list[dict[str, Any]]:
    if run["state"] != "running":
        return []
    return list((run.get("state_detail") or {}).get("prompts") or [])


def _prompt_ids(run: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(p.get("prompt_id", "") for p in _waiting_for(run)))


def _wait(host: Any, store: Any, run: dict[str, Any], seconds: int) -> tuple[dict[str, Any], str]:
    """Wait until the run is final or newly waits for the user, at most ``seconds``.

    A run that already waits for the user when the call starts does not end the wait at once;
    it ends when its open prompts change. Returns ``(run, reason)`` with reason ``final``,
    ``needs_user``, ``timeout``, ``interrupted`` or ``yielded`` (the user wrote into the parent
    session mid-turn and Hermes asked the tool to give the turn back).
    """
    start_prompts = _prompt_ids(run)
    deadline = host.monotonic() + seconds
    next_read = host.monotonic() + STORE_POLL_S
    while True:
        if run["state"] in FINAL_STATES:
            return run, "final"
        prompts = _prompt_ids(run)
        if prompts and prompts != start_prompts:
            return run, "needs_user"
        now = host.monotonic()
        if now >= deadline:
            return run, "timeout"
        if host.is_interrupted():
            return run, "interrupted"
        if host.yield_requested():
            return run, "yielded"
        host.sleep(min(INTERRUPT_POLL_S, deadline - now))
        if host.monotonic() >= next_read:
            run = store.get_run(run["run_id"]) or run
            next_read = host.monotonic() + STORE_POLL_S


# -- view ---------------------------------------------------------------------------------------

def _view(run: dict[str, Any], now: float, *, wait_end: Optional[str] = None) -> dict[str, Any]:
    state = run["state"]
    prompts = _waiting_for(run)
    waiting = prompts[0]["kind"] if prompts else None
    claimed = (run.get("claim_expires_at") or 0) > now
    stale = state in _STARTING + ("running",) and not claimed
    resolved = run.get("resolved_config") or {}
    stopping = state in ACTIVE_STATES and run.get("cancel_requested_at") is not None
    out: dict[str, Any] = {
        "ok": True,
        "handle": run["run_id"],
        "state": state,
        "label": WAITING_LABELS[waiting] if waiting else STOPPING_LABEL if stopping else LABELS[state],
        "turn": run.get("turn_no") or 1,
        "folder": run["target_dir"],
        "profile": resolved.get("profile") or run["parent_profile"],  # the profile the child runs in
        "config": {  # the state frozen at run (arch §3: "Die GUI zeigt den aufgelösten Stand")
            "workspace": resolved.get("workspace"),
            "toolsets": resolved.get("toolsets"),
            "ceilings": resolved.get("ceilings") or [],
            "files": resolved.get("files") or [],
            "profile_switched": bool(resolved.get("profile_switched")),
            "fingerprint": run.get("config_fingerprint"),
        },
        "toolsets": run.get("expected_toolsets"),
        "effective_toolsets": run.get("effective_toolsets"),
        "session_id": run.get("session_id"),
        "stale": stale,
        "last_known_at": run.get("last_known_at"),
    }
    if waiting:
        out["waiting_for"] = waiting
        out["prompts"] = prompts
    if stopping:
        out["cancel_requested"] = True
    if state == "finished":
        out.update(_finished(run))
    if state in ("refused", "unknown", "cancelled"):
        detail = run.get("state_detail") or {}
        out["reason"] = detail.get("reason")
        out["detail"] = detail.get("detail")
        for key in ("missing", "extra", "enabled_toolsets", "field"):
            if key in detail:
                out[key] = detail[key]
    if wait_end is not None:
        out["wait_end"] = wait_end
        out["still_running"] = state in ACTIVE_STATES
    out["hint"] = _hint(out, state, waiting, stale, wait_end)
    return out


def _finished(run: dict[str, Any]) -> dict[str, Any]:
    """The finished turn's result (card k10): the summary and the output paths the plugin API
    checked when the run finished, plus the clipped final answer."""
    ref = result_view(run.get("result_ref"))
    out: dict[str, Any] = {
        "outcome": run.get("outcome"),
        "result_block": ref["block"],
        "summary": ref.get("summary"),
        "outputs": [{"path": o.get("path"), "relative": o.get("relative"), "kind": o.get("kind"),
                     "size": o.get("size"), "modified_at": _iso(o.get("mtime"))} for o in ref["outputs"]],
        "missing": [{"path": m.get("path"), "reason": m.get("reason")} for m in ref["missing"]],
        "result_truncated": bool(ref.get("result_truncated") or ref.get("truncated")),
        "result": ref.get("final_response") or "",
    }
    if ref["block"] != "ok" and ref.get("block_detail"):
        out["result_block_detail"] = ref["block_detail"]
    if ref.get("unanswered"):
        out["unanswered"] = ref["unanswered"]
    if ref.get("blocked"):
        out["blocked"] = ref["blocked"]
    return out


def _iso(mtime: Any) -> Optional[str]:
    if isinstance(mtime, bool) or not isinstance(mtime, (int, float)):
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _hint(out: dict[str, Any], state: str, waiting: Optional[str], stale: bool,
          wait_end: Optional[str]) -> str:
    if state == "finished":
        return _finished_hint(out)
    if state == "refused":
        return "The run was refused before its task was submitted; reason and detail say what to change."
    if state == "cancelled":
        return ("The run was stopped before its task reached Hermes; nothing ran in this turn. Start a "
                "new run, or send a follow-up task with this handle if the child session exists.")
    if state == "unknown":
        return ("Verksted cannot tell whether the task ran (for example, the gateway restarted mid-turn). "
                "Nothing is retried. Ask the user to open the child session in Hermes Desktop "
                "('Session öffnen') and there discard the lost prompt or send it again.")
    if out.get("cancel_requested"):
        return ("A stop was requested. It counts only when Hermes confirms it; until then the run may "
                "still finish. Call wait with this handle.")
    if wait_end == "yielded":
        return ("The user sent a new message; answer it first. The run keeps going; call wait or status "
                "with the handle afterwards.")
    if waiting == "clarify":
        return ("The child session asks the user a question (Rückfrage). Tell the user to answer it in "
                "Hermes Desktop via 'Session öffnen'; then call wait with this handle.")
    if waiting == "approval":
        return ("The child session needs the user's approval (Freigabe erforderlich). Tell the user to "
                "decide in Hermes Desktop via 'Session öffnen'; an approval not given in time is denied. "
                "Then call wait with this handle.")
    if state in ("waiting_for_gui", "send_waiting"):
        return ("Stored; the run starts as soon as a Hermes Desktop window with the Verksted part enabled "
                "is connected to this profile. Call wait with this handle.")
    if stale:
        return ("No Hermes Desktop window is following this run right now; the state shown is the last "
                "known one (veraltet), not a claim that the run stopped. Call wait or status later.")
    if wait_end == "interrupted":
        return "This call was interrupted; the run keeps going. Call wait or status with the handle."
    return "The run is still going. Call wait with this handle to collect the result."


def _finished_hint(out: dict[str, Any]) -> str:
    parts = []
    if out.get("outcome") == "completed":
        parts.append("The child session finished.")
    elif out.get("outcome") in STOPPED_OUTCOMES:
        parts.append("Hermes stopped the child session's turn; treat its result as partial.")
    else:
        parts.append(f"The child session ended with outcome {out.get('outcome')!r}; treat its result as partial.")
    if out["result_block"] == "ok":
        parts.append("summary and outputs come from its closing block; Verksted checked when the run finished "
                     "that each path in outputs exists inside the workspace (not who wrote it). Paths in "
                     "missing were named but not found or not allowed.")
    else:
        parts.append("It reported no usable result block: no summary and no checked files. Do not guess "
                     "files; its final answer is in result, the full history in the child session "
                     "('Session öffnen').")
    if out.get("unanswered"):
        parts.append("It asked the user a question that got no answer, so its result may rest on an "
                     "assumption; tell the user (they can answer with a follow-up send or in the child session).")
    if out.get("blocked"):
        parts.append("A command it needed approval for did not run (denied or not approved in time); tell the user.")
    if out.get("result_truncated"):
        parts.append("Verksted kept less than the child reported; the rest is in the child session.")
    return " ".join(parts)
