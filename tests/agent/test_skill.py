"""The bundled Hermes skill (verksted/skills/verksted/SKILL.md) and the demo workspace
(examples/verksted-demo/).

Static half: the skill names only actions, arguments, result fields, states and error codes the
package really has, and the demo's step contracts are complete. Hermes half (a subprocess against
the pinned ad2b705, throwaway HERMES_HOME): the plugin registers the skill, Hermes lists, offers
and serves it, the skill's own call examples reach the tool through the tool-search bridge, and a
session in a demo step folder loads that step's AGENTS.md (alone outside git)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from conftest import HERMES_SRC, PACKAGE, REPO, VENV_PYTHON, make_throwaway_home
from verksted import result, store, tool

SKILL = PACKAGE / "skills" / "verksted" / "SKILL.md"
DEMO = REPO / "examples" / "verksted-demo"
STEPS = sorted(p.parent for p in DEMO.glob("workflow/*/AGENTS.md"))
PROBE = Path(__file__).with_name("hermes_skill_probe.py")
PINNED = "ad2b705b4995a958b0a795e9097be661e2d1ef63"

# What the fix pass adds to verksted/__init__.py (k7 could not edit it): register() ends with
# `_register_skill(ctx)`, and this helper sits below register(). Native plugins get no skills/
# discovery at ad2b705, and register_skill does not read the file, so the description (the
# <available_skills> row) and the frontmatter (the offer-time gates) are passed from it.
PROPOSED_HELPER = '''

def _register_skill(ctx) -> None:
    """The bundled Hermes skill skills/verksted/SKILL.md, served as ``verksted:verksted``."""
    from pathlib import Path

    from ruamel.yaml import YAML  # a Hermes core dependency, like config.py's

    skill_md = Path(__file__).parent / "skills" / "verksted" / "SKILL.md"
    frontmatter = YAML(typ="safe", pure=True).load(skill_md.read_text(encoding="utf-8").split("---", 2)[1])
    ctx.register_skill("verksted", skill_md, frontmatter["description"], frontmatter)
'''
_WRAP_REGISTER = '''

_register_without_skill = register


def register(ctx) -> None:
    _register_without_skill(ctx)
    _register_skill(ctx)
'''


def _yaml(text: str):
    return YAML(typ="safe", pure=True).load(text)


def _skill_parts() -> tuple[dict, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    _, head, body = text.split("---", 2)
    return _yaml(head), body


def _json_blocks(body: str) -> list:
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", body, re.S)]


def _examples() -> dict[str, list]:
    """The skill's call examples by the tool they are meant for."""
    out: dict[str, list] = {"tool_search": [], "tool_call": [], "verksted": []}
    for block in _json_blocks(_skill_parts()[1]):
        assert isinstance(block, dict), block
        kind = "tool_search" if "queries" in block else "tool_call" if "calls" in block else "verksted"
        out[kind].append(block)
    return out


def _verksted_args(examples: dict[str, list]) -> list[dict]:
    bridged = [c["arguments"] for b in examples["tool_call"] for c in b["calls"]]
    return examples["verksted"] + bridged


# -- the skill against the package ------------------------------------------------------------

def test_frontmatter_follows_the_hermes_skill_rules():
    frontmatter, body = _skill_parts()
    assert frontmatter["name"] == SKILL.parent.name == "verksted"
    desc = frontmatter["description"]
    # skills/AGENTS.md at ad2b705: <= 60 chars (SKILL_PROMPT_DESC_LIMIT, the index width), one
    # sentence, ending with a period.
    assert isinstance(desc, str) and len(desc) <= 60 and desc.endswith(".") and ". " not in desc, desc
    # An OS gate would hide the skill on some host; a requires_* gate would hide it whenever the
    # tool sits behind tool_search (the default), because the index sees only the visible tools.
    assert "platforms" not in frontmatter
    hermes = (frontmatter.get("metadata") or {}).get("hermes") or {}
    assert not {k for k in hermes if k.startswith(("requires_", "fallback_for_"))}, hermes
    assert body.lstrip().startswith("# Verksted Skill")


def test_body_has_no_hermes_preprocessing_syntax():
    text = SKILL.read_text(encoding="utf-8")
    # skill_view renders ${HERMES_SKILL_DIR}/${HERMES_SESSION_ID} and may run !`cmd` snippets
    # (agent/skill_preprocessing.py); the skill must read the same with preprocessing on or off.
    assert "${HERMES_" not in text and "!`" not in text


def test_examples_use_only_implemented_actions_and_real_arguments():
    examples = _examples()
    props = tool.SCHEMA["parameters"]["properties"]
    implemented = set(tool.ACTIONS)
    calls = _verksted_args(examples)
    assert {c["action"] for c in calls} == implemented  # every implemented action is shown once at least
    for args in calls:
        assert set(args) <= set(props), args
        assert args["action"] in implemented, args
        if args["action"] == "run":
            assert {"folder", "task"} <= set(args)
        else:
            assert "handle" in args
        if args["action"] == "send":
            assert "task" in args and "folder" not in args  # a follow-up keeps the run's folder
        if "wait_seconds" in args:
            assert 0 <= args["wait_seconds"] <= props["wait_seconds"]["maximum"]
    assert examples["tool_search"] and examples["tool_call"]
    for block in examples["tool_call"]:
        assert set(block) == {"calls"} and len(block["calls"]) == 1  # one local tool per tool_call
        assert block["calls"][0]["name"] == tool.TOOL_NAME
    for block in examples["tool_search"]:
        assert set(block) == {"queries"} and all(isinstance(q, str) for q in block["queries"])


def _result_views() -> list[dict]:
    base = {"run_id": "vk-x", "target_dir": "/w", "parent_profile": "default", "claim_expires_at": 0}
    detail = {"reason": "r", "detail": "d", "missing": [], "extra": [], "enabled_toolsets": [], "field": "f"}
    checked = {"v": 2, "final_response": "r", "block": "ok", "summary": "s", "result_truncated": False,
               "outputs": [{"path": "/w/a.md", "relative": "a.md", "kind": "file", "size": 1, "mtime": 1.0}],
               "missing": [{"path": "b.md", "reason": "not_found"}],
               "unanswered": [{"question": "q"}], "blocked": [{"command": "c", "reason": "denied"}]}
    runs = [
        {**base, "state": "finished", "outcome": "completed", "result_ref": checked},
        {**base, "state": "finished", "outcome": "interrupted", "result_ref": {"final_response": "r"}},  # schema 1
        {**base, "state": "refused", "state_detail": detail},
        {**base, "state": "unknown", "state_detail": detail},
        {**base, "state": "cancelled", "state_detail": {"reason": "cancelled_before_submit"}},
        {**base, "state": "running", "claim_expires_at": 1e12,
         "state_detail": {"prompts": [{"kind": "clarify", "prompt_id": "p"}]}},
        {**base, "state": "running", "claim_expires_at": 1e12, "cancel_requested_at": 1.0},
        {**base, "state": "waiting_for_gui"},
        {**base, "state": "send_waiting", "turn_no": 2},
    ]
    return [tool._view(run, 1.0, wait_end=end) for run in runs for end in (None, "timeout")]


def _cancel_verdicts() -> set[str]:
    """Every value of the `cancel` field that action=cancel adds to the view."""
    return {tool._cancel_verdict({"state": state, "outcome": outcome})
            for state in store.STATES for outcome in ("completed", *tool.STOPPED_OUTCOMES)}


def _result_blocks() -> set[str]:
    return {result.parse(text)["block"] for text in
            ("none", "<verksted-result>\nsummary: s\noutputs: none\n</verksted-result>", "<verksted-result>")}


def _error_codes() -> set[str]:
    source = "\n".join(p.read_text(encoding="utf-8") for p in PACKAGE.rglob("*.py"))
    return set(re.findall(r'(?:ToolRefused|PathRefused|ConfigRefused|StoreError)\(\s*"(\w+)"', source))


def test_field_references_exist_in_the_tool_result():
    """Every `field: value` the skill tells the model to look for is a key the tool returns, with
    a value the tool can return."""
    _, body = _skill_parts()
    views = _result_views()
    keys = {k for v in views for k in v} | {"error.code", "error.message", "error.handle", "cancel"}
    args = set(tool.SCHEMA["parameters"]["properties"])
    bridge_args = {"queries", "limit", "names", "calls"}  # tools/tool_search.py bridge_tool_schemas
    values = {
        "state": set(store.STATES),
        "waiting_for": set(tool.WAITING_LABELS),
        "label": set(tool.LABELS.values()) | set(tool.WAITING_LABELS.values()) | {tool.STOPPING_LABEL},
        "wait_end": set(re.findall(r'return run, "(\w+)"', Path(tool.__file__).read_text(encoding="utf-8"))),
        "error.code": _error_codes(),
        "action": set(tool.ACTIONS),
        "cancel": _cancel_verdicts(),
        "result_block": _result_blocks(),
    }
    refs = re.findall(r'`([\w.]+): ([^`]+)`', body)
    assert refs, "the skill names no result fields"
    for key, raw in refs:
        assert key in keys | args | bridge_args, f"`{key}: {raw}` is not a field of the verksted result"
        value = json.loads(raw) if raw[:1] in '"[' or raw in ("true", "false") else raw
        if key in values:
            assert value in values[key], f"`{key}: {raw}`: {sorted(values[key])}"
        elif key in {k for v in views for k, val in v.items() if isinstance(val, bool)}:
            assert isinstance(value, bool), f"`{key}: {raw}` is a boolean field"


def test_every_tool_state_and_waiting_kind_is_handled():
    _, body = _skill_parts()
    for state in store.STATES:
        assert f'`state: "{state}"`' in body, f"the skill does not say what to do with state {state}"
    for kind, label in tool.WAITING_LABELS.items():
        assert f'`waiting_for: "{kind}"`' in body and f'`label: "{label}"`' in body
    assert "`stale: true`" in body and "`ok: false`" in body
    assert "`cancel_requested: true`" in body and f'`label: "{tool.STOPPING_LABEL}"`' in body


def test_every_cancel_verdict_and_result_block_is_handled():
    _, body = _skill_parts()
    for verdict in _cancel_verdicts():
        assert f'`cancel: "{verdict}"`' in body, f"the skill does not say what cancel: {verdict} means"
    for block in _result_blocks() - {"ok"}:
        assert f'`result_block: "{block}"`' in body, block
    for field in ("summary", "outputs", "missing", "unanswered", "blocked"):
        assert f"`{field}`" in body, field
        assert any(field in v for v in _result_views()), f"{field} is not in a finished view"


def test_the_send_refusals_are_handled():
    _, body = _skill_parts()
    for code in ("run_active", "not_sendable", "outcome_unknown"):
        assert code in _error_codes() and f'`error.code: "{code}"`' in body, code


def test_the_task_template_leaves_the_summary_to_the_result_block():
    # k10: Verksted appends the result-block request; the skill's own "list every file you wrote"
    # line would ask the child for a second, unchecked list.
    _, body = _skill_parts()
    assert "lists every file you wrote" not in body
    assert "topmost folder" in body  # k5: the workspace root is the topmost one, as config.py resolves it


def test_the_skill_names_the_desktop_button_as_the_desktop_part_labels_it():
    button = "Session öffnen"
    assert f"'{button}'" in (PACKAGE / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert f"**{button}**" in SKILL.read_text(encoding="utf-8")


# -- the demo workspace -----------------------------------------------------------------------

def _sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _paths(text: str) -> list[str]:
    return re.findall(r"`((?:runs|references|workflow)/[^`]*)`", text)


def test_demo_has_two_numbered_steps():
    assert [p.name for p in STEPS] == ["10-research", "20-write"]
    for name in (".verksted.yaml", "AGENTS.md", "runs/README.md"):
        assert (DEMO / name).is_file(), name
    assert list((DEMO / "references").glob("*.md"))


@pytest.mark.parametrize("step", STEPS, ids=lambda p: p.name)
def test_each_step_is_a_self_contained_contract(step):
    text = (step / "AGENTS.md").read_text(encoding="utf-8")
    sections = _sections(text)
    assert {"Inputs", "Task", "Outputs"} <= set(sections), sorted(sections)
    outputs = _paths(sections["Outputs"])
    assert outputs and all(p.startswith(f"runs/<run-id>/{step.name}/") for p in outputs), outputs
    # Invariant 13: outside git Hermes loads this file alone, so it names the shared files itself,
    # and every one it names exists.
    for ref in (p for p in _paths(text) if p.startswith(("references/", "workflow/"))):
        assert (DEMO / ref).exists(), ref
    assert "references/writing-rules.md" in _paths(sections["Inputs"])


def test_the_steps_have_different_inputs_and_outputs_and_chain():
    research, write = (_sections((s / "AGENTS.md").read_text(encoding="utf-8")) for s in STEPS)
    research_out, write_in = set(_paths(research["Outputs"])), set(_paths(write["Inputs"]))
    assert research_out & write_in, "step 20 reads nothing step 10 writes"
    assert set(_paths(write["Outputs"])).isdisjoint(research_out)


def _demo_configs() -> dict[str, dict]:
    return {str(p.parent.relative_to(DEMO)): _yaml(p.read_text(encoding="utf-8"))
            for p in sorted(DEMO.rglob(".verksted.yaml"))}


def test_demo_configs_only_narrow_the_workspace_ceiling():
    configs = _demo_configs()
    root = configs.pop(".")
    assert root["version"] == 1 and set(root) <= {"version", "profile", "toolsets"}
    assert "profile" not in root  # the demo runs in the calling profile
    for folder, cfg in configs.items():
        assert folder in {str(s.relative_to(DEMO)) for s in STEPS}, folder
        assert cfg["version"] == 1 and set(cfg) <= {"version", "toolsets"}
        assert set(cfg["toolsets"]) < set(root["toolsets"]), (folder, cfg)


# -- against the pinned Hermes ----------------------------------------------------------------

def _pinned_revision() -> str:
    return subprocess.run(["git", "-C", str(HERMES_SRC), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _probe(home: Path, spec: dict) -> dict:
    (home / "skills").mkdir(exist_ok=True)  # a real home has it; without it the skills index is empty
    for sub in ("user", "tmp"):
        (home / sub).mkdir(exist_ok=True)
    spec_file, result = home / "skill-probe-spec.json", home / "skill-probe-result.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update(HERMES_HOME=str(home), HOME=str(home / "user"), TMPDIR=str(home / "tmp"))
    proc = subprocess.run([str(VENV_PYTHON), str(PROBE), str(spec_file), str(result)], cwd=str(HERMES_SRC),
                          env=env, capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0 and result.is_file(), proc.stderr[-4000:]
    return json.loads(result.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hermes_ready():
    if not VENV_PYTHON.exists() or not HERMES_SRC.exists():
        pytest.fail("the pinned Hermes is missing: run `make dev-env` first")  # fail loud, never skip
    assert _pinned_revision() == PINNED, "the .dev Hermes checkout is not at the pinned revision"


@pytest.fixture(scope="module")
def shipped(hermes_ready):
    home = make_throwaway_home("skill-shipped")
    try:
        yield _probe(home, {"mode": "registration"})
    finally:
        shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(scope="module")
def probed(hermes_ready, tmp_path_factory):
    """The package with the proposed registration (unless it already registers the skill), probed
    in full. The demo copies live under the pytest tmp dir: outside any git repository."""
    home = make_throwaway_home("skill")
    init = home / "plugins" / "verksted" / "__init__.py"
    if "register_skill" not in init.read_text(encoding="utf-8"):
        init.write_text(init.read_text(encoding="utf-8") + PROPOSED_HELPER + _WRAP_REGISTER, encoding="utf-8")
    plain = tmp_path_factory.mktemp("demo-plain") / "verksted-demo"
    in_git = tmp_path_factory.mktemp("demo-git") / "verksted-demo"
    shutil.copytree(DEMO, plain)
    shutil.copytree(DEMO, in_git)
    (in_git / ".git").mkdir()  # what agent/prompt_builder._find_git_root looks for
    examples = _examples()
    spec = {
        "mode": "full",
        "skill_md": str(SKILL),
        "scan": [str(SKILL)] + [str(p) for p in sorted(DEMO.rglob("*.md"))],
        "toolset_names": sorted({t for c in _demo_configs().values() for t in c["toolsets"]}),
        "context_roots": {"plain": str(plain), "git": str(in_git)},
        "context_dirs": ["."] + [str(s.relative_to(DEMO)) for s in STEPS],
        "tool_search": examples["tool_search"],
        "tool_call": examples["tool_call"],
        "verksted": examples["verksted"],
    }
    try:
        yield _probe(home, spec)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_the_shipped_package_loads(shipped):
    assert shipped["plugin"]["enabled"] is True and shipped["plugin"]["error"] is None, shipped["plugin"]
    assert shipped["plugin"]["tools"] == ["verksted"]


def test_the_shipped_package_registers_the_skill(shipped):
    # A native plugin gets no skills/ discovery at ad2b705: only register_skill puts it in Hermes.
    assert shipped["registered"] and shipped["registered"].endswith("plugins/verksted/skills/verksted/SKILL.md")


def test_hermes_registers_lists_and_serves_the_skill(probed):
    assert probed["plugin"]["enabled"] is True and probed["plugin"]["error"] is None, probed["plugin"]
    assert probed["plugin"]["tools"] == ["verksted"]  # the skill registration costs the tool nothing
    assert probed["registered"] and probed["registered"].endswith("plugins/verksted/skills/verksted/SKILL.md")
    frontmatter, body = _skill_parts()
    [meta] = probed["metadata"]
    assert meta["description"] == frontmatter["description"]
    assert meta["frontmatter"] == probed["hermes_frontmatter"]  # what Hermes' own parser reads
    assert probed["listed"] == [{"name": "verksted:verksted", "description": frontmatter["description"],
                                 "category": "plugin"}]
    view = probed["view"]
    assert view["success"] is True and view["name"] == "verksted:verksted", view
    assert body.strip() in view["content"]


def test_a_default_session_offers_the_skill_while_the_tool_is_deferred(probed):
    # tools.tool_search.enabled defaults to auto at ad2b705: the tool is behind the bridge, so the
    # skill (in <available_skills>) is how the model learns to reach it.
    assert probed["surface"] == {"verksted": False, "tool_search": True, "tool_call": True, "skill_view": True}
    description = _skill_parts()[0]["description"]
    assert f"- verksted:verksted: {description}" in probed["index"], probed["index"][-2000:]


def test_the_skill_and_the_demo_pass_the_hermes_content_scans(probed):
    assert probed["skill_injection"] == []
    assert {path: hits for path, hits in probed["threats"].items() if hits} == {}


def test_the_demo_uses_native_hermes_toolsets(probed):
    assert probed["native_toolsets"] and all(probed["native_toolsets"].values()), probed["native_toolsets"]


def test_outside_git_a_step_session_loads_only_its_own_contract(probed):
    context = probed["context"]["plain"]
    assert "# Verksted demo workspace" in context["."]
    for step in STEPS:
        prompt = context[str(step.relative_to(DEMO))]
        assert "[BLOCKED" not in prompt
        assert (step / "AGENTS.md").read_text(encoding="utf-8").strip() in prompt
        assert "# Verksted demo workspace" not in prompt  # no parent inheritance outside git


def test_inside_git_a_step_session_also_gets_the_workspace_agents_md(probed):
    for step in STEPS:
        prompt = probed["context"]["git"][str(step.relative_to(DEMO))]
        assert "[BLOCKED" not in prompt
        assert "# Verksted demo workspace" in prompt and (step / "AGENTS.md").read_text(encoding="utf-8").strip() in prompt


def test_the_skills_call_examples_reach_the_tool(probed):
    for searched in probed["searched"]:
        assert "verksted" in searched["results"][0]["matches"], searched
    codes = _error_codes()
    # Placeholders: a handle that is no run of this chat, a folder that does not exist. The answers
    # must come from the verksted handler, past its argument check.
    for answer in probed["bridged"] + probed["direct"]:
        assert answer["ok"] is False and answer["error"]["code"] in codes - {"bad_args"}, answer
    assert {a["error"]["code"] for a in probed["bridged"]} == {"not_found"}
