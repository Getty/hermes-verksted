# Checking the effective tool set before the first prompt (k2)

Question from `docs/architecture.md` §3 and §7.1 (verksted-core invariant 7): after
`session.create` and before `prompt.submit`, can a client that uses only public interfaces
learn the session's **effective** tool set reliably enough to refuse a start that exceeds
the request or the ceiling?

Checked against Hermes **`ad2b705`** (= v0.22.6-414, the design's pin) and compared with tag
**`v0.22.6`**. Every claim is marked:

- **[read]**: taken from the source at the cited lines.
- **[exercised]**: run in an in-process harness on Linux. The harness is the real
  `gateway.session_controls.AuthorityConnection` dispatch against a throwaway `HERMES_HOME`,
  with a probe plugin and two fake MCP servers.

Nothing here ran in the Desktop GUI, and nothing has been exercised on Windows.

## Verdict

**Yes, at toolset level, using public interfaces only, provided the request has a fixed
shape.** No Hermes extension is needed for v1. An exact allowlist of tool names cannot be
checked, which matches the v1 boundary in §3.

- **Where to read it.** `info.enabled_toolsets` in the `session.create` reply (the same
  object `session.info` returns) is the exact toolset list the first turn passes to the agent
  build. It is taken after Hermes has added plugin, MCP, `context_engine` and `x_search`
  toolsets and removed `agent.disabled_toolsets`. It is the effective set, not the requested
  one. [read + exercised]
- **What the effective set allows.** Only tools from that set can be called (§4). [read]
- **The request shape that makes the check stable.**
  - always send an explicit `toolsets` list;
  - always append `no_mcp`;
  - never send `follow_profile_config`.

  With this shape, none of the paths that re-derive the policy after create can add a toolset
  (§3). [read; the resume path exercised]
- **The check.** The effective set must equal the requested list exactly. Anything else is
  refused (§6).
- **What a Hermes list really means.** The list is *not* exact. Hermes adds:
  - every enabled plugin toolset the profile has not declined (`known_plugin_toolsets.cli`),
    including Verksted's own toolset;
  - every enabled MCP server, unless `no_mcp` is sent.

  So on an ordinary profile the check will **refuse** most restricted starts. That is correct
  behaviour, but it hurts usability. It needs a maintainer decision (§8). The decision does
  not block k3.

## 1 Where the effective set can be read

| Interface | Field and shape | Source at ad2b705 (v0.22.6 where different) | Effective or requested |
|---|---|---|---|
| `session.create` reply | `result.info` is `CanonicalSessionInfo`: `enabled_toolsets: list[str]`, `tools: {toolset: [names]}`, `tool_count`, `lazy: bool`, `launch_request`, `desktop_protocol` | create `gateway/session_controls.py:480-532` ends in `self.resume(ref, {})`; the resume builds `info = snapshot_info(...)` at `:679-684`; contract `tui_gateway/contracts/canonical.py:151-200` | `enabled_toolsets` is **effective**. `launch_request.toolsets` echoes the **request**. [read + exercised] |
| `session.info {session_id}` | the same object | handler `session_controls.py:554-559` → `gateway/session_local_inventory.py:192-206`; contract `canonical.py:206` ("Frozen launch policy projection") | effective; equal to the create reply's `info` in every probe case [exercised] |
| `session.resume` reply | `result.info`, same object, computed **after** any policy refresh | `session_controls.py:663-668` refreshes, then `:679-684` builds `info` | effective at that moment [read + exercised] |
| `tools.show {session_id}` | `{sections: [{name, tools: [{name, description}]}], total}`; lists tools deferred behind Tool Search | `gateway/session_ancillary.py:64-105`; contract `tui_gateway/contracts/tools_mcp_plugins.py:66` | the effective tool **names**, grouped by registered toolset. Errors propagate (fail-loud). [read + exercised] |

All of these files are identical at v0.22.6. [read]

### How the inventory is computed (`session_local_inventory.py`)

- **Lazy session** (no agent built yet):
  - `enabled = policy.current_toolsets(config)`;
  - the tools come from `model_tools.get_tool_definitions(enabled, disabled)` (`:44-56`,
    `:153-162`);
  - the first turn uses the same call: `gateway/run_turn.py:852-859` (v0.22.6 `:857`) →
    `policy_turn_toolsets` (`gateway/session_policy.py:1029-1033`, v0.22.6 `:1025`) →
    `agent/agent_init.py:1011-1038`.
- **Built agent:** `enabled = agent.enabled_toolsets` (`:144-152`).
- **`lazy`** is `agent is None` (`gateway/session_local.py:467`).
- **Fail-open:** if the lazy builder raises, the inventory reports `enabled_toolsets: []`
  and `tools: {}` (`:156-162`). An empty list on its own is not proof. [read]
- **`info.tools` and `tool_count` mislead.** Deferred plugin and MCP tools are collapsed
  behind `other: [tool_search, tool_describe, tool_call]`. A plugin tool can sit inside a
  built-in toolset name. Grouping uses the *registered* toolset of each tool, so overlapping
  toolsets are attributed to one of them. Use `enabled_toolsets` for the check and
  `tools.show` only for display. [exercised]

## 2 What `toolsets` means at ad2b705

Validation happens in `_resolve_toolsets` (`gateway/session_policy.py:652-705`, v0.22.6
`:648-701`):

- Each name must satisfy `saved_toolset_resolver` (`hermes_cli/toolset_validation.py:81-110`).
  That accepts:
  - registered toolsets;
  - configured MCP server names and their `mcp-<name>` forms, even before the server
    connects;
  - plugin toolset keys;
  - `hermes-<platform>` bundles;
  - `no_mcp`.
- Naming a disabled MCP server is refused.
- The list is written to `platform_toolsets.cli` and resolved with
  `hermes_cli/tools_config.py::_get_platform_tools` (`:571-646`).
- The only post-check is "requested tools ⊆ effective tools" (`:685-690`, refusal at `:689`;
  v0.22.6 `:685`). It is a subset check, never an exact one. [read]

Probe profile: plugin `probe` (toolset `probe`), enabled MCP server `fakemcp` (it never
connects) and disabled MCP server `offmcp`. Cases were run with `source: "gui"`, which the
Desktop SDK always sends (see the k1 finding below), and with `source: "tool"`. Both give the
same `enabled_toolsets` for every explicit list. [exercised]

| `toolsets` sent | Profile as installed | Profile with `known_plugin_toolsets: {cli: [probe]}` |
|---|---|---|
| *(absent)* | the whole profile default + `fakemcp` + `probe`; `gui` also adds `catalog`, `desktop_ui`, `project` | the same without `probe` |
| `[file, web]` | `[fakemcp, file, probe, web]` | `[fakemcp, file, web]` |
| `[]` | `[fakemcp, probe]` | `[fakemcp]`, `tool_count` 0 |
| `[no_mcp]` | `[probe]` | **`[]`**, `tool_count` 0 |
| `[file, no_mcp]` | `[file, probe]` | **`[file]`** |
| `[file, probe, no_mcp]` | `[file, probe]` | `[file, probe]` |
| `[file, fakemcp]` | `[fakemcp, file, probe]` | `[fakemcp, file]` |
| `[file, desktop_ui, no_mcp]` (gui) | `[desktop_ui, file, probe]` | `[desktop_ui, file]` |
| `[file, nosuchset]` | error 4001 `invalid_params`, `data: {field: "toolsets", detail: "unknown or invalid toolset name: 'nosuchset'"}` | same |
| `[file, offmcp]` | error 4001 `invalid_params`, `detail: "MCP server 'offmcp' is disabled in config"` | same |
| `[hermes-cli]` | error 4001, `detail: "not available on this surface: kanban_…"` | same |
| `[all]` | accepted; contains `all` itself plus every toolset, including `yuanbao` | same |

What follows from the table:

- **An explicit list is not exact.** On top of the names sent, `_get_platform_tools` adds:
  - **Plugin toolsets.** Every enabled one is added unless the profile has it in
    `known_plugin_toolsets.cli` and it is missing from the list (`_enabled_plugin_toolsets`,
    `tools_config.py:520-527`, applied at `:619-620`). Only the Tools-config save writes
    `known_plugin_toolsets` (`_save_platform_tools`, `:804`). This also covers a plugin
    that registers a tool *into a built-in toolset name*: with the probe tool registered
    into `file`, `[no_mcp]` still produced `[file]` even on the declined profile, because
    `file` had become a plugin toolset key. [exercised]
  - **MCP servers.** Every enabled server is added unless `no_mcp` is sent or a server is
    named (`_merge_mcp_servers`, `:733-753`). `no_mcp` wins over named servers (`:746`).
    [read + exercised]
  - **`context_engine`**, when a non-default context engine is configured and the list is not
    empty (`:624-625`). [read]
  - **`x_search`**, when xAI credentials exist and the list names no configurable or plugin
    toolset (composite branch, `:514-516`). For example `[no_mcp]` or `[]`. [read]
  - **Recovered platform-native toolsets**, which are non-configurable (`:709-731`). None
    appeared on `cli`. [read + exercised]
  - **Recently shipped toolsets** (`_RECENTLY_SHIPPED_TOOLSETS`, `:431`). The set is empty
    at both revisions, but a later build may fill it. [read]
  - `agent.disabled_toolsets` is subtracted last (`:638-642`). [read]
- **`[]` is not empty.** It gets every enabled MCP server and every undeclined plugin
  toolset. Only `[no_mcp]`, on a profile that has declined all plugin toolsets, with the
  default context engine and no xAI credentials, gives `enabled_toolsets: []`. [exercised]
- **Unknown names are refused** as `invalid_params` with `field: "toolsets"`. Hermes never
  drops them silently. [exercised]
- **Default (no `toolsets`).** The profile's `platform_toolsets.cli` (or the platform
  default) plus everything listed above. With `source: "gui"` Hermes also adds `project`,
  `desktop_ui` and `catalog` (`session_policy.py:691-701`, `toolsets.py:68`). In a coding
  workspace the coding posture (`focus_toolsets`) may replace the default, because
  `source != 'tool'` (`session_policy.py:465`). [exercised / read]
- **Composites**
  - `all` resolves to everything.
  - A `hermes-*` composite is refused on this surface.
  - `safe_mode` / `ignore_user_config` would strip plugin toolsets (`:704`), but they bypass
    the profile config completely. Neither is usable for Verksted, and the SDK cannot send
    them anyway (k1 §1a). [read + exercised]

**k1 finding that matters here:** `host.request` / `host.requestProfile` rewrite
`session.create` on a canonical (local) gateway. `source` is always replaced by `"gui"`, and
only a fixed set of keys may be sent (`apps/desktop/src/api/canonical-protocol.ts:534-548`,
see `docs/hermes-interfaces.md` §1a). The explicit-list results above are the same for
`gui` and `tool`. Only the default case differs.

## 3 Can the set change after create?

`LocalSessionPolicy.toolsets` is frozen at create. `current_toolsets`
(`session_policy.py:256-264`, v0.22.6 `:252`) returns it, unless the session follows the
profile and was created **without** `toolsets`. [read]

| Path | Effect on an explicit list + `no_mcp` | Effect on the default (no list) | Source |
|---|---|---|---|
| `session.resume` from a connection that does not hold the session yet (CFG-2 refresh) | toolsets kept; the MCP part is recomputed, and with `no_mcp` it stays empty: **cannot grow** | **whole set re-derived** from the live profile | `session_controls.py:663-668`; `gateway/session_policy_refresh.py:53-79` (`_settings`), `:92-129` (`_mcp_servers`), `:245-260` |
| `reload.mcp` | MCP-only refresh, same rule: cannot grow | MCP part can grow | `gateway/session_ancillary.py:306-335` |
| Explicit list naming MCP servers, no `no_mcp` | the named servers act as an allowlist; enabled-but-unnamed servers are not added | — | `session_policy_refresh.py:104-113`, `:128-129` |
| Explicit list without MCP name and without `no_mcp` | **gains every newly enabled server** on the next refresh | — | same |
| `follow_profile_config` / guest model (`FOLLOW_ADOPTED`) | still frozen with an explicit list | re-resolved **every turn** | `session_policy.py:239-244`, `:256-264`; `gateway/session_local.py:316-321` |
| `onboarding` added per turn | only if an onboarding record exists for the session | same | `agent/onboarding_session.py:39-48`; `session_policy.py:1029-1033` |
| `message_agent` re-injection | only in a managed Bot-Mode "Bot Chat" | same | `tools/mcp_tool_agent.py:404-414` |
| `session.mutate` | no toolset op (model, reasoning, workspace, editor_mcp, edit_policy) | same | `gateway/session_mutations.py:20` |
| Desktop client's implicit `session.resume` before `prompt.submit` when the session is not attached on that socket (after a reconnect, or on a different socket) | triggers the CFG-2 refresh above; cannot grow | **can grow, unseen by the plugin** | `apps/desktop/src/api/client.ts:46-55`, `:482-499`; the attached set is cleared on connect (`:337`) and filled after `session.create`/`session.resume` (`:464-466`, `:506`) |

- A committed refresh **evicts the cached agent** (`publish_local_policy`,
  `gateway/session_local.py:387-395`). The next `session.info` is lazy again and reports the
  new policy. [read]
- The agent cache key includes `enabled_toolsets` (`gateway/run_agent_cache.py:102-131`), so
  a moved policy rebuilds the agent. [read]
- `revision` does **not** change on a policy refresh. It cannot serve as a guard. [exercised]
- There is no atomic "submit only if the policy is unchanged". [read]

Drift probe [exercised]: three sessions were created, then the config gained a new MCP server
`latemcp` and `platform_toolsets.cli: [file, web, terminal]`. A `session.resume` from a fresh
connection then gave:

- default session → `[file, latemcp, probe, terminal, web]`;
- `[file, web]` → gained `latemcp`;
- `[file, web, no_mcp]` → unchanged `[file, probe, web]`.

`session.info` *before* the resume still showed the old sets.

**Tool names inside a toolset can still move between turns.** Examples are a plugin
registering into a built-in toolset name, MCP tools appearing on connect, and `check_fn`
availability (`tools/mcp_tool_agent.py:128-151`). This is below the toolset level and outside
the v1 boundary. [read]

## 4 Is visible the same as executable?

Yes, at tool level. [read]

| Path | How it is gated | Source (ad2b705; v0.22.6) |
|---|---|---|
| Agent loop | refuses any tool name not in `agent.valid_tool_names` | `agent/turn_tool_validation.py:84-110`, `agent/turn_tool_round.py:104-128` |
| Where `valid_tool_names` comes from | the same `get_tool_definitions(enabled, disabled)` that builds `agent.tools` | `agent/agent_init.py:1011-1038` |
| Tool Search bridge (`tool_call`) | scoped to the deferrable names of the session's own toolsets | `model_tools.py:714-763`; v0.22.6 `:694` |
| `execute_code` sandbox | receives `enabled_tools=valid_tool_names` and the agent's toolsets | `agent/tool_executor.py:1709-1728` |
| Delegated children | intersect their toolsets with the parent's | `tools/delegate_tool_toolsets.py:81-104` |

`model_tools.handle_function_call` (`:886-980`; v0.22.6 `:866`) has no general name gate of
its own. It relies on the callers above.

The inventory uses the same builder the agent build uses. So the toolsets in
`enabled_toolsets` are exactly the ones whose tools are both offered and executable.

## 5 Can the agent-plugin side compute the same set?

Only through private helpers. The candidates are
`gateway.session_policy._resolve_toolsets` and `hermes_cli.tools_config._get_platform_tools`,
fed with the target profile's config. [read]

Problems:

- The agent plugin runs in the parent profile's process. The plugin-toolset registry is
  process-global, so it reflects the parent's plugins and not necessarily the child
  profile's.
- Config, MCP and plugin state can change between `verksted run` and `session.create`.

Such a computation can only be **advisory**: a preview for the tool reply, or a proposed
list (decision D2 in §8). It is never the check. The authoritative check is on the create
reply.

## 6 The check algorithm

*Read together with §11: the k3 audit corrected steps 2–5 and §7.*

**Inputs**

- `L`: the toolset list resolved from `.verksted.yaml`.
- `C`: the inherited ceiling.
- Both are frozen into the run record at `verksted run` (architecture §2: "Der aufgelöste
  Stand wird beim Start gespeichert").

**Agent plugin, at `verksted run`**

1. Reject a name in `L` if it is:
   - not a string, or a duplicate;
   - `all` or `*`;
   - starts with `hermes-`;
   - `no_mcp` (reserved, see step 2);
   - outside `C` when a ceiling is set.

   Names Hermes does not know are left to Hermes. It refuses them as `invalid_params`, and
   the run becomes `refused` with Hermes' `detail`.
2. Store the request `T = L + ["no_mcp"]` and the expectation `E = set(L)`. MCP servers in a
   list are not supported in v1 (decision M3). Store the default case per decision D1.

**Desktop part, per start**

1. Hold `host.retainProfile(route)` from `session.create` through `prompt.submit`, so that
   both run on one socket and the session stays attached (k1 §1).
2. Call `session.create` with `toolsets: T`. Never send `follow_profile_config`. Never title
   the session "Bot Chat". If the reply is an error with `field: "toolsets"`, the run becomes
   `refused` with that detail.
3. Check the reply's `info`. Refuse unless all of these hold:
   - `info.desktop_protocol == "hermes-gateway-v1"`;
   - `info.lazy === true`;
   - `info.enabled_toolsets` is an array of strings;
   - `set(info.enabled_toolsets) == E`.

   On a mismatch, name both sides:
   - **extra** = effective − `E`: plugin toolsets such as `verksted`, `context_engine`,
     `x_search`, `desktop_ui`, …;
   - **missing** = `E` − effective: for example an `mcp-x` spelling, or a toolset this
     surface dropped.
4. Call `tools.show {session_id}`. It must succeed. If `E` is empty, `total` must be `0`;
   this guards the fail-open inventory. Show the sections in the run view as "Tools in this
   session".
5. Immediately before `prompt.submit`, read `session.info` again and repeat the check in
   step 3, with `lazy === true` still required.
   - If the client's connection changed since the create (the Desktop client would resume
     implicitly), call `session.resume` yourself first and check the `info` it returns.
   - Then submit on that same socket.
6. Before every `verksted send` (a later `prompt.submit`), first `session.resume`, then
   apply the check in step 3 to the `info` in that reply. `lazy` may be `false` by now.

**If a check fails**

- The run becomes `refused`, with the reason and both lists.
- The session is left with no prompt submitted.
- The tool reply tells the main agent what to change. Examples: "list `verksted` in
  `toolsets`, or decline it in the profile's Tools config"; "remove the MCP server from the
  profile, or use a slim profile".

**Why the check holds.** With `T`, §3 shows that no refresh path can add a toolset. The two
reads in steps 3 and 5 therefore bracket the only window that remains. That window is a
`session.resume` by another client between step 5 and the submit, and it can only shrink the
set.

## 7 "Not checkable" → refuse

The prototype refuses with reason `toolsets_not_checkable` when:

- the create, info or resume reply has no `info`, or `enabled_toolsets` is missing or not a
  list of strings;
- `info.desktop_protocol` is not `hermes-gateway-v1`, for example on a legacy remote
  `hermes serve` (k1 §1a);
- `info.lazy` is not `true` at the first check (an agent is already built, so the
  pre-first-prompt state cannot be attested);
- `tools.show` fails, or `E` is empty while `tools.show.total` is not `0`;
- requested names are missing from the effective set (missing ≠ ∅);
- the run has no explicit list while a ceiling is in force (D1).

It refuses with reason `toolsets_exceeded` when effective − `E` ≠ ∅, or in the default case
without a ceiling, when the set grew since the start.

## 8 Maintainer decisions

**D1: the default case** ("Ohne `toolsets` gelten … die Defaults des Zielprofils innerhalb der
festgelegten Obergrenze", §3). The default case is the only one that cannot be pinned with
public interfaces: every refresh re-derives it, including the Desktop client's implicit
resume. Options:

- **(a) Proposed for k3.**
  - Under a ceiling: an explicit list is required, and a run without one is refused as not
    checkable.
  - Without a ceiling: send no `toolsets`, record the effective set at start, show it, and
    refuse a later `send` if the set grew.
- **(b)** The agent plugin proposes `T = sorted(profile_default ∩ C) + ["no_mcp"]` with the
  private helper from §5, then checks it exactly as in §6. Usability is good, but it couples
  Verksted to a private helper. The check stays authoritative.
- **(c)** Send `C` as the list. Public only, but the session gets the whole ceiling rather
  than the profile's defaults, which deviates from the wording of §3.

**M2: plugin toolsets ride in.** On a profile whose Tools config has never been saved, every
enabled plugin toolset (Verksted's own included, which also lets a child call `verksted`
again) is added to every explicit list. The check then refuses. Options:

- document "save the Tools config once" (this writes `known_plugin_toolsets.cli`);
- use slim profiles;
- let `.verksted.yaml` list plugin toolsets explicitly;
- the Hermes extension in §9.

**M3: MCP servers in `toolsets`.** v1 as proposed always sends `no_mcp`, so a list grants no
MCP servers, and MCP access goes through the default case or a slim profile. Allowing named
servers needs an explicit way to tell server names from toolset names in `.verksted.yaml`
(for example its own `mcp:` key). Named servers keep the "cannot grow" property, because
they act as an allowlist (§3).

**Architecture wording.** §3 and §7.1 assume `source: "tool"`. Through the SDK the session is
`gui`, which matters only for the default case (D1). §3's "`[]` verlangt eine tatsächlich
leere Auswahl" holds only through the `no_mcp` translation and M2. These are proposals for
the maintainer. `docs/architecture.md` is unchanged.

## 9 Optional Hermes extension (usability, not a precursor)

**Exact toolsets.** A `session.create` flag such as `toolsets_exact: true`, or exact
semantics for a fixed source. It would:

- skip the plugin, MCP, `context_engine`, `x_search` and recovery fold-ins for names that are
  not listed;
- be honoured by the resume and `reload.mcp` refresh.

It touches `_resolve_toolsets`, `_get_platform_tools` and `_mcp_servers`: roughly 20–40
lines plus tests. It removes M2 and the `no_mcp` translation, and it makes `[]` mean empty.
It must also be added to the Desktop client's create allowlist (`canonical-protocol.ts:534`).

**Exact tool names.** These remain the separate extension already named in §3: a
session-policy allowlist applied in `_load_tools`, `refresh_agent_mcp_tools` and the bridge
scope, so that it limits visibility and execution alike.

## 10 Open points for the Windows run

Nothing below has been exercised on Windows or in the GUI.

1. `host.requestProfile(route, 'session.create' | 'session.info' | 'tools.show' |
   'session.resume', …)` reaches the canonical owner and returns `info.enabled_toolsets`,
   `lazy: true` and `desktop_protocol: "hermes-gateway-v1"`.
2. The maintainer's real profile: which plugin toolsets (`verksted`, `a2a`, …) and MCP
   servers ride into `[file]` + `no_mcp`, and whether the Desktop Toolsets UI save writes
   `known_plugin_toolsets.cli` the way `hermes tools` does.
3. Windows-only availability inside toolsets (`terminal`, `computer_use`): it changes tool
   names, not `enabled_toolsets`.
4. A reconnect between create and submit: confirm that step 5's explicit resume leaves the
   client attached, so it sends no second implicit resume.
5. The user opens the child via "Session öffnen" (a resume from the main socket) before the
   submit: confirm that the set stays as checked.

## Evidence: probes

- **Harness:** `AuthorityConnection(initialize_session_authority(runner, …), …)` with a
  `SessionStore` runner. It is the setup used by
  `tests/gateway/test_canonical_session_inventory.py`; that file was not run itself because
  the dev venv lacks `pytest_asyncio`.
- **Throwaway homes:** `.dev/home-k2/*` (gitignored). They use `plugins.enabled` in
  `config.yaml`; `hermes plugins enable` was not used.
- **Scripts:** in the session scratchpad, not committed. A test written later by
  `verksted-test-writer` should rebuild them.
- **Known harness artefact:** the create-internal resume logged "keeps its stored policy on
  resume: AttributeError" because the runner is a stub. Later resumes from a fresh
  connection refreshed normally.

## 10 Decisions taken for v1 (pending maintainer review)

Taken on 2026-11-03 by the orchestrating agent during the unattended overnight run, so that
k3–k9 could proceed. They are working choices, not the maintainer's decisions; each can be
revisited without changing the store schema.

- **D1 → option (a).** Under a ceiling an explicit list is required; it is the natural result
  of `.verksted.yaml` inheritance (the nearest explicit list is the selection, every ancestor
  list is a ceiling). Without any list and without a ceiling, no `toolsets` are sent, the
  effective set is recorded at start and shown, and a later `send` is refused if it grew.
- **M2 → Verksted's own toolset is tolerated, everything else is checked.** The effective set
  must contain every requested toolset and nothing beyond `E ∪ {verksted's own toolset}`.
  Refusing Verksted's own toolset would make Verksted unusable on the parent's profile, since
  declining it there removes it from the parent too. In exchange the tool refuses every
  start from a session that is itself a Verksted child (recursion guard, v1: depth 1). Other
  plugin toolsets are refused as "exceeded", with the fixes named in the message (save the
  Tools config once, a slim profile, or list the plugin toolset in `.verksted.yaml`).
- **M3 → always `no_mcp` in explicit lists.** A list grants no MCP servers in v1; MCP access
  goes through the default case or a slim profile. Named servers (an own `mcp:` key) are
  left for later.
- **C2 gate accepted.** v1 starts only on a canonical route whose `runtime.describe` lists
  `durable-admission-v1`; anything else is refused before `session.create`, never retried.
  If the maintainer's Windows backend turns out to be a legacy route, this is the first
  thing to revisit.
- **C1 accepted.** Children are identified by the stored session id; `source` is not relied
  on (the SDK sends `gui`).

## 11 Corrections after the k3 audit and fix pass

Found by the read-only audit of k3 and confirmed by the e2e suite (`tests/e2e`, Linux
stand-in against ad2b705). The code follows these, §6/§7 above are the original analysis.

- **Step 2: never send a `title`.** `session.create` resolves a title to an existing session
  before `request_id` is read (`session_controls.py:467-492`); a title derived from the folder
  name reused another folder's session. A per-run unique title is unsafe too (Hermes prefers the
  latest `"<title> #N"`), so Verksted sends none and the Desktop part refuses a titled create.
- **Step 3 with M2:** the comparison is `E ⊆ effective ⊆ E ∪ {verksted}`, not
  `set(effective) == E`.
- **Step 4 / §7, the empty selection:** with Verksted's own toolset enabled, the effective set
  of `[]` is `['verksted']` and `tools.show.total` is 1. The rule is: `tools.show` may list only
  Verksted's own tool, and its `total` must equal what it lists (e2e
  `test_10_an_empty_toolset_list_runs_with_verksted_own_tool_only`). The fail-open inventory
  (`session_local_inventory.py:146-152`, `enabled_toolsets: []` when `_policy_tools` raises)
  has no marker in any reply; the independent `tools.show` count is the only signal, and in the
  default case an empty list after a non-empty first read is not checkable.
- **Step 5, reconnects:** the Desktop part counts connection epochs (`gateway.ready`, the
  gateway state leaving `open`) and re-attaches with an explicit `session.resume` and checks
  again whenever the epoch moved before a submit attempt. Only the primary socket's reconnects
  are seen; one on a secondary socket, or inside an SDK await, is not (open point for Windows).
- **Reconcile at `submit_requested`:** the toolsets are checked before every (re)submit, not
  only when `lazy` is true; our own pending admission is adopted; any other state ends
  `unknown/submit_unconfirmed` without a submit.
- **`runtime.describe` gate:** only `-32601` (method not found) means a legacy route and
  refuses; any other error leaves the run claimable for a later attempt.
