"""``.verksted.yaml`` resolution, frozen per run (docs/architecture.md §2, §3; card k5).

The walk reads every ``.verksted.yaml`` from the workspace root down to the target folder, on
canonical paths of the executing backend (``paths.Backend``):

- **Workspace root.** With registered ``workspaces`` in the plugin settings, the target must lie
  inside one of them and the outermost one that contains it is the root, so no registration
  deeper down can cut an ancestor's file out of the walk. Without registration, every ancestor up
  to the filesystem root is read and the topmost one with a file is the root. The chain is at
  most ``MAX_DEPTH`` folders.
- **Merge.** Missing values inherit. The nearest explicit ``toolsets`` list is the selection;
  every list above it and the plugin setting ``toolset_ceiling`` are ceilings, and a list that
  names anything beyond a ceiling is refused, never clipped. Under any ceiling the selection
  must be explicit (decision D1 (a), docs/toolset-check.md §10). The nearest ``profile`` wins.
- **Keys.** Only ``version``, ``profile`` and ``toolsets``; anything else is refused, so no
  credentials, backend addresses, models or MCP servers come from a project folder.
- **Profiles.** A profile other than the calling one must be in the plugin setting
  ``allowed_profiles`` and exist; the session then runs there, the store stays with the caller.

The plugin settings are the calling profile's ``plugins.entries.verksted.settings`` (read through
``ctx.get_config``), never anything a folder or the model supplies.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Optional

from . import paths as vpaths

CONFIG_NAME = ".verksted.yaml"
CONFIG_VERSION = 1
MAX_DEPTH = 64
_KEYS = ("version", "profile", "toolsets")
_FORBIDDEN = ("api_key", "base_url", "provider", "model", "env", "mcp_servers")
_MAX_BYTES = 64 * 1024
# hermes_constants.PROFILE_ID_RE at ad2b705 (profile ids are lowercase on disk).
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Verksted adds no_mcp itself (decision M3); its own toolset is never granted by a folder, so a
# child in another profile cannot start further runs past the recursion guard.
RESERVED_TOOLSETS = ("no_mcp", "verksted")
SETTING_KEYS = ("workspaces", "allowed_profiles", "toolset_ceiling")
SETTINGS_PATH = "plugins.entries.verksted.settings"


class ConfigRefused(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_yaml(text: str, where: str) -> Any:
    from ruamel.yaml import YAML  # a Hermes core dependency; third-party, not a Hermes internal
    from ruamel.yaml.error import YAMLError
    try:
        return YAML(typ="safe", pure=True).load(text)
    except YAMLError as exc:
        raise ConfigRefused("bad_config", f"{where} is not valid YAML: {exc}") from exc


def validate_toolsets(value: Any, where: str = CONFIG_NAME, code: str = "bad_toolsets") -> list[str]:
    """Step 1 of the check in docs/toolset-check.md §6 for one list."""
    if not isinstance(value, list):
        raise ConfigRefused(code, f"{where}: toolsets must be a list of toolset names")
    seen: set[str] = set()
    for name in value:
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ConfigRefused(code, f"{where}: toolset names must be non-empty strings: {name!r}")
        if name in seen:
            raise ConfigRefused(code, f"{where}: toolset listed twice: {name!r}")
        if name in ("all", "*") or name.startswith("hermes-"):
            raise ConfigRefused(code, f"{where}: composite toolset not allowed: {name!r}")
        if name == "no_mcp":
            raise ConfigRefused(code, f"{where}: no_mcp is added by Verksted itself; leave it out")
        if name == "verksted":
            raise ConfigRefused(code, f"{where}: Verksted's own toolset is never granted through a folder "
                                      "config; leave it out")
        seen.add(name)
    return list(value)


# -- plugin settings ----------------------------------------------------------------------------

def read_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the calling profile's plugin settings. A malformed setting refuses every run
    (``bad_settings``) rather than being ignored."""
    def where(key: str) -> str:
        return f"{SETTINGS_PATH}.{key}"

    def str_list(key: str) -> list[str]:
        value = raw.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise ConfigRefused("bad_settings", f"{where(key)} must be a list of non-empty strings")
        return list(value)

    workspaces = str_list("workspaces")
    allowed = str_list("allowed_profiles")
    for name in allowed:
        if not _PROFILE_ID.fullmatch(name):
            raise ConfigRefused("bad_settings", f"{where('allowed_profiles')}: {name!r} is not a profile id "
                                                "(lowercase letters, digits, - and _)")
    ceiling = raw.get("toolset_ceiling")
    if ceiling is not None:
        ceiling = validate_toolsets(ceiling, where("toolset_ceiling"), "bad_settings")
    return {"workspaces": workspaces, "allowed_profiles": allowed, "toolset_ceiling": ceiling}


# -- the walk -----------------------------------------------------------------------------------

def _chain(folder: str, top: Optional[str], backend: vpaths.Backend) -> list[str]:
    """Folders from ``top`` (or the filesystem root) down to ``folder``, both canonical."""
    pm = backend.pathmod
    dirs = [folder]
    cur = folder
    while top is None or backend.key(cur) != backend.key(top):
        parent = pm.dirname(cur)
        if parent == cur:
            break
        cur = parent
        dirs.append(cur)
        if len(dirs) > MAX_DEPTH:
            raise ConfigRefused("folder_too_deep", f"{folder!r} is more than {MAX_DEPTH} folders deep "
                                                   f"{'below its workspace' if top else 'on the backend'}")
    dirs.reverse()
    return dirs


def _registered_root(folder: str, workspaces: list[str], backend: vpaths.Backend) -> str:
    roots: list[str] = []
    for raw in workspaces:
        try:
            canonical, _ = vpaths.canonical_folder(raw, backend)
        except vpaths.PathRefused as exc:
            if exc.code in ("folder_missing", "not_a_folder"):
                continue  # a registered folder that is not there now cannot contain the target
            raise ConfigRefused("bad_settings", f"{SETTINGS_PATH}.workspaces: {exc}") from exc
        if backend.inside(canonical, folder):
            roots.append(canonical)
    if not roots:
        raise ConfigRefused("not_in_workspace",
                            f"{folder!r} is not inside a registered Verksted workspace. Register the workspace "
                            f"folder under {SETTINGS_PATH}.workspaces in the calling profile's config.yaml "
                            "(Hermes Desktop: Settings → Plugins → Verksted), or run in a registered one.")
    return min(roots, key=lambda r: len(backend.key(r)))  # outermost: all of them lie on one chain


def _read_file(path: str, backend: vpaths.Backend) -> Optional[tuple[dict[str, Any], str]]:
    """``(values, sha256)`` of one config file, None when there is none. Fails closed: a file
    that exists but cannot be read refuses the run."""
    if not backend.lexists(path):
        return None
    if not backend.isfile(path):
        raise ConfigRefused("bad_config", f"{path} is not a readable file")
    try:
        raw = backend.read_bytes(path, _MAX_BYTES)
    except OSError as exc:
        raise ConfigRefused("bad_config", f"{path} cannot be read: {exc}") from exc
    if len(raw) > _MAX_BYTES:
        raise ConfigRefused("bad_config", f"{path} is larger than {_MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigRefused("bad_config", f"{path} is not UTF-8: {exc}") from exc
    loaded = _load_yaml(text, path)
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigRefused("bad_config", f"{path} must be a mapping")
    keys = [str(k) for k in loaded]
    forbidden = sorted(k for k in keys if k in _FORBIDDEN)
    if forbidden:
        raise ConfigRefused("bad_config", f"{path}: {forbidden} cannot be set by a folder; credentials, backend "
                                          "addresses, models and MCP servers belong to a Hermes profile "
                                          "(name the profile with profile:)")
    unknown = sorted(k for k in keys if k not in _KEYS)
    if unknown:
        raise ConfigRefused("bad_config", f"{path}: unknown keys {unknown} (this build knows {list(_KEYS)})")
    for key, value in loaded.items():
        if value is None:
            hint = "; write [] for an empty selection" if key == "toolsets" else ""
            raise ConfigRefused("bad_config", f"{path}: {key} has no value; remove the key to inherit{hint}")
    if "version" in loaded:
        version = loaded["version"]
        if type(version) is not int or version != CONFIG_VERSION:
            raise ConfigRefused("bad_config", f"{path}: version must be {CONFIG_VERSION}")
    if "profile" in loaded:
        profile = loaded["profile"]
        if not isinstance(profile, str) or not _PROFILE_ID.fullmatch(profile):
            raise ConfigRefused("bad_config", f"{path}: profile must be a Hermes profile id (lowercase letters, "
                                              f"digits, - and _): {profile!r}")
    if "toolsets" in loaded:
        loaded["toolsets"] = validate_toolsets(loaded["toolsets"], path)
    return loaded, "sha256:" + hashlib.sha256(raw).hexdigest()


def resolve(folder: str, *, parent_profile: str, parent_profile_home: str,
            settings: Optional[Mapping[str, Any]] = None,
            profile_home: Optional[Callable[[str], Optional[str]]] = None,
            backend: vpaths.Backend = vpaths.LOCAL) -> dict[str, Any]:
    """The frozen configuration for a run in ``folder`` (canonical, from ``paths.canonical_folder``)."""
    s = read_settings(settings or {})
    registered = bool(s["workspaces"])
    top = _registered_root(folder, s["workspaces"], backend) if registered else None
    found: list[tuple[str, dict[str, Any], str]] = []
    for directory in _chain(folder, top, backend):
        path = backend.pathmod.join(directory, CONFIG_NAME)
        got = _read_file(path, backend)
        if got is not None:
            found.append((path, *got))
    root = top if registered else (backend.pathmod.dirname(found[0][0]) if found else folder)

    ceilings: list[dict[str, Any]] = []
    if s["toolset_ceiling"] is not None:
        ceilings.append({"source": f"{SETTINGS_PATH}.toolset_ceiling", "toolsets": s["toolset_ceiling"]})
    selection: Optional[list[str]] = None
    profile: Optional[str] = None
    for path, values, _ in found:
        if "toolsets" in values:
            listed = values["toolsets"]
            for ceiling in ceilings:
                beyond = [n for n in listed if n not in ceiling["toolsets"]]
                if beyond:
                    raise ConfigRefused("toolsets_widen",
                                        f"{path} lists {beyond}, beyond the ceiling {ceiling['toolsets']} of "
                                        f"{ceiling['source']}; a folder may only narrow the toolsets above it")
            ceilings.append({"source": path, "toolsets": listed})
            selection = listed
        if "profile" in values:
            profile = values["profile"]
    if ceilings and selection is None:
        raise ConfigRefused("toolsets_required",
                            f"a toolset ceiling applies ({ceilings[0]['source']}: {ceilings[0]['toolsets']}), so "
                            f"the selection must be explicit: list this folder's toolsets in {CONFIG_NAME} "
                            "(the profile's defaults cannot be checked against a ceiling)")

    session_profile = profile or parent_profile
    home = parent_profile_home
    if session_profile != parent_profile:
        if session_profile not in s["allowed_profiles"]:
            raise ConfigRefused("profile_not_allowed",
                                f"{CONFIG_NAME} names profile {session_profile!r}, which the calling profile "
                                f"{parent_profile!r} does not allow (allowed: {s['allowed_profiles']}); add it "
                                f"under {SETTINGS_PATH}.allowed_profiles in the calling profile's config.yaml")
        home = profile_home(session_profile) if profile_home else None
        if not home:
            raise ConfigRefused("profile_missing", f"profile {session_profile!r} does not exist on this backend")

    return {
        "version": CONFIG_VERSION,
        "workspace": root,
        "registered": registered,
        "profile": session_profile,
        "profile_home": home,
        "profile_switched": session_profile != parent_profile,
        "toolsets": selection,
        "ceilings": ceilings,
        "files": [{"path": path, "sha256": digest} for path, _, digest in found],
    }


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
