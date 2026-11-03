"""Small flows shared by tests: the calls a Desktop part makes, spelled once."""
from __future__ import annotations

import time

from .backend import HarnessError


def create_session(win, *, request_id: str, cwd: str, toolsets=None, **extra) -> dict:
    params = {"request_id": request_id, "cwd": cwd, "source": "tool", **extra}
    if toolsets is not None:
        params["toolsets"] = toolsets
    return win.request_profile("session.create", **params)


def submit(win, session_id: str, text: str, submission_id: str) -> dict:
    return win.request_profile("prompt.submit", session_id=session_id, text=text, submission_id=submission_id)


def wait_terminal(win, session_id: str, admission_id: str, timeout=90) -> dict:
    """Poll prompt.receipt (include_result) until the admission is terminal. 'Accepted' is not 'done'."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = win.request_profile("prompt.receipt", session_id=session_id, admission_id=admission_id, include_result=True)
        if last["status"] == "terminal":
            return last
        time.sleep(0.3)
    raise HarnessError(f"admission {admission_id} not terminal in {timeout}s; last receipt: {last}")


def wait_prompts_open(win, session_id: str, kind: str, timeout=60) -> dict:
    """The child shows an open prompt (what Verksted must derive 'Rückfrage' / 'Freigabe' from): session.resume prompts[]."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = win.request_profile("session.resume", session_id=session_id, defer_history=True, omit_messages=True)
        hit = [p for p in snap.get("prompts", []) if p.get("kind") == kind]
        if hit:
            return hit[0]
        time.sleep(0.3)
    raise HarnessError(f"no open {kind} prompt on {session_id} within {timeout}s")
