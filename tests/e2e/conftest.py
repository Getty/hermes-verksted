"""Fixtures for the Linux end-to-end harness. Everything runs against a throwaway HERMES_HOME under .dev/e2e/.

Run:  .dev/venv/bin/python -m pytest tests/e2e -v        (from the repo root)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vk_e2e.backend import REPO, Backend, HarnessError, preflight  # noqa: E402
from vk_e2e.desktop import DesktopWindow  # noqa: E402
from vk_e2e.model import ScriptedModel  # noqa: E402
from vk_e2e.user import UserChat  # noqa: E402

E2E_ROOT = REPO / ".dev" / "e2e"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _e2e_session():
    preflight()  # fail loud (error, not skip) when the pinned Hermes / venv is missing
    if shutil.which("node") is None:
        raise HarnessError("node is not on PATH: the Desktop stand-in needs it (node >= 22)")
    if E2E_ROOT.exists():
        shutil.rmtree(E2E_ROOT, ignore_errors=True)
    E2E_ROOT.mkdir(parents=True)
    yield


@pytest.fixture
def model():
    m = ScriptedModel().start()
    yield m
    m.stop()


@pytest.fixture
def e2e(request, model):
    """Factory: e2e(plugins={'name': Path}, ...) -> Env. Everything it starts is torn down, and a leftover
    process (one that had to be SIGKILLed) fails the test."""
    made: list[Env] = []
    root = E2E_ROOT / request.node.name.replace("[", "_").replace("]", "")

    def make(plugins: dict[str, Path] | None = None, **kw) -> "Env":
        env = Env(root, model, plugins or {}, **kw)
        made.append(env)
        return env

    yield make
    forced: list[int] = []
    for env in reversed(made):
        forced += env.teardown()
    failed = request.session.testsfailed
    if not failed:
        shutil.rmtree(root, ignore_errors=True)
    assert not forced, f"processes had to be SIGKILLed at teardown: {[i for e in made for i in e.backend.forced_info]}"
    for e in made:
        if getattr(e, "load_error", None):
            raise e.load_error


class Env:
    def __init__(self, root: Path, model: ScriptedModel, plugins: dict[str, Path], **kw):
        self.root, self.model = root, model
        root.mkdir(parents=True, exist_ok=True)
        self.backend = Backend(root, model.url, plugins, **kw)
        self.windows: list[DesktopWindow] = []
        self.users: list[UserChat] = []

    def start(self) -> "Env":
        self.backend.start()
        return self

    @property
    def work(self) -> Path:
        return self.backend.work

    def window(self, plugin_id: str, **kw) -> DesktopWindow:
        w = DesktopWindow(self.backend, plugin_id, self.root, name=kw.pop("name", f"window{len(self.windows)}"), **kw)
        self.windows.append(w)
        return w

    def user(self, **kw) -> UserChat:
        u = UserChat(self.backend, **kw)
        self.users.append(u)
        return u

    def close_clients(self) -> None:
        for u in self.users:
            u.close()
        for w in self.windows:
            w.close()
        self.users, self.windows = [], []

    def teardown(self) -> list[int]:
        self.close_clients()
        forced = self.backend.stop()
        self.load_error = None
        if self.backend.endpoint is not None:
            try:
                self.backend.check_plugins_loaded()
            except HarnessError as exc:  # raised by the fixture after every env is torn down
                self.load_error = exc
        return forced


@pytest.fixture(scope="module")
def shared_e2e_factory():
    """Module-scoped backend for suites that do not need a fresh home per test (the backend takes ~15 s to
    start). Returns make(plugins) -> Env; one Env per distinct plugin set. Torn down once; a SIGKILLed
    straggler fails the module's last test."""
    model = ScriptedModel().start()
    envs: dict[tuple, Env] = {}

    def make(plugins: dict[str, Path]) -> Env:
        key = tuple(sorted((k, str(v)) for k, v in plugins.items()))
        if key not in envs:
            envs[key] = Env(E2E_ROOT / f"shared-{len(envs)}", model, plugins).start()
        return envs[key]

    make.model = model
    yield make
    forced: list[int] = []
    for env in envs.values():
        forced += env.teardown()
    model.stop()
    assert not forced, f"processes had to be SIGKILLed at teardown: {[i for e in envs.values() for i in e.backend.forced_info]}"
    for e in envs.values():
        if getattr(e, "load_error", None):
            raise e.load_error
