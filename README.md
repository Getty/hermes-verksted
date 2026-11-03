# Verksted

A Hermes Agent plugin that turns a folder into a persistent Hermes workspace. A bundled
skill lets the main agent hand a bounded task to a regular Hermes session in that folder —
optionally under another profile and with a narrowed tool set — and collect the result.
Subfolders can stand for the steps of a process. First target: the official Hermes Desktop
on Windows.

**Status:** 0.1.0, unreleased. Built and tested on Linux against Hermes `ad2b705`
(v0.22.6-414) with a real Hermes backend and a Node stand-in for the Desktop renderer. **Not
yet exercised on Windows or in the real Hermes Desktop** — the acceptance runs are written
down in [docs/windows-acceptance.md](docs/windows-acceptance.md).

## How it works

- The **agent part** (`verksted/`, Python) registers the `verksted` tool with the actions
  `run`, `status`, `wait`, `send` and `cancel`. `run` checks the folder, resolves its
  `.verksted.yaml`, stores the run with every id the start needs, and returns a handle. It
  never starts a session itself.
- The **Desktop part** (`verksted/desktop/plugin.js`) claims the stored run through the
  plugin API, creates a regular Hermes session in the folder through the public Desktop SDK,
  checks the session's effective toolsets before the first prompt, submits the task and
  follows it to the end.
- The child's questions ("Rückfrage") and approvals ("Freigabe erforderlich") are answered by
  the user in the child's own chat: **Verksted** in the sidebar → **Session öffnen**. The
  main agent never answers them.
- Hermes keeps the agent loop, history, memory, model access and approvals. Verksted stores
  only the mapping of run, parent session and child session (SQLite under the parent
  profile's `plugin-data/verksted/`).

The design is [docs/architecture.md](docs/architecture.md) (German); the interface checks
against Hermes are in [docs/hermes-interfaces.md](docs/hermes-interfaces.md) and
[docs/toolset-check.md](docs/toolset-check.md).

## Supported builds

| Component | Checked against | How |
|---|---|---|
| Hermes backend | `ad2b705` (v0.22.6-414), interfaces compared with tag `v0.22.6` | Linux: contract and e2e tests |
| Hermes Desktop | source at `ad2b705` | read only; the Node stand-in mirrors its canonical client |
| Windows Desktop + backend | — | **open** (k1, k4) |

`plugin.yaml` declares `requires_hermes: ">=0.22.6"`; Hermes enforces it at install and load.
On a Hermes run from a git checkout, a slow `git` can make Hermes report an older version and
skip the plugin (a `Plugin 'verksted' skipped: requires hermes …` WARNING in
`logs/errors.log`; [docs/hermes-interfaces.md](docs/hermes-interfaces.md) §8). Packaged builds
are not affected.
The Desktop part needs a canonical local gateway whose `runtime.describe` lists
`durable-admission-v1`; on anything else it refuses to start runs.

## Install

Both halves must be on: the agent part (`plugins.enabled` of the profile) **and** the
Desktop part (Hermes Desktop → Capabilities → Plugins). Copy the package, never link it —
Hermes refuses symlinks and junctions in plugin folders.

- **Desktop dialog** (Capabilities → Plugins → Install plugin): the Git URL with the
  subfolder, `https://github.com/Getty/hermes-verksted.git#verksted` (or a local clone as
  `file:///C:/Users/<you>/src/hermes-verksted#verksted`), pinned to a commit. One run installs
  and enables both halves. Needs Git on the machine. (Not yet exercised; whether the dialog
  accepts `#verksted` is the first Windows check.)
- **CLI:** `hermes plugins install https://github.com/Getty/hermes-verksted.git#verksted --ref <commit>`.
- **Manual copy:** unzip `verksted-<version>.zip` (built with `make package`, top folder
  `verksted/`) into `<HERMES_HOME>/plugins/`, or copy `verksted/` to
  `<HERMES_HOME>/plugins/verksted/`; add `verksted` to `plugins.enabled` in that profile's
  `config.yaml`, restart the gateway, then Rescan in Desktop and switch the Verksted Desktop
  part on. On Windows `<HERMES_HOME>` is `%LOCALAPPDATA%\hermes`; the PowerShell steps are in
  [docs/windows-acceptance.md](docs/windows-acceptance.md) §0.

The agent part is per profile (`<HERMES_HOME>/plugins/`, or the profile's own home); the
Desktop part is one per Desktop install. When only one half is on, Verksted says so: a run
whose profile no Verksted Desktop part has polled stays `waiting_for_gui` and `status` reports
`desktop_last_poll_s: null`; a Desktop part whose profile does not serve the plugin API shows
"Agent-Teil nicht aktiviert" in its status chip, and a Desktop too old for the SDK calls it
needs shows "Hermes Desktop zu alt für Verksted" and starts nothing.

**Updating:** delete `<HERMES_HOME>/desktop-plugins/verksted/` before copying a new version —
Desktop refreshes its copy of the Desktop part only when the source file is newer, and
Explorer or `Copy-Item` keep the old file dates. The run store in `plugin-data/verksted/` is
kept across updates.

Tool search: with Hermes' default `tools.tool_search.enabled: auto` the `verksted` tool is
loaded on demand; the bundled skill tells the main agent to reach it through `tool_search` →
`tool_call`.

## Configure

**Plugin settings** (per profile, `plugins.entries.verksted.settings` in `config.yaml`, or
Desktop → Settings → Plugins → Verksted):

| Setting | Meaning |
|---|---|
| `workspaces` | Absolute folders on the backend. When set, runs happen only inside them; the outermost one containing a target is the root its `.verksted.yaml` files are read from. |
| `allowed_profiles` | Profiles a folder may run its session in. The calling profile is always allowed. |
| `toolset_ceiling` | Optional upper bound for every run's toolsets. When set, every folder must list its toolsets. |

**`.verksted.yaml`** (optional, in the workspace and any subfolder):

```yaml
version: 1
profile: research          # optional, must be an allowed profile
toolsets: [file, web, clarify]
```

Files are read from the workspace root down to the target folder. Missing values inherit;
an explicit `toolsets` list replaces the inherited one and may only narrow it — a widening
list is refused, never clipped. Only `version`, `profile` and `toolsets` are accepted;
credentials, models and backend addresses are refused. The resolved state is frozen when the
run starts.

A list never grants MCP servers (Verksted adds `no_mcp`). A **slim profile** is the way to
keep MCP servers from starting at all: a reduced toolset list does not stop profile-wide MCP
servers from being spawned by the gateway.

See [examples/verksted-demo/](examples/verksted-demo/) for a two-step workflow
(`workflow/10-research`, `workflow/20-write`, results under `runs/<run-id>/`).

## Limits of version 1

- Toolsets only, no allowlist of single built-in tools.
- Hermes Desktop with the Verksted part must be open to start runs and to follow them; a
  child's question asked while no Desktop window is attached is lost (it ends as
  "unanswered"), and an approval nobody answers is denied after Hermes' approval timeout.
- Local folders only: UNC and device paths are refused.
- Folders, profiles and toolsets structure the work; they are not a sandbox.

## Development

```sh
make dev-env    # Hermes at the pinned commit + venv in .dev/ (once)
make test       # pytest (tests/agent, incl. a contract test against the pinned Hermes) + node --test (tests/desktop)
make test-e2e   # Linux e2e: real Hermes backend, Node stand-in for the Desktop renderer (~8 min)
make doctor     # hermes plugins doctor on the throwaway home .dev/home
make package    # dist/verksted-<version>.zip from the committed tree (refuses local changes)
make install    # copy the committed package into .dev/home (INSTALL_HOME=… for another home under .dev/)
```

Every target uses a throwaway `HERMES_HOME` under `.dev/`; nothing touches `~/.hermes`.
`make install` writes outside `.dev/` only with `I_MEAN_IT=1` — installing into a real Hermes
home is the maintainer's call. Continuing on Windows: [docs/windows-handoff.md](docs/windows-handoff.md).
