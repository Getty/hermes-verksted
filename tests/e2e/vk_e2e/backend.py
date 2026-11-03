"""A real Hermes backend (pinned build) started the way the Desktop starts its local one.

Desktop (apps/desktop/electron/main.ts:11848, local-gateway.ts): runs ``hermes gateway ensure --json``
with ``HERMES_HOME`` set, reads the endpoint from its JSON (``api_origin``, ``instance_id``,
``profile_id``), mints one-use tickets over the owner's control socket ``<home>/gateway.sock`` and
dials ``/api/ws`` with subprotocols ``hermes-gateway-v1`` + ``hermes-gateway-ticket.<ticket>``; REST
(including ``/api/plugins/<id>``) carries a ``native-http`` ticket in ``X-Hermes-Gateway-Ticket``.
This class does the same. Nothing here is a look-alike server.

Hazards honoured: the home lives under ``.dev/`` (never ``~/.hermes``, never tmpfs), ``HOME`` and
``TMPDIR`` are redirected into the throwaway root too, plugins are enabled through ``plugins.enabled``
in ``config.yaml`` (never ``hermes plugins enable``, which re-resolves the dependency environment).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERMES_SRC = REPO / ".dev" / "hermes-agent"
VENV = REPO / ".dev" / "venv"
HERMES_BIN = VENV / "bin" / "hermes"
PINNED = "ad2b705b4995a958b0a795e9097be661e2d1ef63"
WS_PROTOCOL = "hermes-gateway-v1"
TICKET_PREFIX = "hermes-gateway-ticket."


class HarnessError(RuntimeError):
    pass


def config_version() -> int:
    m = re.search(r'"_config_version":\s*(\d+)', (HERMES_SRC / "hermes_cli" / "config_defaults.py").read_text())
    return int(m.group(1))


def preflight() -> None:
    """Fail loud (not skip) when the pinned test Hermes is not there."""
    if not HERMES_BIN.exists():
        raise HarnessError(f"{HERMES_BIN} missing: the dev venv is not built (make dev-env)")
    head = subprocess.run(["git", "-C", str(HERMES_SRC), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != PINNED:
        raise HarnessError(f".dev/hermes-agent is at {head or '?'}, expected pinned {PINNED}")


class Backend:
    """``plugins``: {plugin_name: package dir}. Each is symlinked (or copied) to ``<home>/plugins/<name>/``
    and listed in ``plugins.enabled``. The Desktop half (``desktop/plugin.js``) is copied to
    ``<home>/desktop-plugins/<name>/plugin.js`` as Electron does (desktop-plugins-root.ts)."""

    def __init__(self, root: Path, model_url: str, plugins: dict[str, Path] | None = None, *,
                 copy_plugins: bool = False, approvals_timeout: int = 60, tool_search: str = "off", config: dict | None = None):
        self.root = Path(root)
        self.home = self.root / "home"
        self.user = self.root / "user"
        self.work = self.root / "work dir ü"
        self.model_url = model_url
        self.plugins = dict(plugins or {})
        self.copy_plugins = copy_plugins
        self.approvals_timeout = approvals_timeout
        # Hermes defers plugin/MCP tools behind tool_search/tool_describe/tool_call when tools.tool_search.enabled
        # is "auto" (the default): a plugin tool is then NOT in the model-facing tool array. The scripted model
        # calls tools by name, so the harness defaults to "off". Pass tool_search="auto" for the production default.
        self.tool_search = tool_search
        self.extra_config = config or {}
        self.endpoint: dict | None = None
        self.log_path = self.root / "ensure.log"
        self.forced_info: list[tuple[int, str]] = []

    # --- home ------------------------------------------------------------------------------
    def _write_home(self) -> None:
        for d in (self.home, self.user, self.work):
            d.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        cfg = {
            "_config_version": config_version(),
            "tools": {"tool_search": {"enabled": self.tool_search}},
            "gateway": {"multiplex_profiles": False},
            # manual: a terminal command that writes needs a human-style approval (k1 §3).
            "approvals": {"mode": "manual", "timeout": self.approvals_timeout},
            "model": {"provider": "custom", "default": "local-wire-stub", "base_url": self.model_url},
            "auxiliary": {"title_generation": {"enabled": False}},
            "plugins": {"enabled": sorted(self.plugins)},
        }
        for k, v in self.extra_config.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        (self.home / "config.yaml").write_text(json.dumps(cfg))  # JSON is YAML
        self.install_plugins()

    def install_plugins(self) -> None:
        (self.home / "plugins").mkdir(exist_ok=True)
        for name, src in self.plugins.items():
            src = Path(src).resolve()
            dst = self.home / "plugins" / name
            if dst.is_symlink() or dst.exists():
                shutil.rmtree(dst) if dst.is_dir() and not dst.is_symlink() else dst.unlink()
            if self.copy_plugins:
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                dst.symlink_to(src, target_is_directory=True)
            self.sync_desktop_plugin(name)

    def sync_desktop_plugin(self, name: str) -> Path | None:
        """Electron copies ``plugins/<id>/desktop/plugin.js`` into ``desktop-plugins/<id>/`` with a marker."""
        src = self.home / "plugins" / name / "desktop" / "plugin.js"
        if not src.exists():
            return None
        dst_dir = self.home / "desktop-plugins" / name
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst_dir / "plugin.js")
        (dst_dir / ".hermes-package.json").write_text(json.dumps({"source": f"plugins/{name}", "harness": True}))
        return dst_dir / "plugin.js"

    def desktop_plugin_file(self, name: str) -> Path:
        p = self.home / "desktop-plugins" / name / "plugin.js"
        if not p.exists():
            raise HarnessError(f"{name} has no desktop/plugin.js in its package")
        return p

    # --- process ----------------------------------------------------------------------------
    def env(self) -> dict[str, str]:
        e = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TZ") if k in os.environ}
        e.update(HOME=str(self.user), USERPROFILE=str(self.user), HERMES_HOME=str(self.home), TMPDIR=str(self.root),
                 PYTHONUNBUFFERED="1", OPENAI_API_KEY="loopback-only", OPENAI_BASE_URL=self.model_url)
        return e

    def _hermes(self, *args: str, timeout=120) -> subprocess.CompletedProcess:
        return subprocess.run([str(HERMES_BIN), *args], env=self.env(), cwd=str(self.root), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)

    def start(self) -> "Backend":
        preflight()
        assert self.home.resolve().is_relative_to(REPO / ".dev"), "harness home must live under .dev/"
        self._write_home()
        r = self._hermes("gateway", "ensure", "--json")
        self.log_path.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise HarnessError(f"`hermes gateway ensure --json` produced no JSON (exit {r.returncode}): {r.stderr[-400:]}")
        if r.returncode != 0 or payload.get("state") != "ready":
            raise HarnessError(f"gateway not ready: {payload}")
        self.endpoint = payload["endpoint"]
        if self.endpoint["profile_id"] != str(self.home):
            raise HarnessError(f"gateway runs {self.endpoint['profile_id']}, expected {self.home}")
        return self

    def stop(self) -> list[int]:
        """Stop the owner. Returns pids that had to be SIGKILLed (a clean stop returns [])."""
        forced: list[int] = []
        if self.home.exists():
            try:
                self._hermes("gateway", "stop", timeout=60)
            except subprocess.TimeoutExpired:
                pass
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and self.leftover_pids():
                time.sleep(0.3)
            for pid in self.leftover_pids():
                forced.append(pid)
                try:
                    cmd = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")[:200]
                except OSError:
                    cmd = "?"
                self.forced_info.append((pid, cmd))
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.2)
        return forced

    def leftover_pids(self) -> list[int]:
        """Every process whose environment names this HERMES_HOME (the gateway, its children)."""
        needle = f"HERMES_HOME={self.home}".encode()
        pids = []
        for p in Path("/proc").iterdir():
            if not p.name.isdigit() or int(p.name) == os.getpid():
                continue
            try:
                if needle in (p / "environ").read_bytes().split(b"\0"):
                    pids.append(int(p.name))
            except (OSError, PermissionError):
                continue
        return pids

    # --- auth + protocol access ----------------------------------------------------------------
    def control_socket(self) -> str:
        direct = self.home / "gateway.sock"
        if direct.exists():
            return str(direct)
        return (self.home / "gateway.sock.path").read_text().strip()

    def mint_ticket(self, purpose: str = "interactive") -> str:
        """electron/local-gateway.ts mintLocalGatewayTicket, in Python."""
        ep = self.endpoint
        params = {"profile_id": ep["profile_id"], "instance_id": ep["instance_id"], "purpose": purpose}
        if purpose == "interactive":
            params["scope"] = "host"
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(5)
            s.connect(self.control_socket())
            s.sendall(json.dumps({"protocol": 1, "id": 1, "verb": "session-ticket", "params": params}).encode() + b"\n")
            raw = s.makefile("rb").readline(65537)
        reply = json.loads(raw)
        grant = reply.get("result", {})
        if not reply.get("ok") or grant.get("instance_id") != ep["instance_id"] or grant.get("profile_id") != ep["profile_id"]:
            raise HarnessError(f"ticket refused: {reply}")
        return grant["ticket"]

    @property
    def ws_url(self) -> str:
        return self.endpoint["api_origin"].replace("http:", "ws:") + "/api/ws"

    def http(self, method: str, path: str, body=None, *, headers: dict | None = None, ticket: bool = True):
        """REST against the gateway process as Electron main does: native-http ticket header, no Origin."""
        h = dict(headers or {})
        if ticket:
            h["X-Hermes-Gateway-Ticket"] = self.mint_ticket("native-http")
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.endpoint["api_origin"] + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw.decode(errors="replace")

    # --- logs ------------------------------------------------------------------------------------
    def logs(self) -> dict[str, str]:
        out = {}
        d = self.home / "logs"
        if d.exists():
            for f in sorted(d.glob("*.log")):
                out[f.name] = f.read_text(errors="replace")[-4000:]
        return out
