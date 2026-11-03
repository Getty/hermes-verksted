# Verksted House Rules

The shared house rules come from skilletor — bundle `getty-house@getty`: engineering
discipline, the delegation lock, the publish gate, karr coordination. They load as
`.claude/rules/.local.*.md`; if they are missing, run `skilletor sync` and publish nothing
until they load. This file adds what is true only here, and wins where it differs.

## What this repo is

A Hermes Agent plugin package: the `verksted` tool hands a bounded task to a regular Hermes
session in a workspace folder, started through the Desktop SDK, and returns the result to the
parent session. Read first: `docs/architecture.md` (German); its invariants are skill
`verksted-core`.

## Agent team

| Task | Agent |
|---|---|
| Implement / refactor / debug plugin code | `verksted-worker` (default) |
| Write/extend tests and acceptance scenarios | `verksted-test-writer` |
| Commits, `Changes`, card → done, release audit | `verksted-release-manager` |
| Read-only research, audit, interface check against a Hermes build | `verksted-worker` ("read-only, change nothing") |

Behavior-relevant = everything in the plugin package — `plugin.yaml`, the Python tool and
state store, `dashboard/`, `desktop/plugin.js`, the bundled Hermes skill
`skills/verksted/SKILL.md` (it steers the Hermes main agent) — and the tests. README and other
prose are not. A changed decision in `docs/architecture.md` is the maintainer's call: propose
it, don't make it.

## Release

The release is the plugin package, agent part and Desktop part together. Building it locally
is fine; installing it into the maintainer's own Hermes home or Desktop needs the go-ahead,
like any publication.

## Hazards

- **The dev box is Linux, the target is Hermes Desktop on Windows.** A green run here says
  nothing about drive letters, spaces and umlauts in paths, junctions, the WSL path space or a
  console window popping up. Until the maintainer has run a scenario on Windows, the report
  says "not exercised on Windows".
- **The Hermes interfaces move.** The design was checked against Hermes `ad2b705`; the
  references in `hermes-plugin-engineering` are pinned to another commit. A call that matches
  the docs but is missing in the installed build fails only inside the GUI. Check each SDK and
  gateway call against the target build before building on it, and name that build in the report.
- **`hermes plugins enable` against a throwaway home** re-resolves Hermes' whole dependency
  environment into `$HERMES_HOME/cache/uv` (~500 MB); on `/tmp` (tmpfs) that fills the disk.
  Set `plugins.enabled` in the test home's `config.yaml` instead (learned in `~/dev/hermes-citiai`).

## Hermes specifics — reference, don't restate

Hermes extension points, SDK contracts and test practice: skill `hermes-plugin-engineering`;
the Verksted design invariants: skill `verksted-core` (both force-loaded for `verksted-*` agents).
