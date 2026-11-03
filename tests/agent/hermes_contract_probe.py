"""Runs inside a real Hermes (the pinned checkout) with a throwaway HERMES_HOME; driven by
test_hermes_contract.py. Writes one JSON object to the file named by argv[2] (importing
tui_gateway takes stdout over for its JSON-RPC channel). Not a test module itself."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HERMES_HOME"]).resolve()
assert ".dev/test-homes/" in str(home), f"refusing to run against {home}"
workspace = sys.argv[1]
result_file = Path(sys.argv[2])
out: dict = {"home": str(home)}

import model_tools  # noqa: E402  (loads the plugin system like an agent process does)
from hermes_cli.plugins import get_plugin_manager  # noqa: E402
from tools.registry import registry  # noqa: E402

manager = get_plugin_manager()
loaded = manager._plugins.get("verksted")
out["plugin"] = {"found": loaded is not None, "enabled": getattr(loaded, "enabled", None),
                 "error": str(getattr(loaded, "error", None) or "") or None}
entry = registry.get_entry("verksted", scope=manager.scope_key)
out["entry"] = entry and {"toolset": entry.toolset, "is_async": entry.is_async,
                          "schema_name": entry.schema.get("name")}

frames: list = []


class Transport:
    def write(self, frame):
        frames.append(frame if isinstance(frame, dict) else json.loads(frame))
        return True


from tui_gateway import server as tui_server  # noqa: E402

tui_server.register_live_transport(Transport())

from gateway.session_context import clear_session_vars, set_session_vars  # noqa: E402

# What a canonical local turn binds for the primary profile (gateway/run.py _set_session_env).
tokens = set_session_vars(session_key="agent:main:local:dm:parent", session_id="parent-sid", source="desktop",
                          profile="", cwd=workspace)
try:
    def call(args, **kw):
        raw = model_tools.handle_function_call("verksted", args, task_id="task-1", session_id="parent-sid",
                                               tool_call_id="tc-1", user_task="contract", **kw)
        return json.loads(raw)

    out["forged"] = call({"action": "run", "folder": workspace, "task": "x", "session_id": "FORGED"})
    out["run"] = call({"action": "run", "folder": workspace, "task": "Say hello", "wait_seconds": 0})
    out["status"] = call({"action": "status", "handle": out["run"].get("handle")})
    out["busy"] = call({"action": "run", "folder": workspace, "task": "again", "wait_seconds": 0})
    # With the default tools.tool_search.enabled: auto a plugin tool is deferred: the model reaches it
    # through the bridge, which must keep the caller's ids (the parent binding).
    from tools.tool_search import is_deferrable_tool_name, load_config_readonly  # noqa: E402
    out["deferrable"] = is_deferrable_tool_name("verksted", load_config_readonly().effective_defer_tools)
    out["searched"] = json.loads(model_tools.handle_function_call(
        "tool_search", {"queries": ["verksted workspace folder"]}, task_id="task-1", session_id="parent-sid"))
    out["bridged"] = json.loads(model_tools.handle_function_call(
        "tool_call", {"calls": [{"name": "verksted", "arguments": {"action": "status",
                                                                    "handle": out["run"].get("handle")}}]},
        task_id="task-1", session_id="parent-sid", tool_call_id="tc-2", user_task="contract"))
finally:
    clear_session_vars(tokens)
out["frames"] = [f for f in frames if "plugin." in json.dumps(f)]
out["store_file"] = (home / "plugin-data" / "verksted" / "data.db").is_file()

# The same store through the real dashboard host: auth middleware, mount, plugin API.
from fastapi.testclient import TestClient  # noqa: E402

import hermes_cli.web_server as ws  # noqa: E402

ws.app.state.bound_host = "127.0.0.1"
client = TestClient(ws.app, base_url="http://127.0.0.1")
token = {ws._SESSION_HEADER_NAME: ws._SESSION_TOKEN}
base = "/api/plugins/verksted"
out["mounted"] = sorted(r.path for r in ws.app.router.routes if str(getattr(r, "path", "")).startswith(base))
out["no_auth"] = client.get(f"{base}/runs/claimable").status_code
out["bogus_ticket"] = client.get(f"{base}/runs/claimable", headers={"X-Hermes-Gateway-Ticket": "bogus"}).status_code
claimable = client.get(f"{base}/runs/claimable", headers=token)
out["claimable"] = {"status": claimable.status_code, "body": claimable.json()}
handle = out["run"].get("handle")
claim = client.post(f"{base}/runs/{handle}/claim", headers=token,
                    json={"owner": "contract-window", "profile_id": str(home), "lease_s": 60}).json()
out["claim"] = {k: claim.get(k) for k in ("ok", "generation", "mode", "code")}
out["claim_run_keys"] = sorted((claim.get("run") or {}).keys())
identity = {"connection_id": "local", "route_profile": "default", "target_profile": "default",
            "profile_id": str(home), "instance_id": "contract"}
out["step"] = client.post(f"{base}/runs/{handle}/step", headers=token,
                          json={"owner": "contract-window", "generation": claim.get("generation"),
                                "step": "create_requested", "data": {"identity": identity}}).json()
out["stale"] = client.post(f"{base}/runs/{handle}/step", headers=token,
                           json={"owner": "contract-window", "generation": (claim.get("generation") or 0) - 1,
                                 "step": "session_created",
                                 "data": {"identity": identity, "session_id": "s"}}).json()
out["mismatch"] = client.post(f"{base}/runs/{handle}/claim", headers=token,
                              json={"owner": "w2", "profile_id": "/somewhere/else"}).json()
result_file.write_text(json.dumps(out, default=str), encoding="utf-8")
