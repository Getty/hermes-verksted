# Hermes interfaces Verksted relies on

Source check for card k1: every Hermes interface `docs/architecture.md` builds on, read at
the revision the design names and at the latest release tag. The Windows side is open — see
[Windows check for the maintainer](#windows-check-for-the-maintainer).

## Revisions compared

| Name | Commit | Date | Notes |
|---|---|---|---|
| **pinned** | `ad2b705b4995a958b0a795e9097be661e2d1ef63` (`v0.22.6-414-gad2b705b49`) | 2026-11-02 20:54 -0500 | The revision `docs/architecture.md` was checked against. Dev venv reports `Hermes Agent v0.22.6+414.gad2b705 (2026.9.24) · stable · local ad2b705b4995`. |
| **tag** | `v0.22.6` = `f0d94882e89a` | 2026-11-02 07:57 -0800 | Latest release tag. |
| — | `origin/main` = `6059d35108` | — | 6 commits past pinned (delegation transcript reservation, bundle `.pyc` permissions); not compared. |

`apps/desktop/package.json` and `pyproject.toml` both say `0.0.0` at both revisions; the real
version is stamped at build time, so the build has to be read off the installed app (checklist
below).

Between tag and pinned, 315 files changed. Of the files this document cites, only these
differ, and none of them changes an interface Verksted uses:

- Desktop: `apps/desktop/electron/main.ts` (window-button offset, an SSH `devServer` flag);
  `apps/desktop/electron/local-gateway.ts` (the dev-server variable is stripped from the
  `gateway ensure` env; the `native_dial` URL moved from `:251` to `:254`);
  `apps/desktop/src/api/canonical-protocol.ts` (`daybreak` config key).
- Gateway: OpenAI "Daybreak" additions in `tui_gateway/prompt_turn.py`,
  `tui_gateway/methods_config*.py`, `tui_gateway/contracts/config_free_tier_control.py`,
  `gateway/session_policy.py`, `gateway/run_turn_runner.py` and
  `hermes_cli/config_defaults.py`; stricter command detection in `tools/approval.py`.
- Tool dispatch: `tools/registry.py`, `model_tools.py`, `agent/tool_executor.py` (check_fn
  verdict caching, tool-search scoping).
- Web server: `hermes_cli/web_server.py`, `web_server_dashboard.py` and the new
  `web_server_boundary.py` (CORS, plugin websocket gate).
- `pyproject.toml` (`nemo-relay` 0.10 → 0.11; the Pillow override is unchanged).

Each one is assessed where the tables below cite it.

**Columns.** *pinned* / *tag*: `present`, `differs` (with what), `missing`. *How*: `read` =
source read at the given line; `exercised` = run in the Linux dev venv
(`.dev/venv`, throwaway `HERMES_HOME`). Nothing here was exercised on Windows or against a
packaged Desktop. File paths are relative to the Hermes checkout.

## 1 Desktop SDK calls

The SDK barrel is `apps/desktop/src/sdk/index.ts`; `host` starts at line 558. All rows: no
difference between tag and pinned (`git diff v0.22.6 ad2b705 -- apps/desktop/src/sdk
apps/desktop/src/contrib apps/desktop/src/api/plugins.ts apps/desktop/src/api/client.ts
apps/desktop/electron/plugin-profile-routes.ts` is empty).

| Call | pinned | tag | Source | Shape and notes | How |
|---|---|---|---|---|---|
| `host.profileRoutes()` | present | present | `sdk/index.ts:1347-1365`; IPC `electron/main.ts:14496-14566`; builder `electron/plugin-profile-routes.ts:261-303` | `Promise<PluginProfileRoute[]>`, `PluginProfileRoute = { connectionId: string; mode: 'local' \| 'remote'; primary?: true; profile: string; targetProfile: string }` (`sdk/profile-request.ts:31-40`, `src/global.d.ts:1085-1094`). **Connection identity is `connectionId` = the registry source id** (`connection-registry.ts:49-75`, persisted in `<userData>/connections.json`, `main.ts:1327`); the local runtime is always `'local'` (`connection-registry.ts:42`). Identity is the pair (`connectionId`, `profile`); `targetProfile` is the backend profile (differs only for SSH `remoteProfile`). `mode: 'remote'` covers registry kinds `remote`, `cloud` and `ssh`. No endpoint, token or host ever crosses to the renderer. Undialed SSH sources appear as a `default` seed route. Throws `Hermes Desktop connection routing unavailable` on shells without the bridge. | read |
| `host.requestProfile(route, method, params?, timeoutMs?, {spawnPriority?})` | present | present | `sdk/index.ts:1383-1389` → `sdk/profile-request.ts:50-106` → `store/gateway.ts` `requestGatewayForAgent` | Gateway JSON-RPC on the route's own backend without changing the active chat. Descriptor must carry non-empty `connectionId`, `profile`, `targetProfile` or it throws. The string overload works only when the registry has exactly one, local, source (`profile-request.ts:91-105`). Default deadline 30 s (`api/client.ts:26`); `spawnPriority: 'foreground'` for user-initiated calls. **Every request passes the client-side protocol layer, which rewrites some params — see §1a.** | read |
| `host.retainProfile(route, {spawnPriority?})` | present | present | `sdk/index.ts:1414-1427` → `store/gateway.ts:1384-1420` | Not in the SDK doc's host listing. Returns `Promise<() => void>`. Each `requestProfile` is its own socket lease; a non-retained secondary socket closes between calls, and "the gateway detached the session on WS disconnect and the next RPC hit 4001 'not in memory'" (`store/gateway.ts:1387-1392`). That reaping is the **legacy sidecar's** (§3, last row); on the canonical local gateway a closed socket loses neither session nor turn, but the new socket must resume before `prompt.submit` — Desktop's client does that itself (§1a). Hold a retain across `session.create` → `prompt.submit` anyway (one socket, one subscription, events keep arriving) and release in `finally`. Primary / shared-primary routes return a no-op release (that socket lives as long as the window); `retainProfileSocket` is a no-op for local routes (`sdk/index.ts:1391-1404`). | read | *Update after k8:* Verksted now holds the retain for the run's whole lifetime (start through tracking), so the child stays attached while the run is followed and a secondary route does not open a socket per poll (k8 decision D-t).
| `ctx.rest(path, opts?)` | present | present | `contrib/plugin.ts:154,396` → `api/plugins.ts:73-87` | `<T>(path, { method?, body?, upload?: {filename, contentType?, bytes}, timeoutMs? }) => Promise<T>`, path relative to `/api/plugins/<id>`; `..` is rejected (`api/plugins.ts:56-64`). **Scoped to the window's ACTIVE `(connectionId, profile)`** through `profileScoped()` / `connectionScoped()` (`api/client.ts:578-585`, `695-697`); there is no route parameter. A plugin cannot address the plugin API of a non-active profile or connection through `ctx.rest`. | read |
| `ctx.socket(path, onMessage)` | present | present | `contrib/plugin.ts:159,397` → `api/plugins.ts:138-260` | WebSocket to `/api/plugins/<id><path>`, JSON frames, returns disposer. Same active scope as `ctx.rest`; follows profile/connection switches (closes the old socket, dials the new scope). Local gateway (`authMode: 'native'`): a one-use URL from main with the grant in a header (`api/plugins.ts:98-117`). Token remotes: `?token=`. **OAuth remotes and the browser-hosted app: no-op** (`api/plugins.ts:194-199`). Stops after 5 refusals in a row. Doc: a secondary profile's plugin host answers 501 for websockets (`desktop-plugin-sdk.md:2353-2355`). | read |
| `ctx.onEvent(type, fn)` / `host.onEvent` | present | present | `contrib/plugin.ts:135,394`; `contrib/events.ts:31-64`; fan-in `store/gateway.ts:992-996`, `app/contrib/wiring.tsx:828-831` | Every frame from every open gateway socket of this window (primary and pooled secondaries) reaches plugin listeners before app dispatch, stamped with `profile` and, for secondaries, `connectionId`. Only sockets that are open deliver. | read |
| `host.openSession(storedId, opts)` | present | present | `sdk/index.ts:827-1160`; options `sdk/index.ts:273-312`; intents `app/open-session.ts:34` | `opts` includes `profile?`, **`route?: PluginProfileRoute`**, `intent?: 'in-place' \| 'main' \| 'stack' \| 'tab' \| 'window'` (default `'in-place'`), `awaitHydration?`, `forceResume?`, `hydrationTimeoutMs?`, `retryHydrationTimeoutOnce?`, `keepAllProfilesScope?` (default true: navigation, no workspace switch), `tabTitle?`. With `route`, the owner hint is recorded and the route's backend is dialed through `openGatewayForAgent(connectionId, profile)` (`index.ts:900-937`); with only `profile`, the hint is built from the **ambient** connection (`index.ts:850-860`, `902-915`). The SDK doc lists only `{ profile?, intent? }` (`desktop-plugin-sdk.md:1431-1433`). Expects the stored (durable) session id. | read |
| `host.state.*`, `host.connections()`, `host.agents()` | present | present | `sdk/index.ts:559-605`, `764-789` | Read-only atoms incl. `connectionId`, `profile`, `focusedSessionOwner`; `connections()` lists registry entries without secrets. | read |
| `host.status()` | present | present | `sdk/index.ts:1343` → `api/config.ts:88-93` | `GET /api/status` on the active scope. | read |

### 1a Client-side rewriting of gateway calls

`host.requestProfile` and `host.request` go through `HermesGateway.request`
(`apps/desktop/src/api/client.ts:390-460`). Which wire contract applies depends on the dial:
a local gateway URL carries `native_dial` (`electron/local-gateway.ts:254`) and is
**canonical** (`client.ts:336`); a remote `hermes serve` is **legacy**.

| Rewrite | Contract | Source | Effect for Verksted |
|---|---|---|---|
| `session.create`: keys outside `request_id, source, cwd, model, provider, toolsets, reasoning, profile, cols, title, hidden, follow_profile_config, service_tier, cwd_explicit` throw `Canonical gateway does not support explicit session options` | canonical | `api/canonical-protocol.ts:534-537` | Only those keys may be sent. |
| `session.create`: **`source` is replaced by `'gui'`**; `request_id` kept (or a random one minted and cached per identical param set) | canonical | `canonical-protocol.ts:540-548` | `source: "tool"` from the design never reaches a local gateway through the SDK. |
| `session.create`: **`request_id` stripped** | legacy | `api/client.ts:67-79` | No create idempotency on a remote backend through the SDK. |
| `prompt.submit` without `submission_id`/`input_id` gets a random `submission_id` | canonical | `canonical-protocol.ts:217` | Always pass the stored `submission_id`. Legacy keeps it as sent (`client.ts:62-66`). |
| `prompt.submit`, `approval.respond`, `clarify.respond`, `session.interrupt`, `prompt.cancel`, `session.rewind` first `session.resume` the session on this socket if not attached | canonical | `client.ts:46-53`, `482-499` | A later socket (new window, reconnect) re-attaches implicitly. |
| `session.interrupt` / `session.steer` / `session.redirect` need an `execution_generation`, taken from the last snapshot/event if absent; else throw | canonical | `canonical-protocol.ts:417-423` | Stop needs a prior resume/create on the same client. |
| `approval.respond` / `clarify.respond` need the prompt to have been delivered to this client at the current generation | canonical | `canonical-protocol.ts:551-560` | Consistent with invariant 9; the plugin has no reason to call them. |

## 2 Desktop plugin: declaration, discovery, enablement, trust, lifecycle

No difference between tag and pinned for any file in this table.

| Aspect | pinned / tag | Source | Finding | How |
|---|---|---|---|---|
| Declaration | present / present | `desktop-plugin-sdk.md:157-216`; `contrib/plugin.ts:115-175` | No manifest file. `plugin.js` is one uncompiled ESM file default-exporting `{ id, name?, defaultEnabled?, capabilities?, register(ctx) }`. Imports allowed: `@hermes/plugin-sdk`, `react`, `react/jsx-runtime` only; no JSX syntax; module evaluation deadline 10 s (`desktop-plugin-sdk.md:2451-2475`). | read |
| Where it is loaded from | present / present | `electron/desktop-plugins-root.ts:1-23`; `electron/fs-ipc.ts:146-163`; doc `desktop-plugin-sdk.md:2130-2172` | One **app-level** root `<HERMES_HOME>/desktop-plugins/<id>/plugin.js`. A unified package `<HERMES_HOME>/plugins/<id>/desktop/plugin.js` (default home and every profile's `plugins/`) is **copied** by Electron into `desktop-plugins/<id>/` with a `.hermes-package.json` marker; a root `plugin.js` wins over `desktop/plugin.js`. No symlinks inside, < 64 MB. Refreshed on Rescan / install / update / restart (mtime rule; a symlinked dev checkout re-syncs on every save). The folder name must equal `id`. **A package installed only on a remote backend contributes no Desktop half** — it shows "unavailable (remote backend)" (`desktop-plugin-sdk.md:2208-2217`). | read |
| Desktop's `HERMES_HOME` on Windows | present / present | `electron/data-paths.mjs:26-66`; `main.ts:1252-1256` | `HERMES_HOME` env → user-registry `HERMES_HOME` → `%LOCALAPPDATA%\hermes` (legacy `%USERPROFILE%\.hermes` only if the default does not exist). | read |
| Enablement | present / present | `contrib/runtime-loader.ts:525-531`, `924-931`; `contrib/plugins-store.ts:35-80`, `116-151` | Desktop half of a unified package (marker present) is **opt-in: off until the user toggles it** in Capabilities → Plugins, or the install dialog / hub agent switch enables it. Decision stored per window origin in `localStorage` key `hermes.desktop.pluginDecisions.v2`. Separately, the Python half (and with it `dashboard/plugin_api.py`) loads only when listed in `plugins.enabled` (§4, §5). Both switches are needed. | read |
| Trust | present / present | `desktop-plugin-sdk.md:2419-2447` | Full renderer authority (whole SDK and `window.hermesDesktop` bridge); loader isolation is error isolation only. Catalog installs: human admission review + import allowlist. Not a sandbox. | read |
| Lifecycle — does it run without an open tab? | present / present | `contrib/runtime-loader.ts:492-531`; `desktop-plugins-root.ts:74-79` | **Yes.** `register(ctx)` runs once at load when enabled, independent of panes, tabs or the focused session; listeners and `ctx.setInterval` timers live until disable/unload. **Each window runs its own instance** (each renderer polls the root every 5 s) — two windows means two concurrent Verksted Desktop parts, as invariant 4 assumes. Nothing runs while the app is closed. | read |
| Timers in hidden windows | present / present | `electron/main.ts:1215-1233` | Idle, minimized or occluded windows fall back to Chromium's default timer throttling; only windows with a turn in flight are unthrottled. A claim lease renewed by `ctx.setInterval` must tolerate long gaps (Chromium's intensive throttling can align chained timers to one minute — inferred, not exercised). | read |
| Approvals / clarify in the native app | present / present | `app/session/hooks/use-message-stream/gateway-event/server-requests.ts:344-422`, `786-803` | The app itself handles `clarify` and `approval` prompts for every session it receives them for (on the canonical gateway they arrive as `clarify.request` / `approval.request` events, §3, which the client turns into the same handlers, `api/client.ts:44`, `:126-140`) (parks the card per session, marks "needs input", native OS notification with Approve/Reject for approvals). The plugin SDK exposes no door to answer server requests — invariant 9 holds by construction on the Desktop side. | read |

## 3 Gateway session control (`hermes-gateway-v1`)

The local Desktop gateway speaks the canonical contract. `gateway/session_controls.py`
(`AuthorityConnection.dispatch`, `:296-377`) answers it; params and results are declared in
`tui_gateway/contracts/canonical.py` and `canonical_sessions.py`. Unknown or missing keys are
refused with `4001 invalid_params` before the handler runs. **Every refusal is error code
`4001`**; the cause is in `error.data.reason` (`invalid_params`, `permission_denied`,
`admission_conflict`, `stale_generation`, …). `5001` means storage is unavailable and `-32601`
an unknown method. The verbs below carry no `profile` key: the socket fixes the profile
(`session_controls.py:96-100`).

None of the files behind this table differs between tag and pinned
(`git diff --quiet v0.22.6 ad2b705` on `tui_gateway/contracts/canonical*.py`,
`gateway/session_{controls,authority,local,local_inventory,events,pending_controls,viewer_close,server_requests,results,finite}.py`,
`hermes_state_{keys,local,runtime}.py`, `tui_gateway/event_replay.py`,
`tui_gateway/session_lifecycle.py`). The differences elsewhere don't change the wire:
`session_policy.py` gained an OpenAI "Daybreak" label set (`:87-90`, which excludes `tool`),
`config_defaults.py` a `daybreak` key, and `tools/approval.py` stricter command detection.

*Exercised* here means: a throwaway `python -m gateway.run` with `HOME`, `HERMES_HOME` and
`TMPDIR` under `.dev/home-k1-gw`, a loopback stub model, tickets from
`ui-tui/scripts/gateway_bootstrap.py`, and a raw WebSocket (`…/api/ws`, subprotocols
`hermes-gateway-v1` + `hermes-gateway-ticket.<t>`). It did **not** go through Desktop's
client, so the §1a rewrites were not in play.

| Call | pinned | tag | Source | Params → result, and behaviour | How |
|---|---|---|---|---|---|
| `runtime.describe` | present | present | contract `canonical.py:334-353`; handler `session_controls.py:541-552` | No params (any param → `invalid_params`). → `{instance_id, profile_id, authority_epoch, capabilities[], session_create: {sources[], parameters[]}, skin?}`. `capabilities` = `durable-admission-v1, event-replay-v1, local-cli-create-v1, acp-editor-policy-v1, acp-session-mcp-v1, clarify-batch-v1`; `sources` = `acp, cli, gui, oneshot, tool, tui`. `profile_id` is the profile's **absolute `HERMES_HOME` path** (`gateway/run_runtime.py:54-55`). Gives no OS, path-space or WSL indication and no toolsets. | exercised |
| `session.create` | present | present | contract `canonical_sessions.py:21-55`, registered `:324-335`; handler `session_controls.py:467-532`; `gateway/session_local.py:276-355`; policy `gateway/session_policy.py:419-495`, toolsets `:652-706` | Params: `request_id?`, `source` ∈ `cli\|tui\|gui\|acp\|tool\|oneshot` (default `cli`), `cwd?`, `toolsets?: string[]`, `model?`, `provider?`, `title?`, `hidden`, `cwd_explicit?`, … (full list in the contract; Desktop's client lets only 14 of them through, §1a). → `CanonicalSnapshot` (row `session.resume`); create ends in a resume, so the creating socket is subscribed. **Session id** = `local-` + sha256 of `[profile_id, principal subject, request_id]` (`hermes_state_keys.py:71-74`, `session_local.py:334`). It is durable in the session DB, so idempotency holds across sockets and gateway restarts, scoped to profile × principal. `request_id`: non-empty string, ≤ 256 chars, minted at random if absent (`session_local.py:325-328`). Same `request_id` + same params → same session (also on a new socket). **But a `title` is resolved first:** a create with a title returns an existing session with that exact title (or the latest "title #N") before `request_id` is read (`session_controls.py:467-492`, `session_local_title.py:21-22`, `hermes_state_titles.py:203-220`; found by the k3 audit, exercised) — Verksted never sends a title. Same `request_id` + changed params → `invalid_params` on `request_id` ("a resumed request_id must carry the same frozen request"). `cwd` must be an absolute path to an existing directory (`session_policy.py:443-445`); a path with a space and `ü` was accepted. Unknown `source` or toolset → `invalid_params`. `toolsets: []` → not empty: MCP servers and enabled plugin toolsets are added (k2, `docs/toolset-check.md` §2); omitted → profile defaults. The session stays lazy (no agent) until its first turn. `source: "tool"` maps to the cli surface and is **hidden from `session.list`** (`hermes_state_sessions.py:241`). Needs scope `session:create`, which exists only with an authenticated identity (`session_controls.py:137-158`). | exercised |
| Effective tools: `session.info` | present | present | handler `session_controls.py:554-559`; `gateway/session_local_inventory.py:44-56`, `:134-206` | Params exactly `{session_id}`. → `{tools: {toolset: [names]}, enabled_toolsets, mcp_servers, tool_count, lazy, cwd, source, launch_request, …}`; the same object is the snapshot's `info`. Works before the first prompt (`lazy: true`). Tools of an MCP server appear only once it is connected (inferred from `get_tool_definitions`). | exercised (MCP part read) |
| `prompt.submit` | present | **differs from contract** (both revisions) | contract `canonical_sessions.py:209-247`; handler `session_controls.py:813-833`; `gateway/session_authority.py:694-779`; `hermes_state_runtime.py:130-177` | Params `session_id`, `text`, `submission_id`, plus `queued`, `attachments`, `finite`, `unattended`, … → `AdmissionStatus {admission_id, ref: {profile_id, session_id}, sequence, status: "queued", outcome: null, authority_epoch, execution_generation: null}`. That is **accepted, not done**. The contract marks `submission_id` optional, but the handler **requires** it (`:822-824`); Desktop's client mints one if missing (§1a). The calling socket must be subscribed to the session (create or resume), else `permission_denied` (`:815-816`). Idempotency is durable (table `session_admissions`, key principal × session × `submission_id` + payload digest): same id + same text → same `admission_id`; same id + other text → `admission_conflict`. Queue cap 32. `finite` answers clarify headless (`gateway/run_turn_runner.py:587-591`); `unattended` auto-approves on cli-surface sessions, and `tool` counts as cli (`gateway/session_finite.py:44-68`). | exercised (`finite`/`unattended` read) |
| `prompt.receipt` | present | present | contract `canonical.py:26-56`; handler `session_controls.py:858-870`; `session_authority.py:781-795`; results `gateway/session_results.py:49-66` | Params `{session_id, admission_id, include_result?}`. **It takes `admission_id`, not `submission_id`.** → `AdmissionStatus` with `status` ∈ `queued\|started\|unknown\|terminal`, `outcome` ∈ `completed\|failed\|interrupted\|cancelled\|steered`, and with `include_result` on a terminal admission `result` (`result.final_response` = answer text) and `usage`. Needs no subscription; works on a fresh socket for the same principal. The result is persisted for every settled admission. | exercised |
| `session.resume` | present | present | contract `canonical_sessions.py:58-75`, snapshot `:137-161`, prompts `:98-109`; handler `session_controls.py:609-704` | Params `session_id`, `defer_history`, `omit_messages`, … → `{session_id, stored_session_id, messages, message_count, running, authority_epoch, replay_epoch, last_sequence, subscription_id, revision, execution_generation, pending[], prompts[], info, open_requests?, …}`. Subscribes the calling socket; this is the re-attach call and the precondition for submit and respond on that socket. On the canonical gateway `session_id == stored_session_id`; compaction and reset rotate only the private transcript target (`session_controls.py:629-642`). | exercised |
| `session.events.since` | present | present | contract `canonical_sessions.py:295-321`; handler `session_controls.py:739-751`; `gateway/session_events.py:99-111`; ring `tui_gateway/event_replay.py:30-36` | Params `{session_id, last_sequence = 0, last_seen = 0, replay_epoch?}` → `{events[{type, session_id, payload, replay_epoch, seq, execution_generation?, admission_id?}], latest_seq, last_sequence, epoch, replay_epoch, truncated, snapshot_required, count, open_requests?}`. A wrong `replay_epoch` → `truncated` + `snapshot_required`. **The replay ring is in memory only** (512 events / 4 MB per session, 64 sessions, 64 MB per process), so it is not durable; an overflow pushes `session.replay_gap` (`session_events.py:66-73`). Gap recovery = resume + receipt. | exercised |
| Pending clarify / approval | present | present (approval detection stricter) | `gateway/session_pending_controls.py:18-63`; turn `run_turn_runner.py:577`, `:713`; respond `session_authority.py:1053-1067`, `session_controls.py:925-935`; timeouts `gateway/local_viewer_events.py:244-259` | **Events, not server requests:** `clarify.request {kind, prompt_id, execution_generation, question, choices, multi_select, timeout_s}`, `approval.request {kind, prompt_id, execution_generation, command, description, choices: [once, deny, session, always], timeout_s}`, then `*.settled {prompt_id, execution_generation}`. The snapshot's `prompts[]` holds the open ones. Delivered to every socket subscribed to the session; with nobody subscribed the prompt waits until its timeout (approval 300 s, `config_defaults.py:1664`; clarify 3600 s, `:219`) and comes back in the next resume's `prompts[]` — also when the creating socket closed before the question was asked. **No `waiting` state exists:** the receipt says `started`, `execution_state` never says waiting (`session_authority.py:331-339`); only `prompts[]` and the `*.request` events show it. `deny` → command not executed (tool result "BLOCKED: Command denied by user…", file still there) — and the turn outcome is still **`completed`**. Approval timeout fails closed (`tools/approval.py`). The creating socket gets `session:respond`/`session:approve` like any viewer. Server requests proper (`srq-` ids, 120 s) carry sudo, secrets, batch clarify (`clarify_batch: true`) and Desktop UI calls (`gateway/session_server_requests.py:19`, `:171-176`). | exercised (clarify answered from a second socket; deny with `approvals.mode: manual`); server requests read | *Correction (k4/k8, exercised):* a Desktop-submitted turn carries `clarify_batch:true` (`apps/desktop/src/api/client.ts:430`), so a Rückfrage arrives as a server request in the snapshot's `open_requests`, and with no viewer attached it does **not** wait: it ends after ~0.3 s as "unanswered" and the turn completes. Approvals still wait `approvals.timeout` and then fail closed as blocked.
| Stop: `session.interrupt` / `prompt.cancel` | present | present | contract `canonical_sessions.py:265-266`, `canonical.py:59-77`; handlers `session_controls.py:872-907`; `session_authority.py:928-955` | `session.interrupt {session_id, execution_generation}` → session handle `{ref, instance_id, authority_epoch, revision, execution_generation, execution_state}`, returned at once and still `running`: **it means "requested"**. A wrong generation → `stale_generation`. Confirmation is the later `message.complete` (`status: "interrupted"`, `outcome: "cancelled"`) and the receipt `terminal` / `interrupted` — the event and the receipt name the outcome differently. `prompt.cancel {session_id, admission_id}` retires a queued admission (`terminal`/`cancelled`) or interrupts a started one. A gateway killed mid-turn leaves the admission `unknown`; it is never replayed and is acknowledged with `prompt.resolve_unknown {session_id, admission_id, execution_generation}`. | exercised (`unknown` path read) | *Update (k8, exercised):* the gateway-kill path was exercised in e2e 15 — after restart the receipt is `unknown`, the session lists our submission as pending, `prompt.cancel` is refused; Verksted marks the run `unknown` and retries nothing.
| Session lifetime vs socket close | present | present | `session_controls.py:937-959`; `session_authority.py:413-440`; `gateway/session_viewer_close.py:180-236`, `:271-280`; `config_defaults.py:2310` | Canonical: closing the creating socket **does not stop the turn**. The receipt stayed `started`, a new socket resumed with `running: true`, and the turn finished. After the last viewer leaves, `sessions.orphan_end_grace_s` (300 s) ends the idle row; the session stays resumable and the next admission reopens it. | exercised (close + resume); grace read |
| Legacy sidecar (remote `hermes serve`) | present | present | `tui_gateway/session_lifecycle.py:880-1040`; `config_defaults.py:1009`; `tui_gateway/session_registry.py:320`; `tui_gateway/methods_session.py:372`, `:472` | A different contract: the runtime session id differs from the stored `session_key`. An orphaned socket is reaped after `dashboard.ws_orphan_reap_grace_s` (20 s): idle sessions are dropped, stale running turns interrupted, approvals dropped. There `4001` means "session not found". No `request_id` idempotency was found in the sidecar's create (partial read). This is the path `host.retainProfile` guards against (§1). | read |

## 4 Agent plugin

These files are identical between tag and pinned: `hermes_cli/plugins.py`,
`plugins_manifest.py`, `plugins_discovery.py`, `plugins_loader.py`, `plugin_session.py`,
`plugin_isolation.py` and `gateway/session_context.py`. Some files in the dispatch path do
differ (`tools/registry.py`, `model_tools.py`, `agent/tool_executor.py`), but only in
check_fn verdict caching and tool-search scoping; handler kwargs, timeouts and the async
bridge are unchanged. Line numbers are pinned; a tag line is given where it moved.

| Item | pinned | tag | Source | Shape and notes | How |
|---|---|---|---|---|---|
| `plugin.yaml` | present | present | `hermes_cli/plugins_manifest.py:32` (known fields), `:361` (`PluginManifest`), `:551` (`parse_manifest_file`) | Fields: `name`, `version`, `description`, `author`, `requires_env`, `provides_tools`, `provides_hooks`/`hooks`, `kind` (`standalone` default \| `backend` \| `exclusive` \| `platform` \| `model-provider`, `:27`), `requires_hermes`, `capabilities` (consent metadata, not a grant), v2: `manifest_version`, `api_version`, `requires_plugins`, `python_dependencies` (never auto-installed), `config_schema`, `license`, `homepage`, `tags`, `emits`, `listens`, `provides_locales`, `config_keys`. Unknown fields only warn. | read |
| Discovery | present | present | `hermes_cli/plugins_discovery.py:190-219`, entry-point group `:29` | Bundled; `<HERMES_HOME>/plugins/<name>/plugin.yaml` (category depth ≤ 2); `./.hermes/plugins` only with `HERMES_ENABLE_PROJECT_PLUGINS`; pip entry points `hermes_agent.plugins`. Collision precedence project > user > bundled. Windows home `%LOCALAPPDATA%\hermes` (`hermes_constants.py:59-62`). | read |
| Enablement | present | present | `plugins_discovery.py:269-334` (`gate_manifest`) | A non-bundled standalone plugin loads only when its key or name is in `plugins.enabled` and not in `plugins.disabled` (per profile `config.yaml`). | read |
| `register(ctx)` | present | present | `hermes_cli/plugins_loader.py:546-633`; `plugins.py:1629`; `plugin_isolation.py:96-106` | Imported as `hermes_plugins.<slug>`, `register(ctx)` called, load deadline `plugins.load_timeout_seconds` (10 s). One `PluginManager` per resolved home, so `register` runs **once per profile** in a multi-profile gateway (docs say "exactly once"). Default isolation `in_process`; `plugins.isolation: host` moves it into a per-profile host process. | exercised (load) |
| `ctx.register_tool(...)` | present | present | `hermes_cli/plugins.py:463-466`; `tools/registry.py:735` (tag `:691`) | `register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)`. `registry.register` knows `execution_timeout`, but `ctx` does not pass it. | read |
| Toolset exposure | present | present | `hermes_cli/tools_config.py:520-527`, `:619-620` | Plugin toolsets are **on by default on every platform** unless default-off or deselected under `known_plugin_toolsets`. | exercised |
| **Real calling session in the handler** (invariant 5) | present | present | Call path: `agent/tool_executor.py:1713-1719` (tag `:1729`) and `agent_runtime_helpers.py:2532` pass `session_id=agent.session_id` → `model_tools.handle_function_call` (`:886`) → `_execute_tool` (`:836`) builds `{task_id, session_id, user_task}` → `registry.dispatch` (`:972`) filters by the handler signature (`_kwargs_accepted_by`, `:1112`). Context vars: `gateway/session_context.py:188` (`get_session_env`), bound per turn in `tui_gateway/server.py:931-952` from `prompt_turn.py:683`. | **Host-set, not model-set.** A handler that declares `session_id` receives the agent's durable session id as a kwarg; a forged `session_id` in the model's arguments stays in `args`. Per-turn context vars readable with `get_session_env(...)`: `HERMES_SESSION_ID` (durable agent session id), `HERMES_SESSION_KEY`, `HERMES_SESSION_PROFILE`, `HERMES_SESSION_SOURCE` (`desktop`), `HERMES_UI_SESSION_ID` (tab). Profile: `get_hermes_home()` is the session's profile home (secondary profiles get a per-turn override, `prompt_turn.py:689`, `hermes_constants.py:194`); `ctx.profile_name` derives from it (`plugins.py:401`); `ctx.session_cwd()` is the session's cwd (`plugin_session.py:33`). `parent_agent` is **not** injected on this path, although the plugin docs list it (only via `ctx.dispatch_tool`). Compaction can rotate the session id (`post_compaction` hook carries `session_id` and `old_session_id`), so a binding keyed on the session id must follow rotation or key on `HERMES_SESSION_KEY` (inferred). | exercised (probe plugin: forged arg stayed in `args`; kwargs and context vars visible in sync and async handlers) |
| Blocking handler / `wait` | present | present | `agent/tool_executor.py:138` (`_DEFAULT_CONCURRENT_TOOL_TIMEOUT_S = 420.0`), `:189`, `:834-940`; `model_tools.py:95-147`; `tools/interrupt.py` | Sync handlers run on a `DaemonThreadPoolExecutor` worker; the agent thread polls every 1 s and heartbeats every 30 s (the 1800 s inactivity timeout does not trip). Async handlers (`is_async=True`) run on the worker's own loop; under an already-running loop they are capped at 300 s. Per-call deadline `timeouts.tools.sequential_call` → `tools.concurrent_batch`, default **420 s**, not raisable by the plugin. `/stop` sets the per-thread interrupt bit: the handler must poll `tools.interrupt.is_interrupted()`; after 3 s grace the call is abandoned with "[Tool execution cancelled …]". While it waits the parent turn is busy. | read (async bridge exercised) |

## 5 Dashboard plugin API (`/api/plugins/<id>`)

`web_server.py` and `web_server_boundary.py` differ between tag and pinned: CORS now trusts
only the exact bound loopback origin, where it used to accept any localhost port.
`web_server_dashboard.py` differs too: `PluginApiWebSocketGate` now applies the Host/Origin/peer
check (close code 4403) to plugin websockets. Neither change touches the mount or the auth
contract Verksted uses. Line numbers are pinned; a tag line is given where it moved.

| Item | pinned | tag | Source | Shape and notes | How |
|---|---|---|---|---|---|
| `dashboard/manifest.json` | present | present | `hermes_cli/web_server_dashboard.py:615` (discover), `:574` (`_dashboard_plugin_entry`), `:504` | Fields: `name` (default dir name), `label`, `description`, `icon`, `version`, `tab{path, position, override, hidden}`, `slots`, `entry` (default `dist/index.js`), `css`, `api` (relative path inside `dashboard/`). The enable gate checks the manifest **`name` against `plugins.enabled`** (`:848`), so it must equal the agent plugin key. | read |
| `plugin_api.py` contract | present | present | `web_server_dashboard.py:1181` (tag `:1173`), `:1239` | Imported as `hermes_dashboard_plugin_<name>`; must export `router` (FastAPI `APIRouter`). | exercised |
| Mount path | present | present | `web_server_dashboard.py:1245` (tag `:1237`); `web_server.py:858` (tag `:849`) | `prefix=f"/api/plugins/{name}"`, mounted at web-server import, hot-mounted later. | exercised (`/api/plugins/vkprobe/who` mounted) |
| Serving process | present | present | `gateway/run_api.py:1-13`, `:49-110`; `web_server_plugin_profiles.py:1-10`, `:53`, `:61`, `:234`, `:266`; `web_server.py:413-434` | For a local Desktop, the **local gateway process** serves `/api/ws` and mounts the dashboard app lazily. That is the same process that runs Desktop turns (`dashboard.turn_isolation` defaults to false, `config_defaults.py:992`). **Launch profile**: `plugin_api.py` is imported in the gateway process, as a module separate from the agent plugin. **Secondary profile**: its own copy runs in that profile's plugin-host child process, with that profile's home, request/response only, 60 s timeout, websockets refused. | read |
| Host authentication | present | present (CORS tightened) | `web_server.py:457` (`auth_middleware`), `:480` (`_token_auth_seam`); `hermes_cli/dashboard_auth/native_http.py:18-42`, `:45-81`, `:180-211`, `:287-312`; `runtime_bootstrap.py:37-56` | Local Desktop: Electron main attaches a one-use `X-Hermes-Gateway-Ticket` (purpose `native-http`, 30 s TTL) to each request; the renderer never sees it. It is accepted only from a loopback peer with no `Origin` header. Otherwise `X-Hermes-Session-Token` / Bearer, or the cookie gate on exposed servers. On `/api/plugins` paths `?profile=` must match the grant's home, else **403 `profile_scope_mismatch`**. | exercised (none → 401, session token → 200, bogus ticket → 401, unknown plugin → 404); native grant read only |
| Caller identity in a handler | present | present | `runtime_bootstrap.py:37-56` | `request.state.native_http_principal = {instance_id, profile_id (home path), subject (OS peer), purpose, capabilities={'http:owner'}, scope}`; `get_hermes_home()` is bound to the grant's home. **No window, tab or connection identity** — a claim owner id must be minted by the Desktop part. | read |
| Reachable from the Desktop part | present | present | §1 `ctx.rest` | Yes, through `ctx.rest` — but only on the window's **active** connection and profile. | read |
| Gateway JSON-RPC method from a plugin | **missing** | **missing** | `tui_gateway/server.py:775` | No plugin API registers a JSON-RPC method; `register_method` is core-internal and each name needs a contract test. `host.requestProfile(route, 'verksted.*')` is therefore not an option. | read |
| Shared state between agent plugin and `plugin_api.py` | present | present | `plugins/plugin_storage.py:27`, `:36` | They are not guaranteed to share a process (secondary profile, `plugins.isolation: host`, turn isolation). `plugins.plugin_storage.plugin_db(name)` opens `<home>/plugin-data/<name>/data.db` (WAL, `check_same_thread=False`). | read |

## 6 Signal from agent plugin to Desktop part

| Path | pinned | tag | Source | Finding | How |
|---|---|---|---|---|---|
| `broadcast_plugin_event(plugin_id, event, payload=None) -> None` | present | present | `hermes_cli/plugin_events.py:63`; `tui_gateway/server.py:632` (`_broadcast_global_event`); doc `desktop-plugin-sdk.md:2277-2321` | Wire frame `{"jsonrpc":"2.0","method":"event","params":{"type":"plugin.<id>.<event>","session_id":"","payload":{…}}}` to **every** live transport of that process — not filtered by profile, so every Desktop window on that gateway gets it. No client → logged no-op. Invalid id or event → `ValueError`. Works from a tool handler in the Desktop-attached gateway, including a secondary profile's turn (same process, read). Under turn isolation it is relayed from the compute host (`compute_host.py:562`, `compute_host_bridge.py:145`). From a **secondary profile's `plugin_api.py`** (plugin host) no relay was found, so the call is presumably dropped (inferred). Desktop side: `ctx.onEvent('plugin.verksted.<event>', fn)` (§1). | exercised (frame shape, no-client no-op); reach read |
| `ctx.emit` | present | present | `hermes_cli/plugins.py:986`; `plugins_dispatch.py:528` | In-process plugin-to-plugin bus only; never reaches the Desktop. | read |
| `ctx.socket('/events')` | present | present | §1 | Plugin-scoped push from `plugin_api.py`, but active scope only, no-op on OAuth remotes, refused on a secondary profile's plugin host. | read |

**Answer:** a public "work available" signal exists — `broadcast_plugin_event` from the tool
handler. It is a doorbell only: no delivery guarantee, nothing when no window is connected,
fanned out to every window. Polling the plugin API on connect, on reconnect and on a timer
stays mandatory.

## 7 Observed: dev-env lock conflict at pinned

Building the dev venv from `uv export --frozen --extra mcp` at pinned and installing with pip
failed on Pillow, so `browser-harness` was left out (`.dev/hermes-req-nobh.txt` is
`.dev/hermes-req.txt` minus `browser-harness==0.1.13`). Cause, read in source:
`browser-harness 0.1.13` (a core dependency, `pyproject.toml:141`) pins `pillow==12.3.0`
exactly; Hermes forces `Pillow==13.0.0` through `[tool.uv] override-dependencies`
(`pyproject.toml:650-661`) for the 13.0.0 security advisories. uv honours the override, a
plain pip install of the exported requirements does not. Hermes' own PM check accepts the
mismatch because `uv.lock` records the override (`pm/pip_check.py`, per the comment). Same
override at the tag. The failure itself was observed while the venv was built; I did not
re-run it.

## Consequences for Verksted

These are proposals only. Changing a decision in `docs/architecture.md` is the maintainer's
call. "arch §N" refers to a section of `docs/architecture.md`; a plain "§N" refers to this
document. "k3" is the step-1 prototype.

### Gateway

**C1 — `source: "tool"` does not survive the SDK** (arch §4 step 3). The gateway accepts
`tool` (§3). Desktop's canonical adapter, however, replaces `source` with `gui` (§1a). So a
session Verksted creates through `host.requestProfile` is a `gui` session, and it is listed in
Desktop's sessions, not hidden. A `tool` session would be hidden from those lists (§3), and
"Session öffnen" by stored id works either way. Proposal: drop `source: "tool"` from arch §4
step 3 and identify children by the stored session id alone.

**C2 — Create idempotency is real, but only on the canonical gateway** (arch §4 step 3; arch §5
"Verlorene Startbestätigungen"). Repeating a create with the stored `request_id` and
*identical* params returns the same session, across sockets and gateway restarts. The scope is
profile × principal (§3). That makes a lost create reply safe to retry, under three
conditions:

- Store the exact params object with the run before the first call.
- Never re-derive the params.
- Retry only under the same principal. A different principal gets a different session id,
  which is exactly why the design forbids blind retries after an identity change.

On a legacy route the client strips `request_id` (§1a), and the sidecar does not dedupe.
Proposal for v1: require a canonical route and refuse otherwise. The gate: `runtime.describe`
must answer — it is canonical-only, so the sidecar should return `-32601` (read, not
exercised) — and must list
`durable-admission-v1`. Record `instance_id` and `profile_id` from it with the run. A different
`instance_id` on the same route later means a different backend, and only reconciliation is
allowed.

**C3 — Submit and receipt use different ids** (arch §4 step 3, step 4). `prompt.submit` needs
`submission_id`; the handler refuses a call without it. Same id plus same text gives the
same admission, durably (§3). A lost submit reply is therefore retried with the stored
`submission_id` and the stored text. `prompt.receipt` takes the **`admission_id`** from the
submit reply, not the `submission_id`. The store needs both. Run create → `session.info` →
submit on one retained route, because submit needs a subscription on the calling socket.

**C4 — The effective tool set can be checked before the first prompt** (arch §3; arch §7 step 1
"andernfalls wird die dafür nötige kleine Hermes-Erweiterung zum expliziten Vorlauf"). Call
`session.info` on the lazy session. It returns `tools`, `enabled_toolsets`, `tool_count` and
`mcp_servers` (§3, exercised). For the toolset level the arch §7 "Vorlauf" is therefore not
needed. One gap: an MCP server's tools show up only once it is connected, so on a lazy session
count servers, not tools. Refuse a start if `mcp_servers` names a server outside the allowed
set. Card k2 owns the details.

**C5 — Never send `finite`, `unattended` or `yolo`** (arch §5). `finite` answers clarify
headless. `unattended` auto-approves on cli-surface sessions, and `tool` is one of them (§3).
Each of them would bypass the native Rückfrage/Freigabe.

**C6 — The Desktop part is a full viewer** (arch §4 "Berechtigungsantworten gehören nicht zu
diesem Modell-Tool"; arch §5). The socket that created the session holds
`session:respond`/`session:approve` (§3). The SDK offers no server-request handler, but
`host.requestProfile(route, 'approval.respond', …)` is callable. The rule therefore belongs in
code: `desktop/plugin.js` never sends `approval.respond`, `clarify.respond` or `clarify.lock`,
with a test that greps for it.

**C7 — "Waiting" is derived, not reported** (arch §5 "Sobald die Kindsession auf Eingabe
wartet"). No gateway state says waiting; the receipt says `started`. The source is the
snapshot's `prompts[]` (`kind: clarify | approval`) plus the `clarify.request` /
`approval.request` / `*.settled` events (§3). Map `clarify` → "Rückfrage" and `approval` →
"Freigabe erforderlich". The approval timeout is 300 s and fails closed. Show it, because a
user who comes back later finds the request denied.

**C8 — A denied approval still ends `completed`** (arch §7 acceptance "Verweigerte
Freigabe"). The denial shows only in the tool result text (§3). Verksted returns the child's
`result.final_response` and its own `outcome` as given. The acceptance test asserts that the
action did not happen (file unchanged), not a failed outcome.

**C9 — Stop is asynchronous** (arch §5 "Ein Stop wird erst als abgeschlossen gemeldet, wenn
Hermes ihn bestätigt"). Proposal for `cancel`: `prompt.cancel {session_id, admission_id}`. It
covers both queued and started admissions and needs no `execution_generation`. Report
"stopped" only when the receipt says `terminal` with `interrupted` or `cancelled`. Until then
report "stop requested".

**C10 — Reconcile from receipts, not from events** (arch §4 step 4; arch §5 GUI restart). The
event replay ring is in memory and bounded (§3), so events are hints. Reconciliation is always
`session.resume` (for `prompts[]`) plus `prompt.receipt {include_result: true}`. A gateway
killed mid-turn leaves the admission `unknown`, which maps to Verksted `unknown`.
`prompt.resolve_unknown` unblocks the session's queue without re-running anything. Proposal:
call it only on an explicit user action, never automatically.

**C11 — "Kindsession läuft unabhängig vom geöffneten Tab" holds — on the canonical gateway**
(arch §1). Exercised: the turn survives the creating socket closing. On a legacy sidecar it does
not hold, because of the 20 s reap (§3), which is one more reason for C2's gate. After 300 s
without a viewer, an idle session row is ended but stays resumable. A status read then sees
an ended session that is not lost.

### Desktop part, plugin API, agent plugin

**C12 — State store and the active-scope limit of `ctx.rest`** (arch §4 "Die Zustandsablage
bleibt beim ursprünglichen Elternprofil"; arch §4 step 2). The Desktop part reaches only the
plugin API of the window's active connection and profile (§1, §5). A run stored under profile A
is invisible to a window that focuses profile B, and the native grant rejects `?profile=A` with
403. Options:

- (a) Keep the store per parent profile, and accept that only a window focused on that
  profile claims its runs.
- (b) Enable Verksted in every allowed profile, and have each profile's `plugin_api.py` serve
  only its own runs. Whichever profile is active claims its own. A profile no window focuses
  still gets nothing.
- (c) Use one store under the launch home, shared across profiles. This breaks "Ablage beim
  Elternprofil" and gives a secondary profile's plugin host a foreign home.

Proposal: (a) for v1. A run nobody can claim shows as "waiting for a window on profile X". k3
is single-profile and unaffected.

**C13 — Store backend** (arch §4 step 2, "beispielsweise SQLite"). The agent plugin and
`plugin_api.py` are different modules and not always in one process (§5). Both halves use
`plugins.plugin_storage.plugin_db("verksted")` (`<home>/plugin-data/verksted/data.db`, WAL),
with `BEGIN IMMEDIATE` around claim and state transitions. Proposal: make SQLite a fixed
decision rather than an example.

**C14 — Claim owner identity** (arch §4 step 2; arch §4 step 3 "authentifizierte
Aufruferidentität"). `plugin_api.py` sees `native_http_principal` (instance, profile home, OS
subject) but no window, tab or connection (§5). The Desktop part mints a random owner id per
window instance at `register()` and sends it with every claim call. The backend stores it with
the principal's `instance_id` and `profile_id`. An "identity change" is then a different
principal or a different owner id on a later call, or a different `instance_id` from
`runtime.describe` (C2).

**C15 — Doorbell** (arch §4 step 2). Call `broadcast_plugin_event("verksted", "work", {})`
after the run row is committed, with no run data in the payload. Every window on that gateway
receives it (§6). The Desktop part answers by polling its plugin API. It also polls on
`register()`, on reconnect and on a timer, because the bell is not delivered when no window is
connected.

**C16 — Lease timing in hidden windows** (arch §4 step 2). Renewal runs in `ctx.setInterval`,
which Chromium throttles in idle, minimized or occluded windows (§2). Proposal: a lease well
above one minute (3 min lease, renewed every 30 s), with a missed renewal treated as
"expired → reconcile only", as the design already says.

**C17 — Parent binding** (arch §4 step 1, invariant 5). Declare `session_id` in the handler
signature. Read `HERMES_SESSION_KEY`, `HERMES_SESSION_PROFILE` and `HERMES_SESSION_SOURCE`
through `get_session_env`, and take the profile from `ctx.profile_name`. Never accept parent ids
from tool arguments: a model-set `session_id` stays in `args` and must be ignored (§4,
exercised). Compaction can rotate the agent's session id, so either bind on the durable session
key or follow `post_compaction(old_session_id → session_id)` (inferred, not exercised).

**C18 — Bound on `wait`** (arch §4 step 5). The per-call deadline is 420 s and the plugin
cannot raise it; an async handler under a running loop is cut at 300 s (§4). Proposal:

- Cap `wait`, and the waiting part of `run`, at about 240 s.
- After that, return `still_running` together with the handle.
- Poll `tools.interrupt.is_interrupted()` at least once a second, so a `/stop` on the parent
  returns within the 3 s grace.

**C19 — Recursion** (arch §3; arch §4 last paragraph, "Starts in einem bereits in der
Aufrufkette aktiven Ordner werden abgelehnt"). Plugin toolsets are on by default on every
platform (§4), so a child created with default toolsets gets `verksted` too. Two proposals:

- Always pass an explicit `toolsets` list without `verksted`, and check the result with C4.
- Refuse `run` when the calling session is itself a Verksted child, i.e. its session key is
  stored as a child. This check is separate from the folder check.

**C20 — Packaging** (arch §7 step 3). Both switches must be set:

- the Python half through `plugins.enabled` in each profile that serves runs;
- the Desktop half through Capabilities → Plugins (opt-in, per window origin).

`dashboard/manifest.json` `name` must equal the plugin key `verksted`. A package installed
only on a remote or WSL backend contributes no Desktop half. To be confirmed in the Windows
check: for a WSL backend, the package must also be installed in the Windows-side
`HERMES_HOME`.

**C21 — `openSession` call** (arch §5). Use
`host.openSession(storedId, { route, intent: 'tab' })` with the stored route descriptor, not
the `{ profile, intent }` that arch §5 names. With `profile` only, the SDK takes the connection
from the active window — exactly the "same profile name on another connection" case arch §5
forbids (§1).

**C22 — Paths on a WSL backend** (arch §6). The tool handler runs on the backend, so it
validates paths in the backend's own path space. It should record `sys.platform` with the run,
since `runtime.describe` has no OS field. The gateway translates a Windows `cwd` into the WSL
space itself (`hermes_constants.py:1255-1266`, applied in
`tui_gateway/methods_session.py:1105-1113`). Proposal: the Desktop part sends the backend
path as the handler stored it, never a path it derived itself.

### What k3 does differently from a literal reading of arch §4

1. **Gate.** Check `runtime.describe` for `durable-admission-v1` and refuse otherwise (C2).
2. **One retained route**, released in `finally`, carrying:
   1. `runtime.describe`;
   2. `session.create {cwd, request_id, toolsets}`, with an explicit list without `verksted`
      and no reliance on `source` (C1, C19);
   3. `session.info`, compared against the allowed set (C4);
   4. `prompt.submit {session_id, text, submission_id}` (C3).
3. **Persist before each call**: `request_id` plus the exact create params, and
   `submission_id` plus the exact text. Persist `admission_id` after the submit reply. After a
   lost reply, repeat the identical call (C2, C3).
4. **Status and result.** Status comes from `prompt.receipt {admission_id}` plus the
   snapshot's `prompts[]`; the result from `include_result` → `result.final_response`
   (C7, C10).
5. **"Session öffnen"** is `host.openSession(sessionId, { route, intent: 'tab' })` (C21).
6. **Never send** `finite`, `unattended`, `yolo`, `approval.respond` or `clarify.respond`
   (C5, C6).
7. **Agent side.** `wait` stays ≤ 240 s and polls `is_interrupted()`. The parent binding comes
   from host kwargs and context vars. The store is `plugin_db`, and a doorbell plus polling
   wakes the Desktop part (C13, C15, C17, C18).
8. **Denial acceptance test.** Assert the file is untouched and expect the outcome
   `completed` (C8).

## Windows check for the maintainer

Goal: name the Desktop and backend build actually installed, say whether the backend is
native or WSL, and confirm on that build the calls the tables above pin from source. Steps
1–3 only read. Steps 4–5 load a throwaway probe plugin; the house rules say installing into
the maintainer's own Hermes home needs the go-ahead, so use the isolated home in step 4a,
or the real home only with that go-ahead (4b).

### 1 Record the Desktop build

1. Desktop → **Settings → About**: copy the version block — app version, *Client*,
   *Backend*, commit (14 chars), branch/channel, *Distribution*, *Runtime*
   (embedded/external), install id + root (`components/version-details.tsx:98-150`).
2. PowerShell, which package it is:
   ```powershell
   Get-AppxPackage *hermes* | Select-Object Name, Version, PackageFullName   # MSIX / Store
   Get-Command hermes -All | Select-Object Source                           # which hermes.exe wins
   ```
3. The baked install stamp from the Desktop log (`<HERMES_HOME>\logs\desktop.log`,
   `electron/main.ts:1236-1240`, `:1350`):
   ```powershell
   Select-String -Path "$env:LOCALAPPDATA\hermes\logs\desktop.log" -Pattern 'install stamp' | Select-Object -Last 1
   ```
   (If `HERMES_HOME` is set, use that root instead of `%LOCALAPPDATA%\hermes`; resolution
   order in `electron/data-paths.mjs:41-66`.)

### 2 Record the backend build and where it runs

1. Desktop → **Settings → Gateways → Registered gateways**: list every entry with its kind
   (`local`, `remote`, `ssh`, `cloud`) and which one is primary. Native Windows backend =
   the `local` entry is active. A WSL backend appears as a `remote` (`hermes serve` inside
   WSL) or `ssh` entry (`electron/wsl-path-bridge.ts:4-9`).
2. Native backend, in PowerShell. Every `hermes` command creates its home if it is missing
   (a bare `hermes --version` created `~/.hermes` on the Linux box), so run these only where
   the home already exists:
   ```powershell
   hermes --version
   hermes gateway status
   hermes gateway list
   hermes dump
   ```
3. WSL backend, inside the distro: `hermes --version`, `hermes gateway status`, `uname -r`,
   and `wsl -l -v` from PowerShell.
4. Compare the backend commit with `ad2b705` / `v0.22.6`. If it is neither, record the
   commit or tag. The tables above hold only for these two revisions; on a newer build,
   re-run this document's checks against that tag first (on Linux).

### 3 Plugin roots and enablement on this machine

```powershell
hermes plugins list
Get-ChildItem "$env:LOCALAPPDATA\hermes\desktop-plugins" -Force
Get-ChildItem "$env:LOCALAPPDATA\hermes\plugins" -Force
```

### 4 Load a throwaway probe (needs 4a or the go-ahead for 4b)

The probe is a disk plugin `desktop-plugins/verksted-probe/plugin.js` whose `register(ctx)`
exposes `globalThis.verkstedProbe = { host, ctx }` (import `host` from `@hermes/plugin-sdk`),
plus an agent plugin `plugins/verksted-probe/` that has one tool and a
`dashboard/plugin_api.py` with `GET /who` returning `request.state.native_http_principal`.
The Windows session writes it; it is not product code and is deleted afterwards.

- **4a isolated:** quit the Desktop, then launch it with
  `HERMES_HOME=C:\verksted-k1\home` and `HERMES_DESKTOP_USER_DATA_DIR=C:\verksted-k1\desktop`
  set (`electron/data-paths.mjs:43-49`; documented for dev slots in
  `website/docs/developer-guide/worktree-ui-dev.md:93,127`). The probe's work folder is
  `C:\verksted k1\Ärger\work` (space and umlaut), so the run also covers path handling.
  Unverified for
  the packaged MSIX app: an app started from the Start menu does not inherit a PowerShell
  environment. If it ignores the variables, stop and use 4b.
- **4b real home, with the go-ahead:** drop the probe into `%LOCALAPPDATA%\hermes`, add
  `verksted-probe` to `plugins.enabled` in `config.yaml` (do **not** run
  `hermes plugins enable` — it re-resolves the whole environment), then
  `hermes gateway restart --detach` (without an installed service a plain `restart` runs
  the gateway in that terminal, `hermes_cli/subcommands/gateway.py:146-155`; read, not
  exercised).

Then Capabilities → Plugins → enable *verksted-probe* (the desktop half is opt-in), open
DevTools with **Ctrl+Shift+I** (enabled in packaged builds, `electron/main.ts:6449-6460`).

### 5 Confirm the calls (DevTools console, probe loaded)

Record each result (or the error text) in this document under a "Windows build `<commit>`"
heading.

```js
const { host, ctx } = verkstedProbe
const routes = await host.profileRoutes()            // §1: shape, connectionId values
const route = routes.find(r => r.primary) ?? routes[0]
const rt = await host.requestProfile(route, 'runtime.describe', {})   // §3: capabilities, profile_id form
const release = await host.retainProfile(route)      // §1
try {
  const cwd = 'C:\\verksted k1\\Ärger\\work'       // must exist on the backend
  const params = { source: 'tool', cwd, request_id: 'k1-probe-1', toolsets: ['file'] }
  const created = await host.requestProfile(route, 'session.create', params)
  const again = await host.requestProfile(route, 'session.create', params)       // same session_id?
  const sid = created.session_id
  const info = await host.requestProfile(route, 'session.info', { session_id: sid }) // §3: tools, source, cwd
  const sub = { session_id: sid, submission_id: 'k1-probe-1', text: 'Say OK.' }
  const adm = await host.requestProfile(route, 'prompt.submit', sub)
  const adm2 = await host.requestProfile(route, 'prompt.submit', sub)           // same admission_id?
  // after a few seconds:
  await host.requestProfile(route, 'prompt.receipt', { session_id: sid, admission_id: adm.admission_id, include_result: true })
  await host.requestProfile(route, 'session.events.since', { session_id: sid, last_sequence: 0 })
  Object.assign(verkstedProbe, { rt, created, again, info, adm, adm2 })
} finally { release() }
await ctx.rest('/who')                               // §5: principal, profile_id
await host.openSession(verkstedProbe.created.session_id, { route, intent: 'tab' })  // §1: right connection?
```

Then, in the opened tab, ask the child something that needs a shell command and deny the
approval (the Linux probe needed `approvals.mode: manual` in the profile's `config.yaml` for
the prompt to appear); confirm the receipt ends `terminal` / `completed` and the command did not run
(§3, C8). Then `prompt.cancel` on a running admission and watch the receipt reach
`terminal` (§3, C9).

Check by eye:

- `info.source` (expect `gui` on a local gateway: §1a rewrites `tool`), `info.tools`
  (expect only `file`), and whether `again` and `adm2` repeated the first ids;
- `rt.capabilities` contains `durable-admission-v1`, and the form of `rt.profile_id`
  (`C:\…` on a native backend, `/home/…` on WSL — inferred);
- the session's cwd is the Windows path on a native backend, or the translated
  `/mnt/c/...` path on a WSL backend (`hermes_constants.py:1255-1266`);
- no console window appeared at any step;
- a `broadcast_plugin_event('verksted-probe', 'ping', {})` from the probe tool, run in a
  parent chat, arrives at `ctx.onEvent('plugin.verksted-probe.ping', …)`;
- **4b only:** remove the probe and its `plugins.enabled` entry afterwards, then
  `hermes gateway restart --detach` (read, not exercised).

### 6 Report back

Note on k1: Desktop version + install stamp, backend version + commit, native or WSL,
and per call `present` / `differs` / `missing` with the error text. Then unblock k1.
