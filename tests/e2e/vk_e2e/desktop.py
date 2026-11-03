"""Python handle on one Node Desktop-window stand-in (shim/window.mjs). Read its header comment for
what is real and what is faked."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from .backend import HERMES_SRC, Backend, HarnessError

SHIM = Path(__file__).resolve().parents[1] / "shim" / "window.mjs"


class DesktopError(RuntimeError):
    def __init__(self, error: dict):
        super().__init__(error.get("message"))
        self.code, self.data, self.status = error.get("code"), error.get("data"), error.get("status")

    @property
    def reason(self):
        return (self.data or {}).get("reason")


class DesktopWindow:
    def __init__(self, backend: Backend, plugin_id: str, workdir: Path, *, interval_mode: str = "manual",
                 name: str = "window", plugin_file: Path | None = None):
        self.backend, self.plugin_id, self.name = backend, plugin_id, name
        cfg = {"home": str(backend.home), "endpoint": backend.endpoint, "pluginId": plugin_id,
               "pluginFile": str(plugin_file or backend.desktop_plugin_file(plugin_id)),
               "pinnedRoot": str(HERMES_SRC), "intervalMode": interval_mode, "logFile": str(workdir / f"{name}.log")}
        self.cfg_path = workdir / f"{name}.config.json"
        self.cfg_path.write_text(json.dumps(cfg))
        self.stderr_path = workdir / f"{name}.stderr"
        self._err = self.stderr_path.open("w")
        self.proc = subprocess.Popen(["node", str(SHIM), str(self.cfg_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._err, text=True, bufsize=1, start_new_session=True)
        self._lines: queue.Queue = queue.Queue()
        self._n = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        boot = self._take(None, 30)
        if not boot.get("ready"):
            self.close()
            raise HarnessError(f"{name}: plugin failed to load:\n{boot.get('error')}")

    def _pump(self):
        for line in self.proc.stdout:
            try:
                self._lines.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self._lines.put({"eof": True})

    def _take(self, rid, timeout):
        stash = []
        try:
            end = time.monotonic() + timeout
            while True:
                try:
                    msg = self._lines.get(timeout=max(0.01, end - time.monotonic()))
                except queue.Empty:
                    raise HarnessError(f"{self.name}: no reply to #{rid} in {timeout}s; stderr: {self.stderr_path.read_text()[-400:]}")
                if msg.get("eof"):
                    raise HarnessError(f"{self.name}: shim exited; stderr: {self.stderr_path.read_text()[-600:]}")
                if msg.get("id") == rid:
                    return msg
                stash.append(msg)
        finally:
            for m in stash:
                self._lines.put(m)

    def cmd(self, cmd: str, timeout=60, **args):
        with self._lock:
            self._n += 1
            rid = self._n
            self.proc.stdin.write(json.dumps({"id": rid, "cmd": cmd, "args": args}) + "\n")
            self.proc.stdin.flush()
        reply = self._take(rid, timeout)
        if not reply["ok"]:
            raise DesktopError(reply["error"])
        return reply["result"]

    # --- SDK-level calls (same client the plugin uses) ------------------------------------------------
    def state(self) -> dict:
        return self.cmd("state")

    def request_profile(self, method: str, timeout=60, **params):
        """host.requestProfile(route, method, params) on the primary route."""
        return self.cmd("rpc", timeout=timeout + 5, method=method, params=params, timeoutMs=timeout * 1000)

    def rest(self, path: str, **opts):
        return self.cmd("rest", path=path, opts=opts)

    def tick(self, id=None) -> dict:
        """Fire the plugin's ctx.setInterval callbacks once (manual interval mode)."""
        return self.cmd("tick", **({"id": id} if id is not None else {}))

    def events(self, since=0) -> list[dict]:
        return self.cmd("events", since=since)

    def wait_event(self, type: str, *, since=0, match=None, timeout=30) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for e in self.events(since):
                if e["type"] == type and (match is None or match(e)):
                    return e
            time.sleep(0.1)
        raise HarnessError(f"{self.name}: no event {type} within {timeout}s")

    def render(self, contribution: str) -> dict:
        """Text and button labels of a registered contribution (shim renderer, not React)."""
        return self.cmd("ui.render", id=contribution)

    def click(self, contribution: str, text: str, nth=0):
        return self.cmd("ui.click", id=contribution, text=text, nth=nth)

    def rpc_calls(self, method: str | None = None) -> list[dict]:
        """The SDK-level calls, in the order they went on the wire (a nested auto-resume precedes its caller)."""
        log = sorted(self.state()["rpcLog"], key=lambda c: c.get("wire_seq", 10**9))
        return [c for c in log if method is None or c["method"] == method]

    def inject(self, method: str, mode: str, times=1, merge: dict | None = None, prefix: dict | None = None):
        """``prefix`` ({param: str}): only calls whose params start with these strings are hit."""
        return self.cmd("inject", method=method, mode=mode, times=times, merge=merge, prefix=prefix)

    def reconnect(self):
        return self.cmd("reconnect")

    def disconnect(self):
        return self.cmd("disconnect")

    # --- user side (the native chat of this window) ----------------------------------------------------
    def user_respond_server_request(self, request_id: str, result: dict):
        return self.cmd("user.respondServerRequest", id=request_id, result=result)

    def close(self):
        try:
            if self.proc.poll() is None:
                try:
                    self.cmd("close", timeout=10)
                except Exception:
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=5)
            self._err.close()
