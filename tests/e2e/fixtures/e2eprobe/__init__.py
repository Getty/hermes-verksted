"""Harness probe agent plugin: proves tool dispatch, caller identity, plugin events and the plugin API."""
import json


def _ping(args, **kwargs):
    from gateway.session_context import get_session_env
    from hermes_cli.plugin_events import broadcast_plugin_event
    broadcast_plugin_event("e2eprobe", "ping", {"note": str(args.get("note", ""))})
    return json.dumps({
        "pong": True,
        "kwargs_session_id": kwargs.get("session_id"),
        "args_session_id": args.get("session_id"),
        "session_key": get_session_env("HERMES_SESSION_KEY"),
        "source": get_session_env("HERMES_SESSION_SOURCE"),
    })


SCHEMA = {"name": "e2e_ping", "description": "Harness probe: broadcasts a plugin event and echoes the caller identity.",
          "parameters": {"type": "object", "properties": {"note": {"type": "string"}, "session_id": {"type": "string"}}}}


def register(ctx):
    ctx.register_tool(name="e2e_ping", toolset="e2eprobe", schema=SCHEMA, handler=_ping)
