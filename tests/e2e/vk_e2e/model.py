"""Scripted loopback OpenAI-compatible model.

A *rule* matches a marker in the latest user message; its *steps* answer the successive LLM rounds
of that turn (round i = number of assistant messages after that user message). Past the last step the
model answers ``DONE``. Requests that match no rule get ``DONE`` too. Everything the gateway sent is
recorded in ``requests`` (tool names offered, last user text, ...) so tests can assert on it.

    model.when("ASK_COLOR", Clarify("Pick a color"), Final(lambda c: f"you said: {c.last_tool}"))
    model.when("RM_FILE", Terminal("echo x > out.txt"), Final(lambda c: "tool said: " + c.last_tool))
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


@dataclass
class Ctx:
    last_tool: str
    user: str
    round: int


@dataclass
class Step:
    text: str | Callable[[Ctx], str] | None = None
    calls: list[tuple[str, dict]] = field(default_factory=list)
    gate: str | None = None  # block this round until model.release(gate)


def Final(text, gate=None) -> Step:
    return Step(text=text, gate=gate)


def Call(name: str, args: dict, gate=None) -> Step:
    return Step(calls=[(name, args)], gate=gate)


def Calls(*calls: tuple[str, dict], gate=None) -> Step:
    return Step(calls=list(calls), gate=gate)


def Clarify(question: str, choices: list[str] | None = None, gate=None) -> Step:
    """A clarify tool call. No ``choices`` = free-text question."""
    q: dict[str, Any] = {"question": question}
    if choices:
        q["choices"] = choices
    return Call("clarify", {"questions": [q]}, gate=gate)


def Terminal(command: str, gate=None) -> Step:
    """A terminal call; under ``approvals.mode: manual`` a command that writes needs approval."""
    return Call("terminal", {"command": command}, gate=gate)


def WriteFile(path: str, content: str, gate=None) -> Step:
    return Call("write_file", {"path": path, "content": content}, gate=gate)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


class ScriptedModel:
    def __init__(self):
        self.rules: list[tuple[str, list[Step]]] = []
        self.requests: list[dict] = []
        self._gates: dict[str, threading.Event] = {}
        self._hit: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._ids = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data": [{"id": "local-wire-stub", "object": "model"}]}')

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer._answer(self, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        for ev in self._gates.values():
            ev.set()
        self.server.shutdown()
        self.server.server_close()

    def when(self, marker: str, *steps: Step):
        self.rules.append((marker, list(steps)))
        for s in steps:
            if s.gate:
                self._gates.setdefault(s.gate, threading.Event())
                self._hit.setdefault(s.gate, threading.Event())

    def gate_hit(self, name: str, timeout=30) -> bool:
        return self._hit[name].wait(timeout)

    def release(self, name: str):
        self._gates[name].set()

    # --- request handling -------------------------------------------------------------------
    def _answer(self, h: BaseHTTPRequestHandler, body: dict):
        msgs = body.get("messages") or []
        if not msgs:
            h.send_response(200)
            h.end_headers()
            h.wfile.write(b"{}")
            return
        user_idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
        user = _text(msgs[user_idx].get("content")) if user_idx >= 0 else ""
        after = msgs[user_idx + 1:]
        rnd = sum(1 for m in after if m.get("role") == "assistant")
        tools = [m for m in after if m.get("role") == "tool"]
        last_tool = _text(tools[-1].get("content")) if tools else ""
        tool_names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        step = None
        for marker, steps in self.rules:
            if marker in user:
                step = steps[rnd] if rnd < len(steps) else None
                break
        with self._lock:
            self.requests.append({"user": user, "round": rnd, "tools": tool_names, "last_tool": last_tool,
                                  "matched": step is not None, "stream": bool(body.get("stream"))})
        if step and step.gate:
            self._hit[step.gate].set()
            self._gates[step.gate].wait(120)
        msg: dict[str, Any] = {"role": "assistant", "content": "DONE"}
        reason = "stop"
        if step and step.calls:
            with self._lock:
                calls = []
                for name, args in step.calls:
                    if callable(args):
                        args = args(Ctx(last_tool, user, rnd))
                    self._ids += 1
                    calls.append({"index": len(calls), "id": f"call_{self._ids}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(args)}})
            msg = {"role": "assistant", "content": None, "tool_calls": calls}
            reason = "tool_calls"
        elif step and step.text is not None:
            t = step.text(Ctx(last_tool, user, rnd)) if callable(step.text) else step.text
            msg = {"role": "assistant", "content": t}
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
        h.end_headers()
        if body.get("stream"):
            for delta, fin in [(msg, None), ({}, reason)]:
                frame = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "local-wire-stub",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                h.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
            h.wfile.write(b"data: [DONE]\n\n")
        else:
            h.wfile.write(json.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "local-wire-stub",
                                      "choices": [{"index": 0, "message": msg, "finish_reason": reason}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode())
        h.wfile.flush()
