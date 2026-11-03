"""The USER answering prompts in the native chat of a child session.

The real Desktop does this in its chat UI: a second viewer socket attached to the session, answering
with the native respond methods. The plugin never does (invariant 9); this class is test-side only.

- approval:  event ``approval.request`` -> ``approval.respond {session_id, execution_generation, prompt_id, choice}``
- clarify (per-question): event ``clarify.request`` -> ``clarify.respond {..., prompt_id, answer}``
- clarify (batch, when the submitter sent ``clarify_batch`` as the Desktop client does): a server->client
  request ``clarify`` with ``questions`` -> a JSON-RPC response ``{answers: {qid: text}, outcome}`` on the
  socket that received it.
"""
from __future__ import annotations

import json
import threading
import time

from websockets.sync.client import connect

from .backend import TICKET_PREFIX, WS_PROTOCOL, Backend, HarnessError


class UserChat:
    def __init__(self, backend: Backend, *, advertise_server_requests: bool = True):
        self.backend = backend
        self.ws = connect(backend.ws_url, subprotocols=[WS_PROTOCOL, TICKET_PREFIX + backend.mint_ticket("interactive")],
                          open_timeout=15, max_size=None)
        self.frames: list[dict] = []
        self._cv = threading.Condition()
        self._n = 0
        self._results: dict = {}
        self._closed = False
        threading.Thread(target=self._reader, daemon=True).start()
        if advertise_server_requests:
            # What the app's socket does on gateway.ready. Mirrored for fidelity only: the k8 probe showed it
            # plays no part -- a batch clarify reaches any socket attached to the session (with
            # session:respond), advertised or not.
            self.call("client.capabilities", server_requests=True)

    def _reader(self):
        try:
            for raw in self.ws:
                f = json.loads(raw)
                with self._cv:
                    if "id" in f and "method" not in f:
                        self._results[f["id"]] = f
                    else:
                        self.frames.append(f)
                    self._cv.notify_all()
        except Exception:
            pass
        finally:
            with self._cv:
                self._closed = True
                self._cv.notify_all()

    def call(self, method: str, timeout=30, **params):
        with self._cv:
            self._n += 1
            rid = f"u{self._n}"
        self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        end = time.monotonic() + timeout
        with self._cv:
            while rid not in self._results:
                left = end - time.monotonic()
                if left <= 0 or self._closed:
                    raise HarnessError(f"user socket: no reply to {method}")
                self._cv.wait(left)
            f = self._results.pop(rid)
        if "error" in f:
            raise HarnessError(f"{method} refused: {f['error']}")
        return f["result"]

    # --- attach -----------------------------------------------------------------------------------
    def open_chat(self, session_id: str) -> dict:
        """Open the child session in this viewer (what 'Session öffnen' does): session.resume."""
        self.snapshot = self.call("session.resume", session_id=session_id, defer_history=True, omit_messages=True)
        return self.snapshot

    # --- finding prompts -----------------------------------------------------------------------------
    def _events(self, session_id, kind):
        with self._cv:
            return [f["params"] for f in self.frames if f.get("method") == "event" and f["params"].get("session_id") == session_id
                    and f["params"]["type"] == kind]

    def _server_requests(self, session_id, method):
        with self._cv:
            return [f for f in self.frames if f.get("method") == method and "id" in f
                    and str(f["id"]).startswith("srq-") and f["params"].get("session_id") == session_id]

    def wait_prompt(self, session_id: str, kind: str, timeout=60) -> dict:
        """Wait until the session shows an open ``clarify`` / ``approval`` prompt. Returns
        ``{'via': 'event'|'server_request'|'open_requests'|'snapshot', ...}``. Fails loud on timeout."""
        end = time.monotonic() + timeout
        last_refresh = time.monotonic()
        while time.monotonic() < end:
            with self._cv:
                settled = {f["params"]["payload"].get("prompt_id") for f in self.frames
                           if f.get("method") == "event" and f["params"]["type"] == f"{kind}.settled"}
                cancelled = {f["params"]["payload"].get("id") for f in self.frames
                             if f.get("method") == "event" and f["params"]["type"] == "request.cancel"}
            for e in self._events(session_id, f"{kind}.request"):
                if e["payload"].get("prompt_id") not in settled:
                    return {"via": "event", "payload": e["payload"], "execution_generation": e.get("execution_generation") or e["payload"].get("execution_generation")}
            if kind == "clarify":
                for f in self._server_requests(session_id, "clarify"):
                    if f["id"] not in cancelled:
                        return {"via": "server_request", "id": f["id"], "payload": f["params"]}
            snap = getattr(self, "snapshot", None) or {}
            for p in snap.get("prompts", []):
                if p.get("kind") == kind and p.get("prompt_id") not in settled:
                    return {"via": "snapshot", "payload": p, "execution_generation": p.get("execution_generation")}
            if kind == "clarify":
                # A batch clarify is NOT in prompts[]: it is an open server request, listed in open_requests.
                for r in snap.get("open_requests", []):
                    if r.get("method") == "clarify" and str(r.get("id", "")).startswith("srq-") and r["id"] not in cancelled \
                            and r["params"].get("session_id") == session_id:
                        return {"via": "open_requests", "id": r["id"], "payload": r["params"]}
            if time.monotonic() - last_refresh > 2 and getattr(self, "snapshot", None) is not None:
                # What reopening the tab does: resume again and read what is still open.
                last_refresh = time.monotonic()
                self.snapshot = self.call("session.resume", session_id=session_id, defer_history=True, omit_messages=True)
            time.sleep(0.1)
        raise HarnessError(f"no {kind} prompt on {session_id} within {timeout}s; frames: {[f.get('method') or 'result' for f in self.frames][-12:]}")

    # --- answering ---------------------------------------------------------------------------------------
    def answer_clarify(self, session_id: str, text: str, timeout=60) -> dict:
        p = self.wait_prompt(session_id, "clarify", timeout)
        if p["via"] in ("server_request", "open_requests"):
            qids = [q["qid"] for q in p["payload"]["questions"]]
            self.ws.send(json.dumps({"jsonrpc": "2.0", "id": p["id"], "result": {"answers": {q: text for q in qids}, "outcome": "submitted"}}))
        else:
            pay = p["payload"]
            self.call("clarify.respond", session_id=session_id, execution_generation=p["execution_generation"],
                      prompt_id=pay["prompt_id"], answer=text)
        return p

    def respond_approval(self, session_id: str, choice: str, timeout=60) -> dict:
        p = self.wait_prompt(session_id, "approval", timeout)
        self.call("approval.respond", session_id=session_id, execution_generation=p["execution_generation"],
                  prompt_id=p["payload"]["prompt_id"], choice=choice)
        return p

    def deny_approval(self, session_id: str, timeout=60) -> dict:
        return self.respond_approval(session_id, "deny", timeout)

    def approve_once(self, session_id: str, timeout=60) -> dict:
        return self.respond_approval(session_id, "once", timeout)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
