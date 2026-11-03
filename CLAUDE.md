# CLAUDE.md — Verksted

Hermes Agent plugin package, working name **Verksted**: a folder becomes a persistent
Hermes workspace; the `verksted` tool lets the Hermes main agent start or resume a regular
Hermes session in it through the Desktop SDK and collect the result. First acceptance
target: the official Hermes Desktop on **Windows**. The karr board follows the three
implementation steps of the design; the package is built and tested on Linux, the Windows
acceptance runs are still open (`docs/windows-acceptance.md`). **On Windows, start with
[`docs/windows-handoff.md`](docs/windows-handoff.md).**

- **Design:** [`docs/architecture.md`](docs/architecture.md) (German) — read first. It
  names the Hermes revision its interfaces were checked against.
- **Rules + delegation:** shared house rules via the skilletor bundle `getty-house@getty`;
  lanes and hazards in `.claude/rules/verksted-rules.md` (auto-loaded).
- **Board:** `karr list --compact`.
- **Build / test:** `make dev-env` once (pinned Hermes ad2b705 + venv in `.dev/`), then
  `make test` — pytest on `tests/agent` (incl. a contract test against the pinned Hermes)
  plus `node --test` on `tests/desktop`; green before `review`. `make test-e2e` runs the
  Linux end-to-end suite (real Hermes backend, Node stand-in for the Desktop renderer;
  ~8 min, one run at a time — two wipe each other's `.dev/e2e`). `make doctor` runs
  `hermes plugins doctor`, `make package` builds `dist/verksted-<version>.zip` from HEAD,
  `make install` copies HEAD into `.dev/home`; every target stays under `.dev/`.

## Delegation

Delegate behavior-relevant code to the right agent instead of touching it yourself —
principle in the shared rule `getty-agent-delegation`, lanes in `.claude/rules/verksted-rules.md`.

| Task | Agent |
|---|---|
| Plugin code: Python tool + state, plugin API, Desktop part, bundled Hermes skill | `verksted-worker` (default) |
| Write or extend tests and acceptance scenarios | `verksted-test-writer` |
| Commits, `Changes`, card → done, release audit | `verksted-release-manager` |
| Read-only research, audit, checking an interface against a Hermes build | `verksted-worker` ("read-only, change nothing") |

Every subagent is a `verksted-*` agent — never the built-in `general-purpose`/`Explore`/`Plan`.

The agents carry their skills via `briefing.skills` (see `.claude/agents/`); the main agent
delegates rather than loading them. `verksted-core` (the design invariants) is owned here
under `.claude/skills/`; `hermes-plugin-engineering`, the git and the karr skills are
installed by skilletor (`.claude/skilletor.json`).

## Package layout

From the design, step 3. The package root is `verksted/` (an install copies only that folder,
not `tests/`, `docs/` or `.dev/`); the paths below are relative to it.

| Path | Contents |
|---|---|
| `plugin.yaml` + Python entry point | agent plugin: the `verksted` tool (`run`, `status`, `wait`, `send`, `cancel`), config resolution, path checks, SQLite run/claim store |
| `dashboard/manifest.json`, `dashboard/plugin_api.py` | backend API under `/api/plugins/<id>`: claims and state transitions for the Desktop part |
| `desktop/plugin.js` | Desktop SDK part: claims runs, creates/resumes sessions, shows status and "Session öffnen" |
| `skills/verksted/SKILL.md` | the Hermes skill shipped to users — product code, not a Claude skill |
| `docs/architecture.md` | the design |
