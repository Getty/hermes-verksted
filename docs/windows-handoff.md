# Handoff: continuing Verksted on Windows

Start here when you are the Claude Code session on the maintainer's Windows machine. The
package was built and tested on Linux overnight on 2026-11-03, against Hermes `ad2b705`
(v0.22.6-414). **Nothing has been exercised on Windows or in the real Hermes Desktop.** Your
job is the part Linux cannot do: run the package in the real Desktop and its backend, record
what happens, and turn the results into cards.

## 1 Where things stand

- **Package:** `verksted/` (agent part, plugin API, Desktop part, bundled skill), version
  `0.1.0`, unreleased. `Changes` has an unstamped `{{$NEXT}}` section.
- **Repository:** https://github.com/Getty/hermes-verksted (public), branch `main`; the karr
  board is pushed too (`refs/karr/*`).
- **Verified on Linux** (all against throwaway homes under `.dev/`, Hermes `ad2b705`):
  - `make test`: 258 pytest (incl. a contract test against the pinned Hermes) and 89 node
    tests, 0 skipped.
  - `make test-e2e`: 44 passed, 0 skipped (~9–14 min depending on load). Real pinned backend,
    Node stand-in for the Desktop renderer, scripted model.
  - `make doctor` OK; Hermes' plugin security scan says `safe`; `hermes plugins validate` ok
    (one known warning: private core imports in `verksted/hermes_host.py`).
- **Board** (`karr list --compact`):
  - k1 — record the Windows Desktop and backend build; blocked on you.
  - k4 — the step-1 acceptance; waits for the Windows run (scenarios A–F).
  - k2, k3, k5–k11 are done on Linux (toolset check, prototype, config and profiles, Windows
    path rules, skill, cancel/send/lost confirmations, result outputs, packaging, further e2e).

What the Linux tests are and are not: the backend is the real pinned Hermes gateway; the
Desktop renderer is a Node stand-in ported from the Desktop's canonical client
(`tests/e2e/shim/`); the model is scripted. Drive letters, junctions, the packaged Electron app,
`file://` installs, window unload, console windows and the WSL path space are not covered.

## 2 Getting the repo onto Windows

```powershell
git clone https://github.com/Getty/hermes-verksted.git
cd hermes-verksted
karr sync --pull        # the board lives in refs/karr/*, which a plain clone does not fetch
karr list --compact     # k1 and k4 should be the open cards
```

Then, in the Windows checkout:

1. Install the tools the agent team needs: `skilletor`, `karr`, and the `briefing` plugin for
   Claude Code. Run `skilletor sync`. It installs the shared house rules
   (`.claude/rules/.local.*.md`) and the skills `hermes-plugin-engineering`, the git and the
   karr skills; they are git-ignored, so a copy or clone does not carry them. Until the rules
   load, publish nothing (`.claude/rules/verksted-rules.md`).
2. Spawn only `verksted-*` agents. If a spawn is refused because a briefing skill does not
   resolve, `skilletor sync` did not run — fix that, do not substitute a built-in agent.

## 3 Rules that bite on Windows

- **Never touch the maintainer's own Hermes home** (`%LOCALAPPDATA%\hermes`) or Desktop
  profile without the maintainer's explicit go-ahead. Every run uses a throwaway
  `HERMES_HOME` and `HERMES_DESKTOP_USER_DATA_DIR` (`docs/windows-acceptance.md` §0). If the
  packaged Desktop ignores those variables, stop and ask.
- A bare `hermes …` without `HERMES_HOME` creates or uses the real home. Set it first, every
  time, in the same shell.
- Never `hermes plugins enable` against a throwaway home (it re-resolves the whole dependency
  environment into it); set `plugins.enabled` in its `config.yaml`. Do not use
  `hermes plugins install` either unless the scenario is the install itself.
- Copy the package, never link it: Hermes refuses symlinks and junctions in plugin folders.
- When updating the package, delete `<HERMES_HOME>\desktop-plugins\verksted\` before Rescan:
  Desktop refreshes its copy only when the source file is newer.
- No push, tag, release, public issue or PR without the go-ahead.

## 4 Order of work

1. **Record the build (k1).** Follow `docs/hermes-interfaces.md` "Windows check for the
   maintainer" §1–3: Desktop version and install stamp, backend version and commit, native or
   WSL backend, plugin roots. If the build is not `ad2b705` / `v0.22.6`, compare the interfaces
   of that document first (§5 console checks) — the design depends on them, and a call that is
   missing only fails inside the GUI.
2. **Package and install** into the throwaway home: on Linux `make package` builds
   `dist/verksted-<version>.zip` (top folder `verksted/`); on Windows follow
   `docs/windows-acceptance.md` §0 (PowerShell, `Expand-Archive`, `plugins.enabled`). The
   Desktop install dialog with a `file:///C:/…#verksted` URL is a scenario of its own: whether
   the dialog accepts the `#subdir` form has only been read in the source.
3. **Run the acceptance scenarios** in `docs/windows-acceptance.md`: A–F (step 1, card k4),
   G–K (paths, k6), and the rows added after k8/k10/k9 (cancel, send, lost confirmation,
   gateway kill, result outputs, Rückfrage without viewer, approval timeout, lean profile,
   update). Each row names the Linux test that mirrors it.
4. **Report per scenario:** "exercised on Windows build `<commit>`: pass / fail / not run",
   in that document's results section and as a note on the card. A failure is a new card with
   the exact steps, the DevTools console output and the run's `status`.
5. Cards: note the build on k1 and unblock it; k4 goes to the release-manager once A–F pass.
   A release (stamping `Changes`, tagging, installing into the real home) waits for the
   maintainer.

## 5 Decisions taken overnight, awaiting the maintainer

The orchestrating agent took these so the board could be worked through without questions.
They are working choices, recorded on the cards and in the docs, not the maintainer's
decisions. Each is reversible; most need no schema change.

| Where | Decision | Record |
|---|---|---|
| Toolsets | D1 (a): under a ceiling an explicit list is required; without list and ceiling no `toolsets` are sent and the effective set is recorded | `docs/toolset-check.md` §10, k2, k5 |
| Toolsets | M2: Verksted's own toolset is tolerated in a child; no runs from a Verksted child (depth 1) | `docs/toolset-check.md` §10 |
| Toolsets | M3: every explicit list gets `no_mcp`; MCP only through the default case or a slim profile | `docs/toolset-check.md` §10 |
| Gateway | C2: runs only on a canonical route whose `runtime.describe` lists `durable-admission-v1` | `docs/toolset-check.md` §10, `docs/hermes-interfaces.md` |
| Gateway | C1: children are identified by session id, not by `source` (the SDK sends `gui`) | same |
| Package | the package root is the subfolder `verksted/`, not the repo root | k3 |
| Sessions | no session title is ever sent (a title resolves to an existing session before `request_id`) | k3, `docs/toolset-check.md` §11 |
| Start | P1: a lost submit reply in a non-fresh session with nothing pending repeats the identical submit (reverses k3 decision 16) | k8 |
| States | P2: new final state `cancelled` for runs Hermes never saw; otherwise "stopped" only when Hermes confirms | k8 |
| Store | P3: schema v2 with a turn counter, a turn archive and `send_waiting`; `send` only on a finished run | k8 |
| Rückfragen | P5: arch §5 says the child waits for "Session öffnen" — true for approvals within `approvals.timeout`, for a Rückfrage only while a Desktop window is attached; Verksted reports `unanswered` / `blocked` | k8, proposal for arch §5 |
| Result | P4 option B: the result keeps the clipped final answer, a bounded summary and checked output references | k10, proposal for arch §4 |
| Result | P6–P9: outputs are checked against the workspace root, in the plugin API at `finished`; Verksted appends its own result-block request to the task; the check proves existence and containment, not authorship | k10 |
| Config | arch §3 "without toolsets the target profile's defaults apply within the ceiling" contradicts D1 (a) | k5 drift, wording is the maintainer's call |
| Release | `requires_hermes: ">=0.22.6"` has no upper bound | k9 |
| Desktop | the last Desktop poll is kept in the store's `meta` table; `status` reports `desktop_last_poll_s`, a poll counts as recent up to 60 s; nothing starts or restarts because of it | k9 |
| Desktop | without `profileRoutes`, `requestProfile`, `retainProfile` or `openSession` the Desktop part starts nothing and shows "Hermes Desktop zu alt für Verksted"; a 404 from the plugin API shows "Agent-Teil nicht aktiviert" | k9 |
| Skill | the skill description is cut to Hermes' 60-character row: "Folder tasks and workflow steps in own sessions: run, track." | k9 |
| Build | `make package` builds only from a clean committed `verksted/`; `make install` copies HEAD and writes outside `.dev/` only with `I_MEAN_IT=1` | k9 |

## 6 Open points only Windows can answer

- Does the packaged Desktop honour `HERMES_HOME` / `HERMES_DESKTOP_USER_DATA_DIR`?
- Does the install dialog accept `file:///C:/…#verksted`?
- A WSL backend contributes no Desktop part to the Windows Desktop (k1 C20, to confirm).
- `host.requestProfile` reaches the canonical owner and returns `enabled_toolsets`, `lazy` and
  `desktop_protocol` (`docs/toolset-check.md` §10 "Open points for the Windows run").
- Does closing a Desktop window run the plugin's dispose (claims given back), or does takeover
  wait out the 180 s lease?
- Paths: drive-letter case, spaces and umlauts, junctions, `\\?\`, long paths (G–K).
- Does any step open a console window?
- If Verksted is missing on a backend run from a git checkout (e.g. a source install in WSL),
  look for `Plugin 'verksted' skipped: requires hermes …` in `logs/errors.log` first: a slow
  `git` can make Hermes report an older version (`docs/hermes-interfaces.md` §8). The packaged
  Desktop reads its install stamp and is not affected.
