"""Contract test against the pinned Hermes (ad2b705 in .dev/hermes-agent): the real plugin loader
loads and registers the package from a throwaway HERMES_HOME, the real dispatcher invokes the
tool, and the real dashboard host mounts and authenticates the plugin API on the same store.

Runs in a subprocess so Hermes' process-global state never leaks into the other tests."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from conftest import HERMES_SRC, VENV_PYTHON

PROBE = __file__.replace("test_hermes_contract.py", "hermes_contract_probe.py")
PINNED = "ad2b705b4995a958b0a795e9097be661e2d1ef63"


def _pinned_revision() -> str:
    return subprocess.run(["git", "-C", str(HERMES_SRC), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture(scope="module")
def hermes_ready():
    if not VENV_PYTHON.exists() or not HERMES_SRC.exists():
        pytest.fail("the pinned Hermes is missing: run `make dev-env` first")  # fail loud, never skip
    assert _pinned_revision() == PINNED, "the .dev Hermes checkout is not at the pinned revision"


def test_real_hermes_loads_registers_and_invokes_the_plugin(hermes_ready, throwaway_home, tmp_path):
    home = throwaway_home("contract")
    workspace = home / "workspace ü"
    workspace.mkdir()
    # k5: the calling profile's plugin settings and a second, allowed profile next to it.
    research_home = home / "profiles" / "research"
    research_home.mkdir(parents=True)
    (research_home / "config.yaml").write_text("{}\n", encoding="utf-8")  # an identity marker
    switched = home / "research ws"
    switched.mkdir()
    (switched / ".verksted.yaml").write_text("profile: research\ntoolsets: [file]\n", encoding="utf-8")
    outside = tmp_path / "not registered"
    outside.mkdir()
    with (home / "config.yaml").open("a", encoding="utf-8") as fh:
        fh.write(f"  entries:\n    verksted:\n      settings:\n        workspaces: [{json.dumps(str(home))}]\n"
                 "        allowed_profiles: [research]\n")
    extra = json.dumps({"switched": str(switched), "outside": str(outside), "research_home": str(research_home)})
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_")}
    env.update(HERMES_HOME=str(home), HOME=str(home / "user"), TMPDIR=str(home / "tmp"))
    (home / "user").mkdir()
    (home / "tmp").mkdir()
    result = home / "probe-result.json"
    proc = subprocess.run([str(VENV_PYTHON), PROBE, str(workspace), str(result), extra], cwd=str(HERMES_SRC), env=env,
                          capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0 and result.is_file(), proc.stderr[-4000:]
    assert "predates version" not in proc.stderr, proc.stderr[-2000:]
    out = json.loads(result.read_text(encoding="utf-8"))

    assert out["plugin"] == {"found": True, "enabled": True, "error": None}
    assert out["entry"] == {"toolset": "verksted", "is_async": False, "schema_name": "verksted"}

    assert out["forged"]["error"]["code"] == "bad_args"
    run = out["run"]
    assert run["ok"] and run["state"] == "waiting_for_gui", run
    # A standalone HERMES_HOME is its own default root (hermes_constants.get_default_hermes_root).
    assert run["folder"] == str(workspace.resolve()) and run["profile"] == "default"
    assert out["status"]["ok"] and out["status"]["handle"] == run["handle"]
    assert out["busy"]["error"]["code"] == "folder_busy"
    # k5 against the real ctx.get_config and hermes_constants profile helpers.
    sw = out["switched"]
    assert sw["ok"] and sw["profile"] == "research" and sw["config"]["profile_switched"] is True, sw
    assert sw["config"]["workspace"] == str(home.resolve()) and sw["toolsets"] == ["file"]
    assert out["outside"]["error"]["code"] == "not_in_workspace", out["outside"]
    assert out["deferrable"] is True  # ad2b705 has no registration option that keeps a plugin tool eager
    assert out["searched"]["results"][0]["matches"] == ["verksted"], out["searched"]
    assert out["searched"]["tools"]["verksted"]["source"] == "plugin"
    assert out["bridged"]["ok"] and out["bridged"]["handle"] == run["handle"], out["bridged"]
    assert out["store_file"] is True
    # The doorbell: one plugin event per stored run, no run data in it.
    bells = [f for f in out["frames"] if "plugin.verksted.work" in json.dumps(f)]
    assert len(bells) == 2, out["frames"]  # the run and the switched run
    assert run["handle"] not in json.dumps(bells)

    base = "/api/plugins/verksted"
    assert {f"{base}/runs", f"{base}/runs/claimable", f"{base}/runs/{{run_id}}/claim",
            f"{base}/runs/{{run_id}}/step"} <= set(out["mounted"])
    assert out["no_auth"] == 401 and out["bogus_ticket"] == 401
    claimable = out["claimable"]
    assert claimable["status"] == 200 and claimable["body"]["ok"]
    assert claimable["body"]["profile_home"] == out["home"]
    assert sorted(r["run_id"] for r in claimable["body"]["runs"]) == sorted([run["handle"], sw["handle"]])
    assert out["claim"] == {"ok": True, "generation": 1, "mode": "start", "code": None}
    assert {"create_params", "prompt_text", "request_id", "submission_id"} <= set(out["claim_run_keys"])
    assert out["step"]["ok"] and out["step"]["run"]["state"] == "create_requested"
    assert out["stale"] == {**out["stale"], "ok": False, "code": "stale_generation"}
    assert out["mismatch"]["code"] == "profile_mismatch"
    assert out["switched_parent_route"]["code"] == "profile_mismatch", out["switched_parent_route"]
    assert out["switched_claim"] == {"ok": True, "code": None, "session_profile": "research",
                                     "own_toolset_tolerated": False}
