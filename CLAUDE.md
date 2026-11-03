# CLAUDE.md — Verksted

Hermes Agent plugin package, working name **Verksted**: a folder becomes a persistent
Hermes workspace; the `verksted` tool lets the Hermes main agent start or resume a regular
Hermes session in it through the Desktop SDK and collect the result. First acceptance
target: the official Hermes Desktop on **Windows**. No code yet — the karr board follows
the three implementation steps of the design.

- **Design:** [`docs/architecture.md`](docs/architecture.md) (German) — read first. It
  names the Hermes revision its interfaces were checked against.
- **Rules + delegation:** shared house rules via the skilletor bundle `getty-house@getty`;
  lanes and hazards in `.claude/rules/verksted-rules.md` (auto-loaded).
- **Board:** `karr list --compact`.
- **Build / test:** none yet — the first card that adds code sets the command and records
  it here.

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

## Planned package layout

From the design, step 3. Names not fixed there are set by the first card that needs them.

| Path | Contents |
|---|---|
| `plugin.yaml` + Python entry point | agent plugin: the `verksted` tool (`run`, `status`, `wait`, `send`, `cancel`), config resolution, path checks, SQLite run/claim store |
| `dashboard/manifest.json`, `dashboard/plugin_api.py` | backend API under `/api/plugins/<id>`: claims and state transitions for the Desktop part |
| `desktop/plugin.js` | Desktop SDK part: claims runs, creates/resumes sessions, shows status and "Session öffnen" |
| `skills/verksted/SKILL.md` | the Hermes skill shipped to users — product code, not a Claude skill |
| `docs/architecture.md` | the design |
