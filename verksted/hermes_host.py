"""The tool handler's view of Hermes. Every Hermes import is inside a method, so importing the
plugin stays cheap and the tool tests run against a fake host without Hermes.

Interfaces read at Hermes ad2b705 (docs/hermes-interfaces.md §4):

- dispatch kwargs ``session_id``/``task_id``/``user_task`` (``model_tools._execute_tool``);
- ``gateway.session_context.get_session_env`` — ``HERMES_SESSION_KEY`` is the stable route key
  of the calling session (canonical local sessions: built from the session id as chat id),
  ``HERMES_SESSION_CHAT_ID`` the canonical session id;
- ``ctx.profile_name`` and ``hermes_constants.get_hermes_home()`` (both follow a context-local
  home override, so a multiplexed profile's turn sees its own home);
- ``hermes_cli.plugin_events.broadcast_plugin_event`` (doorbell, C15);
- ``tools.interrupt.is_interrupted`` (C18; not on the public plugin list, the lint warns).
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from .store import PLUGIN_NAME, Store

logger = logging.getLogger(__name__)


class HermesHost:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def caller(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        from gateway.session_context import get_session_env
        from hermes_constants import get_hermes_home
        session_id = kwargs.get("session_id") or get_session_env("HERMES_SESSION_ID")
        return {
            "session_id": str(session_id or ""),
            "session_key": get_session_env("HERMES_SESSION_KEY"),
            "chat_id": get_session_env("HERMES_SESSION_CHAT_ID"),
            "source": get_session_env("HERMES_SESSION_SOURCE") or get_session_env("HERMES_SESSION_PLATFORM"),
            "profile": self._ctx.profile_name,
            "profile_home": str(get_hermes_home().resolve()),
            "backend_platform": sys.platform,
        }

    def store(self) -> Store:
        return Store.for_active_profile()

    def ring(self) -> None:
        """Tell connected windows that work is waiting; the payload carries no run data."""
        try:
            from hermes_cli.plugin_events import broadcast_plugin_event
            broadcast_plugin_event(PLUGIN_NAME, "work", {})
        except Exception:  # the run is stored; windows also poll on load, reconnect and a timer
            logger.warning("verksted: doorbell failed", exc_info=True)

    def is_interrupted(self) -> bool:
        from tools.interrupt import is_interrupted
        return bool(is_interrupted())

    def yield_requested(self) -> bool:
        """A mid-turn user message: ``redirect()`` asks the tool worker threads to yield
        (agent/interrupt_control.py:346-359). Taken like ``process_registry.wait`` does
        (tools/process_registry.py:2084), also for a deadline worker acting for the tool thread."""
        import threading

        from tools.interrupt import acting_for_tid, consume_yield
        return bool(consume_yield(threading.current_thread().ident) or consume_yield(acting_for_tid.get()))

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> float:
        return time.time()

    def on_post_compaction(self, session_id: str = "", old_session_id: str = "", **_: Any) -> None:
        """A compaction rotated ``old_session_id`` to ``session_id``; keep parent binding and
        recursion guard on the rotated id."""
        if not old_session_id or not session_id:
            return
        try:
            Store.for_active_profile().add_session_alias(old_session_id, session_id)
        except Exception:
            logger.warning("verksted: could not record compaction %s -> %s", old_session_id, session_id,
                           exc_info=True)
