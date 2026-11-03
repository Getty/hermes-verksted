"""HermesHost against the real Hermes modules it reads (pinned ad2b705, the pytest throwaway home):
the session context of the calling turn, the active profile's plugin store, the compaction hook."""

from __future__ import annotations

import contextvars
import sys
from pathlib import Path

from conftest import SESSION_HOME
from test_store import run_record
from verksted.hermes_host import HermesHost


class Ctx:
    profile_name = "default"


def _in_turn(fn, **session):
    """Run *fn* inside a turn bound the way gateway/run.py binds one."""
    from gateway.session_context import clear_session_vars, set_session_vars

    def turn():
        tokens = set_session_vars(**session)
        try:
            return fn()
        finally:
            clear_session_vars(tokens)
    return contextvars.copy_context().run(turn)


def test_caller_reads_the_bound_turn_and_the_active_home():
    host = HermesHost(Ctx())
    seen = _in_turn(lambda: host.caller({}), session_key="agent:main:local:dm:chat-1", session_id="sid-1",
                    chat_id="chat-1", source="desktop")
    assert seen == {"session_id": "sid-1", "session_key": "agent:main:local:dm:chat-1", "chat_id": "chat-1",
                    "source": "desktop", "profile": "default", "profile_home": str(SESSION_HOME.resolve()),
                    "backend_platform": sys.platform}
    # The dispatcher's session_id kwarg wins over the bound one; nothing comes from tool arguments.
    assert _in_turn(lambda: host.caller({"session_id": "sid-kw"}), session_id="sid-1")["session_id"] == "sid-kw"
    assert _in_turn(lambda: host.caller({}), platform="telegram")["source"] == "telegram"


def test_the_store_lives_in_the_active_profiles_plugin_data():
    store = HermesHost(Ctx()).store()
    store.create_run(run_record(1, "/work/host-a"), max_active=4)
    assert (Path(SESSION_HOME) / "plugin-data" / "verksted" / "data.db").is_file()


def test_post_compaction_keeps_parent_and_child_bindings():
    host = HermesHost(Ctx())
    store = host.store()
    store.create_run(run_record(2, "/work/host-b", parent_session_id="parent-old"), max_active=4)
    host.on_post_compaction(session_id="parent-new", old_session_id="parent-old")
    assert store.resolve_session("parent-new") == "parent-old"
    host.on_post_compaction(session_id="stranger-new", old_session_id="stranger-old")  # no run knows it
    assert store.resolve_session("stranger-new") == "stranger-new"
    host.on_post_compaction(session_id="", old_session_id="parent-old")  # nothing to follow


def test_post_compaction_never_raises_into_hermes(monkeypatch, caplog):
    from verksted import hermes_host

    def broken(**_):
        raise OSError("disk gone")
    monkeypatch.setattr(hermes_host.Store, "for_active_profile", broken)
    HermesHost(Ctx()).on_post_compaction(session_id="a", old_session_id="b")
    assert "could not record compaction" in caplog.text


def test_interrupt_follows_the_calling_thread():
    from tools.interrupt import set_interrupt
    host = HermesHost(Ctx())
    assert host.is_interrupted() is False
    set_interrupt(True)
    try:
        assert host.is_interrupted() is True
    finally:
        set_interrupt(False)
    assert host.is_interrupted() is False
