"""Shared fixtures. Every Hermes import in this suite runs against a throwaway HERMES_HOME under
.dev/test-homes/, set here before any test module imports Hermes: a Hermes call without
HERMES_HOME creates ~/.hermes, and nothing here may touch the maintainer's own home."""

from __future__ import annotations

import json
import os
import pwd
import shutil
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEV = REPO / ".dev"
HOMES = DEV / "test-homes"
PACKAGE = REPO / "verksted"
VENV_PYTHON = DEV / "venv" / "bin" / "python"
HERMES_SRC = DEV / "hermes-agent"
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
_MAINTAINER_HOMES = (_REAL_HOME / ".hermes",)
_PRESENT_AT_START = {str(p): p.exists() for p in _MAINTAINER_HOMES}


def _set_session_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "")
    if raw:
        home = Path(raw).resolve()
        if HOMES.resolve() not in home.parents:
            raise pytest.UsageError(f"HERMES_HOME={raw} is not a throwaway home under {HOMES}; refusing to run")
    else:
        home = HOMES / f"pytest-{os.getpid()}"
        os.environ["HERMES_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    return home


SESSION_HOME = _set_session_home()


def pytest_sessionfinish(session, exitstatus):
    if SESSION_HOME.name.startswith("pytest-"):
        shutil.rmtree(SESSION_HOME, ignore_errors=True)
    for path, existed in _PRESENT_AT_START.items():
        if not existed and Path(path).exists():
            session.exitstatus = 1
            print(f"\nFAIL: the test run created {path}; a Hermes call ran without HERMES_HOME")


def make_throwaway_home(name: str, *, enable_plugin: bool = True) -> Path:
    """A fresh Hermes home under .dev/test-homes/ with the package copied into plugins/verksted
    and enabled through config.yaml (never `hermes plugins enable`)."""
    home = HOMES / f"{name}-{uuid.uuid4().hex[:8]}"
    (home / "plugins").mkdir(parents=True)
    shutil.copytree(PACKAGE, home / "plugins" / "verksted",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    enabled = ["verksted"] if enable_plugin else []
    (home / "config.yaml").write_text(f"plugins:\n  enabled: {json.dumps(enabled)}\n", encoding="utf-8")
    return home


@pytest.fixture
def throwaway_home(request):
    homes: list[Path] = []

    def make(name: str = "contract", **kwargs) -> Path:
        home = make_throwaway_home(name, **kwargs)
        homes.append(home)
        return home

    yield make
    for home in homes:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def workspace(tmp_path):
    """A target folder for runs."""
    folder = tmp_path / "workspace"
    folder.mkdir()
    return folder
