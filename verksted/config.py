"""``.verksted.yaml`` resolution, frozen per run.

Step-1 prototype: only the target folder's own file, keys ``version``, ``profile`` and
``toolsets``. Inheritance from the workspace root, the ceiling, the allowed-profiles list and
profile switching are card k5; the stored shape (``resolved_config`` + fingerprint) is already
the one k5 extends.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

CONFIG_NAME = ".verksted.yaml"
CONFIG_VERSION = 1
_KEYS = {"version", "profile", "toolsets"}
_MAX_BYTES = 64 * 1024


class ConfigRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_yaml(text: str) -> Any:
    from ruamel.yaml import YAML  # a Hermes core dependency; third-party, not a Hermes internal
    from ruamel.yaml.error import YAMLError
    try:
        return YAML(typ="safe", pure=True).load(text)
    except YAMLError as exc:
        raise ConfigRefused("bad_config", f"{CONFIG_NAME} is not valid YAML: {exc}") from exc


def validate_toolsets(value: Any) -> list[str]:
    """Step 1 of the check in docs/toolset-check.md §6 (no ceiling yet)."""
    if not isinstance(value, list):
        raise ConfigRefused("bad_toolsets", "toolsets must be a list of toolset names")
    seen: set[str] = set()
    for name in value:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ConfigRefused("bad_toolsets", f"toolset names must be non-empty strings: {name!r}")
        if name in seen:
            raise ConfigRefused("bad_toolsets", f"toolset listed twice: {name!r}")
        if name in ("all", "*") or name.startswith("hermes-"):
            raise ConfigRefused("bad_toolsets", f"composite toolset not allowed: {name!r}")
        if name == "no_mcp":
            raise ConfigRefused("bad_toolsets", "no_mcp is added by Verksted itself; leave it out")
        seen.add(name)
    return list(value)


def resolve(folder: str, *, parent_profile: str) -> dict[str, Any]:
    """The frozen configuration for a run in ``folder``."""
    path = Path(folder) / CONFIG_NAME
    data: dict[str, Any] = {}
    files: list[str] = []
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ConfigRefused("bad_config", f"{CONFIG_NAME} is larger than {_MAX_BYTES} bytes")
        loaded = _load_yaml(raw.decode("utf-8-sig"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigRefused("bad_config", f"{CONFIG_NAME} must be a mapping")
        unknown = sorted(set(map(str, loaded)) - _KEYS)
        if unknown:
            raise ConfigRefused("bad_config", f"{CONFIG_NAME}: unknown keys {unknown} (this build knows {sorted(_KEYS)})")
        data = loaded
        files.append(str(path))
    version = data.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigRefused("bad_config", f"{CONFIG_NAME}: version must be {CONFIG_VERSION}")
    profile = data.get("profile")
    if profile is not None and profile != parent_profile:
        raise ConfigRefused("profile_switch_unsupported",
                            f"{CONFIG_NAME} names profile {profile!r}; this build runs only in the calling "
                            f"profile {parent_profile!r} (allowed profiles come with a later build)")
    toolsets: Optional[list[str]] = None
    if "toolsets" in data and data["toolsets"] is not None:
        toolsets = validate_toolsets(data["toolsets"])
    return {"version": CONFIG_VERSION, "profile": parent_profile, "toolsets": toolsets, "files": files}


def fingerprint(resolved: dict[str, Any]) -> str:
    canonical = json.dumps(resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def start_toolsets(resolved: dict[str, Any]) -> tuple[Optional[list[str]], Optional[list[str]]]:
    """``(T, E)`` from docs/toolset-check.md §6: the list sent with ``session.create`` and the
    expected effective set. ``(None, None)`` is the default case (decision D1 (a), no ceiling):
    no ``toolsets`` are sent and the effective set is recorded at start."""
    listed = resolved.get("toolsets")
    if listed is None:
        return None, None
    return [*listed, "no_mcp"], sorted(set(listed))
