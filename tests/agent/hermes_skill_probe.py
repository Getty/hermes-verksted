"""Runs inside a real Hermes (the pinned checkout) with a throwaway HERMES_HOME; driven by
test_skill.py. Reads its inputs from the JSON file named by argv[1] and writes one JSON object to
the file named by argv[2] (importing tui_gateway takes stdout over). Not a test module itself."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HERMES_HOME"]).resolve()
assert ".dev/test-homes/" in str(home), f"refusing to run against {home}"
spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
result_file = Path(sys.argv[2])
out: dict = {}

import model_tools  # noqa: E402  (loads the plugin system like an agent process does)
from hermes_cli.plugins import get_plugin_manager  # noqa: E402

manager = get_plugin_manager()
loaded = manager._plugins.get("verksted")
out["plugin"] = {"found": loaded is not None, "enabled": getattr(loaded, "enabled", None),
                 "error": str(getattr(loaded, "error", None) or "") or None,
                 "tools": list(getattr(loaded, "tools_registered", None) or [])}
skill_md = manager.find_plugin_skill("verksted:verksted")
out["registered"] = str(skill_md) if skill_md else None

if spec["mode"] == "full":
    from agent.prompt_builder import build_context_files_prompt, build_skills_system_prompt  # noqa: E402
    from agent.skill_utils import parse_frontmatter  # noqa: E402
    from tools.skills_tool import skill_view, skills_list  # noqa: E402
    from tools.skills_tool_plugin import _INJECTION_PATTERNS  # noqa: E402
    from tools.threat_patterns import scan_for_threats  # noqa: E402

    import toolsets  # noqa: E402

    skill_text = Path(spec["skill_md"]).read_text(encoding="utf-8")
    out["hermes_frontmatter"] = parse_frontmatter(skill_text)[0]
    out["metadata"] = [m for m in manager.list_plugin_skill_metadata() if m["name"] == "verksted:verksted"]
    out["listed"] = [s for s in json.loads(skills_list())["skills"] if s["name"] == "verksted:verksted"]
    out["view"] = json.loads(skill_view("verksted:verksted"))

    # The skills index as a default session builds it: the tool surface after tool-search assembly.
    names = {d["function"]["name"] for d in model_tools.get_tool_definitions(quiet_mode=True)}
    out["surface"] = {n: n in names for n in ("verksted", "tool_search", "tool_call", "skill_view")}
    avail_toolsets = {model_tools.get_toolset_for_tool(n) for n in names} - {None, ""}
    out["index"] = build_skills_system_prompt(available_tools=names, available_toolsets=avail_toolsets)

    out["skill_injection"] = [p for p in _INJECTION_PATTERNS if p in skill_text.lower()]
    out["threats"] = {path: scan_for_threats(Path(path).read_text(encoding="utf-8"), scope="context")
                      for path in spec["scan"]}
    out["native_toolsets"] = {name: name in toolsets.TOOLSETS for name in spec["toolset_names"]}
    out["context"] = {label: {rel: build_context_files_prompt(cwd=str(Path(root) / rel), skip_soul=True)
                              for rel in spec["context_dirs"]}
                      for label, root in spec["context_roots"].items()}

    from gateway.session_context import clear_session_vars, set_session_vars  # noqa: E402

    # What a canonical local turn binds for the primary profile (gateway/run.py _set_session_env).
    tokens = set_session_vars(session_key="agent:main:local:dm:parent", session_id="parent-sid",
                              source="desktop", profile="", cwd=str(home))
    try:
        def dispatch(name, args):
            return json.loads(model_tools.handle_function_call(
                name, args, task_id="task-1", session_id="parent-sid", tool_call_id="tc-skill",
                user_task="skill"))

        out["searched"] = [dispatch("tool_search", args) for args in spec["tool_search"]]
        out["bridged"] = [dispatch("tool_call", args) for args in spec["tool_call"]]
        out["direct"] = [dispatch("verksted", args) for args in spec["verksted"]]
    finally:
        clear_session_vars(tokens)

result_file.write_text(json.dumps(out, default=str, ensure_ascii=False), encoding="utf-8")
