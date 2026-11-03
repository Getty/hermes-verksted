// Verksted Desktop part: claims runs the verksted tool stored and starts each one as a regular
// Hermes session through the public Desktop SDK, then follows it until Hermes reports the end. It
// also carries out follow-up turns (`verksted send`) and stop requests (`verksted cancel`).
//
// Calls checked against Hermes ad2b705 (docs/hermes-interfaces.md, docs/toolset-check.md):
// host.profileRoutes / requestProfile / retainProfile / openSession, ctx.rest, ctx.onEvent, and
// the canonical gateway verbs listed in ALLOWED_CALLS. No JSX, no build: the file is loaded as is.

import * as sdk from '@hermes/plugin-sdk'
import * as React from 'react'

export const PLUGIN_ID = 'verksted'
export const OWN_TOOLSET = 'verksted'
export const OWN_TOOLS = Object.freeze(['verksted']) // the tools the agent part registers in OWN_TOOLSET
export const CANONICAL_PROTOCOL = 'hermes-gateway-v1'
export const REQUIRED_CAPABILITY = 'durable-admission-v1'
export const LEASE_S = 180          // C16: well above a minute; renewed every 30 s
export const RENEW_MS = 30000
export const POLL_MS = 15000
export const TRACK_MS = 10000
export const RPC_TIMEOUT_MS = 30000
export const REPEATS = 3            // identical repeats after a lost reply, then the run waits for a later claim

// The only gateway calls this part makes, and the only parameters each may carry. Anything else is
// refused before it leaves the window: questions and approvals of a child session are answered by
// the user in that session, never by this part, and no headless or auto-approving flag is ever sent.
// No `title` on session.create: Hermes resolves a title to an existing session before it looks at
// request_id (gateway/session_controls.py:467-492), so only request_id may pick the session.
// prompt.cancel is scoped to Verksted's own admission (gateway/session_controls.py:872-894): it
// cannot stop a turn the user typed into the child. It is sent only for a stop the tool stored.
export const ALLOWED_CALLS = Object.freeze({
  'runtime.describe': [],
  'session.create': ['cwd', 'cwd_explicit', 'request_id', 'toolsets'],
  'session.info': ['session_id'],
  'session.resume': ['session_id', 'omit_messages'],
  'tools.show': ['session_id'],
  'prompt.submit': ['session_id', 'text', 'submission_id'],
  'prompt.receipt': ['session_id', 'admission_id', 'include_result'],
  'prompt.cancel': ['session_id', 'admission_id']
})

export const LABELS = Object.freeze({
  waiting_for_gui: 'wartet auf Hermes Desktop',
  create_requested: 'wird gestartet',
  session_created: 'wird gestartet',
  toolsets_checked: 'wird gestartet',
  submit_requested: 'wird gestartet',
  running: 'läuft',
  finished: 'fertig',
  refused: 'abgelehnt',
  unknown: 'unbekannt',
  send_waiting: 'Folgeauftrag wartet auf Hermes Desktop',
  cancelled: 'abgebrochen'
})
export const STOPPING_LABEL = 'wird gestoppt'
export const RESULT_BLOCK_LABELS = Object.freeze({
  missing: 'kein Ergebnisblock', unparsable: 'Ergebnisblock unlesbar'
})
export const WAITING_LABELS = Object.freeze({ clarify: 'Rückfrage', approval: 'Freigabe erforderlich' })
// The SDK host members this part calls that an older Hermes Desktop lacks (ad2b705: openSession
// came 2026-08-12, profileRoutes and requestProfile 2026-08-14, retainProfile 2026-08-24). Without
// them this window claims nothing and says so, rather than failing mid-start with a TypeError.
export const HOST_MEMBERS = Object.freeze(['profileRoutes', 'requestProfile', 'retainProfile', 'openSession'])
export const CHIP_NOTES = Object.freeze({
  agentOff: 'Agent-Teil nicht aktiviert', tooOld: 'Hermes Desktop zu alt für Verksted'
})
const CHIP_NOTE_MAX = 48
const STARTED = ['create_requested', 'session_created', 'toolsets_checked', 'submit_requested', 'running']
const FINAL = ['finished', 'refused', 'unknown', 'cancelled']
const CLAIM_GONE = ['stale_generation', 'lease_expired', 'final', 'not_found']
// A step refused because the run moved on without this window (a newer claim, a stop the tool
// stored before the submit): the window lets go quietly.
const MOVED_ON = [...CLAIM_GONE, 'bad_transition']
// store.py IDENTITY_FIELDS: `subject` is the host-authenticated principal of the plugin API requests.
const IDENTITY_KEYS = ['connection_id', 'route_profile', 'target_profile', 'profile_id', 'subject']

export class GuardError extends Error {
  constructor(message) {
    super(message)
    this.name = 'VerkstedGuardError'
    this.guard = true
  }
}

/** A start step that cannot finish now (no reply); the run keeps its state for a later claim. */
export class Deferred extends Error {
  constructor(message) {
    super(message)
    this.name = 'VerkstedDeferred'
  }
}

/** The plugin API refused a step; `code` says why (a lost claim, an identity mismatch, …). */
export class StepRefused extends Error {
  constructor(answer) {
    super((answer && answer.message) || 'step refused')
    this.name = 'VerkstedStepRefused'
    this.code = (answer && answer.code) || 'refused'
  }
}

export function guardCall(method, params) {
  const allowed = ALLOWED_CALLS[method]
  if (!allowed) throw new GuardError(`Verksted never calls ${method}`)
  const extra = Object.keys(params || {}).filter(key => !allowed.includes(key))
  if (extra.length) throw new GuardError(`${method}: parameters not allowed: ${extra.join(', ')}`)
}

export const isRpcError = err => !!err && typeof err.code === 'number'

/** The host members of HOST_MEMBERS this Desktop does not offer. */
export const missingHostMembers = host => HOST_MEMBERS.filter(name => !host || typeof host[name] !== 'function')

/** A 404 from ctx.rest: the profile's backend does not serve this plugin's API, because the agent
 *  part is not enabled there (hermes_cli/web_server.py:435) or not installed. The renderer gets
 *  `Error('404: <body>')` (apps/desktop/electron/api-expected-404.ts), maybe with a statusCode; the
 *  status leads the message, or follows Electron's "Error invoking remote method …: Error: " prefix. */
export function isNotFound(err) {
  const status = err && typeof err === 'object' ? (err.statusCode ?? err.status) : undefined
  return Number(status) === 404 || /(?:^|Error: )404\b/.test(String((err && err.message) || err))
}

/** A refusal that says "not now" rather than "no": the gateway drains for a restart (4001
 *  runtime_draining, gateway/drain_control.py:81-90) or its storage failed (5001). Like a lost
 *  reply it is tried again later and never counts toward giving a run up. */
export function isTransient(err) {
  if (!isRpcError(err)) return true
  const data = err.data && typeof err.data === 'object' ? err.data : {}
  return err.code === 5001 || data.reason === 'runtime_draining'
}

export function failureOf(err) {
  const data = err && err.data && typeof err.data === 'object' ? err.data : {}
  return {
    code: isRpcError(err) ? err.code : null,
    reason: typeof data.reason === 'string' ? data.reason : null,
    field: typeof data.field === 'string' ? data.field : null,
    detail: String((typeof data.detail === 'string' && data.detail) || (err && err.message) || err)
  }
}

/** The one route for the window's active (connection, profile), or null when none or several match. */
export function pickRoute(routes, scope) {
  if (!Array.isArray(routes) || !scope || !scope.profile) return null
  const matches = routes.filter(
    r => r && r.profile === scope.profile && (scope.connectionId ? r.connectionId === scope.connectionId : r.primary === true)
  )
  return matches.length === 1 ? matches[0] : null
}

export function sameIdentity(stored, current) {
  return IDENTITY_KEYS.every(key => ((stored && stored[key]) || null) === ((current && current[key]) || null))
}

/** What a child session waits on the user for, from one `session.resume` snapshot. The canonical
 *  client sends `clarify_batch: true` with every `prompt.submit` (apps/desktop/src/api/client.ts), so a
 *  Rückfrage arrives as a `clarify` server request in `open_requests`, not in `prompts[]`; approvals
 *  come in `prompts[]`, or as an `approval` server request. Other blocking requests (a password or
 *  secret prompt, …) count as a Rückfrage, named by their method only: their params can carry what
 *  the user types.
 *  The client also copies every `prompts[]` entry into `open_requests` under its prompt id
 *  (canonical-protocol.ts:756-758), so each id counts once. */
export function pendingPrompts(snapshot) {
  const out = []
  const seen = new Set()
  for (const p of (snapshot && Array.isArray(snapshot.prompts) ? snapshot.prompts : [])) {
    if (!p || (p.kind !== 'clarify' && p.kind !== 'approval') || seen.has(p.prompt_id)) continue
    seen.add(p.prompt_id)
    out.push(p)
  }
  for (const req of (snapshot && Array.isArray(snapshot.open_requests) ? snapshot.open_requests : [])) {
    if (!req || typeof req.id !== 'string' || seen.has(req.id)) continue
    seen.add(req.id)
    const params = req.params && typeof req.params === 'object' ? req.params : {}
    if (req.method === 'clarify') {
      const answered = params.answers && typeof params.answers === 'object' ? params.answers : {}
      const questions = Array.isArray(params.questions) ? params.questions : []
      for (const q of questions) {
        if (!q || Object.prototype.hasOwnProperty.call(answered, q.qid)) continue
        out.push({ kind: 'clarify', prompt_id: `${req.id}#${q.qid}`, question: String(q.question || '') })
      }
      if (!questions.length) out.push({ kind: 'clarify', prompt_id: req.id })
    } else if (req.method === 'approval') {
      out.push({ kind: 'approval', prompt_id: req.id, command: params.command, description: params.description })
    } else {
      out.push({ kind: 'clarify', prompt_id: req.id, question: `Eingabe angefordert (${req.method})` })
    }
  }
  return out
}

/** docs/toolset-check.md §6 step 3 / §7, with Verksted's own toolset tolerated (decision M2).
 *  `expected` is the stored list E (null = default case D1 (a)); `baseline` is the set the first
 *  check recorded, used in the default case to refuse a set that grew. `tolerateOwn` (M2) is false
 *  for a run in another profile than the parent's: the recursion guard cannot see children there.
 *  `lazyRelaxed`: a session that already ran a turn (a follow-up `send`, §6 step 6, or the repeat
 *  of an unconfirmed submit) no longer needs `lazy: true`; the set is checked all the same. */
export function checkInfo(info, expected, baseline, tolerateOwn = true, { lazyRelaxed = false } = {}) {
  const notCheckable = detail => ({ ok: false, refusal: { reason: 'toolsets_not_checkable', detail } })
  if (!info || typeof info !== 'object') return notCheckable('the reply carries no session info')
  if (info.desktop_protocol !== CANONICAL_PROTOCOL) {
    return notCheckable(`the route does not speak ${CANONICAL_PROTOCOL} (got ${info.desktop_protocol || 'nothing'})`)
  }
  if (info.lazy !== true && !lazyRelaxed) return notCheckable('the session already has an agent; its tools can no longer be attested before the first prompt')
  const raw = info.enabled_toolsets
  if (!Array.isArray(raw) || !raw.every(name => typeof name === 'string')) return notCheckable('enabled_toolsets is missing or not a list of names')
  const effective = [...new Set(raw)].sort()
  if (!tolerateOwn && effective.includes(OWN_TOOLSET)) {
    return { ok: false, refusal: { reason: 'toolsets_exceeded', missing: [], extra: [OWN_TOOLSET], enabled_toolsets: effective,
      detail: `the session's profile enables Verksted's own toolset; a child in another profile could start runs the recursion guard cannot see. Disable the verksted plugin in that profile or decline its toolset in the profile's Tools config` } }
  }
  if (Array.isArray(expected)) {
    const want = new Set(expected)
    const have = new Set(effective)
    const missing = [...want].filter(name => !have.has(name)).sort()
    const extra = effective.filter(name => !want.has(name) && name !== OWN_TOOLSET)
    if (missing.length) {
      return { ok: false, refusal: { reason: 'toolsets_not_checkable', missing, extra, enabled_toolsets: effective,
        detail: `Hermes did not enable requested toolsets: ${missing.join(', ')}` } }
    }
    if (extra.length) {
      return { ok: false, refusal: { reason: 'toolsets_exceeded', missing, extra, enabled_toolsets: effective,
        detail: `the session would get toolsets beyond .verksted.yaml: ${extra.join(', ')}. Save the profile's Tools config once, use a slim profile, or list the toolset in .verksted.yaml` } }
    }
  } else if (Array.isArray(baseline)) {
    if (!effective.length && baseline.length) {
      // A lazy inventory whose tool snapshot failed reports [] (gateway/session_local_inventory.py:146-152).
      return notCheckable('the session reports no toolsets after it listed some at the start; a failed inventory reads like this')
    }
    const grew = effective.filter(name => !baseline.includes(name))
    if (grew.length) {
      return { ok: false, refusal: { reason: 'toolsets_exceeded', extra: grew, enabled_toolsets: effective,
        detail: `the session's toolsets grew since the start: ${grew.join(', ')}` } }
    }
  }
  return { ok: true, effective }
}

/** For a session whose inventory names no toolset beyond Verksted's own: does `tools.show` agree?
 *  It recomputes the tools from the session policy (gateway/session_ancillary.py:80-104), apart from
 *  the inventory, which reports [] both for an empty selection and when it failed. Only the own
 *  toolset's tools may be listed, by name, and the total must be what the sections list.
 *  Returns null when it agrees, else why not. Without `tolerateOwn` no tool may be listed at all. */
export function onlyOwnTools(shown, tolerateOwn = true) {
  const sections = shown && Array.isArray(shown.sections) ? shown.sections : null
  if (!sections) return 'tools.show answered without sections'
  let listed = 0
  const foreign = []
  for (const section of sections) {
    for (const tool of (section && Array.isArray(section.tools) ? section.tools : [])) {
      listed += 1
      const name = tool && typeof tool.name === 'string' ? tool.name : '?'
      if (!(tolerateOwn && section.name === OWN_TOOLSET && OWN_TOOLS.includes(name))) foreign.push(`${section && section.name}/${name}`)
    }
  }
  if (listed !== shown.total) return `tools.show counts ${shown.total} tools but lists ${listed}`
  if (foreign.length) return `the session names no toolset beyond Verksted's own, yet tools.show lists ${foreign.join(', ')}`
  return null
}

/** Reconciling a submit that may already have been admitted, from a fresh `session.resume`
 *  snapshot: `pending` lists the non-terminal admissions with `input_id` = the submission id
 *  (gateway/session_authority.py:451-460). Returns { adopt, status } for our own admission,
 *  { unclear } when the snapshot cannot rule one out, or { check, lazy } when nothing of ours is
 *  pending and nothing else runs: then the identical prompt.submit goes out again after the check.
 *  Hermes answers it with our admission if it took the first one (an exact or terminal replay,
 *  hermes_state_runtime.py:128-162) or admits it now as the only start — idempotent by
 *  submission_id + text, never a second turn (card k8, P1). `lazy` true means the session never
 *  ran a turn, so nothing was admitted. */
export function submitVerdict(snap, expected, baseline, submissionId, tolerateOwn = true) {
  const pending = snap && Array.isArray(snap.pending) ? snap.pending : null
  const mine = pending && pending.find(p => p && p.input_id === submissionId && typeof p.admission_id === 'string' && p.admission_id)
  if (mine) return { adopt: mine.admission_id, status: mine.status }
  if (!pending) return { unclear: 'the session snapshot lists no admissions' }
  if (pending.length || snap.running === true) return { unclear: 'another turn is queued or running in the session' }
  const info = snap.info
  if (!info || typeof info !== 'object') return { unclear: 'the session snapshot reports no info' }
  return { check: checkInfo(info, expected, baseline, tolerateOwn, { lazyRelaxed: true }), lazy: info.lazy === true }
}

function parseJson(text) {
  if (typeof text !== 'string') return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** What went unanswered in a finished turn, from `prompt.receipt {include_result}` →
 *  `result.messages` (this turn's rows; the only place it shows after the fact). A Rückfrage of a
 *  Desktop turn is a batch clarify: with no attached viewer it ends at once as `unanswered`
 *  (tools/clarify_tool.py:87-112, gateway/session_server_requests.py:172-173). An approval nobody
 *  gave in time fails closed as a tool row `status: "blocked"` (tools/terminal_tool.py:1137). */
export function resultNotes(result) {
  const messages = result && Array.isArray(result.messages) ? result.messages : []
  const calls = new Map()
  for (const m of messages) {
    for (const call of (m && Array.isArray(m.tool_calls) ? m.tool_calls : [])) {
      const fn = call && call.function
      if (call && typeof call.id === 'string' && fn) calls.set(call.id, { name: fn.name, args: parseJson(fn.arguments) || {} })
    }
  }
  const unanswered = []
  const blocked = []
  for (const m of messages) {
    if (!m || m.role !== 'tool') continue
    const body = parseJson(m.content)
    if (!body || typeof body !== 'object') continue
    if (Array.isArray(body.responses)) {
      for (const r of body.responses) {
        if (r && r.status === 'unanswered') unanswered.push({ question: String(r.question || '').slice(0, 500) })
      }
    }
    if (body.status === 'blocked') {
      const call = calls.get(m.tool_call_id) || {}
      const args = call.args || {}
      const command = typeof args.command === 'string' ? args.command : (call.name || m.name || m.tool_name || 'tool')
      const why = String(body.error || '')
      blocked.push({ command: command.slice(0, 500), reason: /denied/i.test(why) ? 'denied' : /timed out/i.test(why) ? 'timeout' : 'blocked' })
    }
  }
  return { unanswered: unanswered.slice(0, 8), blocked: blocked.slice(0, 8) }
}

function randomId() {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

const enc = encodeURIComponent

/**
 * The window's worker. `rest(path, opts)` is `ctx.rest`; `host` is the SDK host (or a fake).
 * poll() claims and starts runs, renewAll() keeps the leases, trackAll() follows running runs.
 */
export function createWorker({ host, rest, owner, log = () => {}, sleep, now = () => Date.now() }) {
  const wait = sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)))
  // run_id → { generation, tracking, route, identity, sessionId, admissionId, failures, busy,
  //            release (the route retain), cancelRequested, cancelSent }
  const held = new Map()
  const listeners = new Set()
  // note: what keeps this window from working, in full (the page, the chip's tooltip); chip: its
  // short form for the status chip, when the note has one.
  let view = { runs: [], profileHome: null, note: null, chip: null, updatedAt: null }
  let polling = null
  let pollAgain = false
  let disposed = false
  // Bumped whenever the primary gateway socket may have been replaced. A session attached before
  // the bump is no longer attached on the new socket, and the client would resume it unseen before
  // a prompt.submit (client.ts:46-53, :482-499): such a session is re-attached and checked first.
  let epoch = 0

  const publish = patch => {
    view = { ...view, ...patch }
    for (const fn of listeners) fn()
  }
  const setNote = (note, chip = null) => publish({ note, chip })

  const scope = () => {
    const fromHost = typeof host.activeConnectionId === 'function' ? host.activeConnectionId() : null
    const connectionId = fromHost || (host.state && host.state.connectionId ? host.state.connectionId.get() : null) || null
    const profile = host.state && host.state.profile ? host.state.profile.get() : null
    return { connectionId, profile }
  }

  const rpc = (route, method, params = {}) => {
    guardCall(method, params)
    return host.requestProfile(route, method, params, RPC_TIMEOUT_MS)
  }

  // After a lost reply only the identical call goes out again, on the same route (same identity).
  const rpcRepeat = async (route, method, params) => {
    let last = null
    for (let attempt = 1; attempt <= REPEATS; attempt++) {
      try {
        return await rpc(route, method, params)
      } catch (err) {
        if (err && err.guard) throw err
        if (isRpcError(err) && err.code !== 5001) throw err
        last = err
        if (attempt < REPEATS) await wait(1000 * attempt)
      }
    }
    throw new Deferred(`${method}: no reply after ${REPEATS} attempts (${(last && last.message) || last})`)
  }

  const api = (path, body) => rest(path, body === undefined ? undefined : { method: 'POST', body })

  const step = async (runId, name, data) => {
    const h = held.get(runId)
    if (!h) throw new StepRefused({ code: 'not_held', message: 'this window no longer holds the claim' })
    const answer = await api(`/runs/${enc(runId)}/step`, { owner, generation: h.generation, step: name, data: data || {} })
    if (!answer || answer.ok !== true) throw new StepRefused(answer)
    // A stop the tool stored reaches the holder with the answer to its next step.
    if (answer.run && answer.run.cancel_requested) h.cancelRequested = true
    return answer.run
  }

  // Forget a run and give its route back (the retain kept the child attached while it was followed).
  const drop = runId => {
    const h = held.get(runId)
    if (!h) return null
    held.delete(runId)
    if (typeof h.release === 'function') {
      try {
        h.release()
      } catch (err) {
        log('verksted: route release failed', err)
      }
    }
    return h
  }

  const letGo = async runId => {
    const h = drop(runId)
    if (!h) return
    try {
      await api(`/runs/${enc(runId)}/release`, { owner, generation: h.generation })
    } catch (err) {
      log('verksted: release failed', runId, err)
    }
  }

  // -- one start, on one retained route (take() holds the retain) --------------------------------
  async function drive(runId, run, route, identity) {
    const expected = Array.isArray(run.expected_toolsets) ? run.expected_toolsets : null
    const tolerateOwn = run.own_toolset_tolerated === true // fail closed: only the API says so
    let state = run.state
    let sessionId = run.session_id || null
    let admissionId = run.admission_id || null
    if (state === 'running') return { running: true, sessionId, admissionId }

    const refuse = async refusal => {
      await step(runId, 'refused', refusal)
      return { running: false }
    }
    // A submit of this run may be admitted already: neither refused nor sent again (arch: never
    // repeat blindly; an unclear outcome is `unknown`).
    const unsure = async detail => {
      await step(runId, 'unknown', { reason: 'submit_unconfirmed', detail })
      return { running: false }
    }
    const rpcRefusal = (err, reason) => {
      const f = failureOf(err)
      return { reason: f.reason || reason, detail: f.detail, ...(f.field ? { field: f.field } : {}) }
    }
    const resume = () => rpcRepeat(route, 'session.resume', { session_id: sessionId, omit_messages: true })
    const running = async (adm, status) => {
      await step(runId, 'running', { identity, admission_id: adm, receipt_status: status || 'queued' })
      return { running: true, sessionId, admissionId: adm }
    }

    let info = null
    let snap = null
    let attachedAt = epoch
    let sent = state === 'submit_requested' // an earlier claim may have sent the submit
    if (state === 'waiting_for_gui') {
      await step(runId, 'create_requested', { identity })
      state = 'create_requested'
    }
    if (state === 'create_requested') {
      let created
      attachedAt = epoch
      try {
        created = await rpcRepeat(route, 'session.create', run.create_params)
      } catch (err) {
        if (err instanceof Deferred) throw err
        return refuse(err && err.guard ? { reason: 'bad_create_params', detail: err.message } : rpcRefusal(err, 'create_refused'))
      }
      sessionId = created && created.session_id
      if (typeof sessionId !== 'string' || !sessionId) {
        return refuse({ reason: 'create_refused', detail: 'session.create answered without a session_id' })
      }
      info = created.info || null
      await step(runId, 'session_created', {
        identity, session_id: sessionId,
        ...(info && Array.isArray(info.enabled_toolsets) ? { enabled_toolsets: info.enabled_toolsets } : {})
      })
      state = 'session_created'
    } else {
      // A new claim means a new socket: re-attach first (toolset-check.md §6 step 5).
      attachedAt = epoch
      try {
        snap = await resume()
        info = (snap && snap.info) || null
      } catch (err) {
        if (err instanceof Deferred) throw err
        if (sent) return unsure(`session.resume failed: ${failureOf(err).detail}`)
        return refuse(rpcRefusal(err, 'resume_refused'))
      }
    }

    let baseline = Array.isArray(run.effective_toolsets) ? run.effective_toolsets : null
    if (state === 'session_created') {
      const first = checkInfo(info, expected, null, tolerateOwn)
      if (!first.ok) return refuse(first.refusal)
      let shown
      try {
        shown = await rpcRepeat(route, 'tools.show', { session_id: sessionId })
      } catch (err) {
        if (err instanceof Deferred) throw err
        return refuse({ reason: 'toolsets_not_checkable', detail: `tools.show failed: ${failureOf(err).detail}` })
      }
      const total = shown && typeof shown.total === 'number' ? shown.total : null
      if (total === null) return refuse({ reason: 'toolsets_not_checkable', detail: 'tools.show answered without a total' })
      if (!first.effective.some(name => name !== OWN_TOOLSET)) {
        // E = [] or a default case that reads empty: only tools.show tells apart "nothing
        // selected" from an inventory that failed open (toolset-check.md §10 M2).
        const why = onlyOwnTools(shown, tolerateOwn)
        if (why) return refuse({ reason: 'toolsets_not_checkable', detail: why })
      }
      await step(runId, 'toolsets_checked', {
        identity, enabled_toolsets: first.effective, check: { effective: first.effective, tools_total: total }
      })
      state = 'toolsets_checked'
      baseline = first.effective
    }

    if (state === 'toolsets_checked') {
      let again
      try {
        again = await rpcRepeat(route, 'session.info', { session_id: sessionId })
      } catch (err) {
        if (err instanceof Deferred) throw err
        return refuse({ reason: 'toolsets_not_checkable', detail: `session.info failed: ${failureOf(err).detail}` })
      }
      const second = checkInfo(again, expected, baseline, tolerateOwn)
      if (!second.ok) return refuse(second.refusal)
      await step(runId, 'submit_requested', { identity })
      state = 'submit_requested'
    } else if (state === 'send_waiting') {
      // A follow-up turn (toolset-check.md §6 step 6): the re-attached session is checked again. It
      // ran a turn before, so `lazy` may be false; the default case refuses a set that grew (D1).
      const check = checkInfo(info, expected, baseline, tolerateOwn, { lazyRelaxed: true })
      if (!check.ok) return refuse(check.refusal)
      await step(runId, 'submit_requested', { identity })
      state = 'submit_requested'
    } else if (state === 'submit_requested') {
      // Reconciling an unconfirmed submit: decide from the fresh snapshot, never repeat blindly.
      const verdict = submitVerdict(snap, expected, baseline, run.submission_id, tolerateOwn)
      if (verdict.adopt) return running(verdict.adopt, verdict.status)
      if (verdict.unclear) return unsure(verdict.unclear)
      // A session that never ran a turn admitted nothing: a failed check is a refusal, and a stored
      // stop needs no Hermes. Otherwise our turn may have run already: a failed check is unclear.
      if (!verdict.check.ok) return verdict.lazy ? refuse(verdict.check.refusal) : unsure(verdict.check.refusal.detail)
      if (verdict.lazy && run.cancel_requested) {
        await step(runId, 'cancelled', { reason: 'never_admitted' })
        return { running: false }
      }
    }

    // prompt.submit, repeated identically after a lost reply. A reconnect since the session was
    // attached is answered by re-attaching here, seen, and deciding again from that snapshot.
    let last = null
    for (let attempt = 1; attempt <= REPEATS; attempt++) {
      if (epoch !== attachedAt) {
        attachedAt = epoch
        let fresh
        try {
          fresh = await resume()
        } catch (err) {
          if (err instanceof Deferred) throw err
          return sent ? unsure(`session.resume failed: ${failureOf(err).detail}`) : refuse(rpcRefusal(err, 'resume_refused'))
        }
        const verdict = submitVerdict(fresh, expected, baseline, run.submission_id, tolerateOwn)
        if (verdict.adopt) return running(verdict.adopt, verdict.status)
        if (verdict.unclear) return sent ? unsure(verdict.unclear) : refuse({ reason: 'toolsets_not_checkable', detail: verdict.unclear })
        if (!verdict.check.ok) return sent ? unsure(verdict.check.refusal.detail) : refuse(verdict.check.refusal)
      }
      sent = true
      try {
        const admitted = await rpc(route, 'prompt.submit', {
          session_id: sessionId, text: run.prompt_text, submission_id: run.submission_id
        })
        const adm = admitted && admitted.admission_id
        if (typeof adm !== 'string' || !adm) throw new Deferred('prompt.submit answered without an admission_id')
        return running(adm, admitted.status)
      } catch (err) {
        if (err instanceof Deferred || (err && err.guard)) throw err
        if (isRpcError(err) && err.code !== 5001) return refuse(rpcRefusal(err, 'submit_refused'))
        last = err
        if (attempt < REPEATS) await wait(1000 * attempt)
      }
    }
    throw new Deferred(`prompt.submit: no reply after ${REPEATS} attempts (${(last && last.message) || last})`)
  }

  // -- claim one run ------------------------------------------------------------------------
  async function take(summary) {
    const runId = summary.run_id
    const routes = await host.profileRoutes()
    const active = pickRoute(routes, scope())
    if (!active) {
      setNote('Kein eindeutiger Verbindungsweg für das aktive Profil gefunden.')
      return
    }
    // The session runs in the profile frozen at run (k5), on this window's connection: the store and
    // ctx.rest stay with the parent's profile, only the session route changes.
    const wanted = typeof summary.session_profile === 'string' && summary.session_profile ? summary.session_profile : active.profile
    const route = wanted === active.profile ? active : pickRoute(routes, { connectionId: active.connectionId, profile: wanted })
    const stored = summary.identity || {}
    // A running child, or a follow-up for one, belongs to the connection its session was created on.
    // Only the connection is compared here; the profile and the principal are known after the claim.
    if ((summary.state === 'running' || summary.state === 'send_waiting') && stored.connection_id && !(route && sameIdentity(stored, {
      connection_id: route.connectionId, route_profile: route.profile, target_profile: route.targetProfile,
      profile_id: stored.profile_id, subject: stored.subject
    }))) {
      return // a child of another connection: left to a window of that connection
    }

    let describe = null
    let gate = route ? null : {
      reason: 'profile_route_missing',
      detail: `this window's connection ${active.connectionId} has no route to profile ${wanted}; the session would have to start elsewhere`
    }
    if (route) {
      try {
        describe = await rpc(route, 'runtime.describe', {})
      } catch (err) {
        if (!isRpcError(err)) throw err // no reply: try again on the next poll
        if (err.code !== -32601) {
          // Only an unknown method marks a legacy route (gateway/session_controls.py:354-359). A
          // refusal (draining :296-304, storage 5001 :373-377, permission :534-538) is this
          // gateway's state now: claim nothing, a later poll or another window tries again.
          const f = failureOf(err)
          setNote(`runtime.describe refused (${f.reason || f.code}): ${f.detail}. Der Auftrag wartet.`)
          return
        }
        gate = { reason: 'legacy_route', detail: 'runtime.describe is unknown on this route (legacy gateway); Verksted needs the canonical gateway' }
      }
      const caps = describe && Array.isArray(describe.capabilities) ? describe.capabilities : []
      if (!gate && !caps.includes(REQUIRED_CAPABILITY)) {
        gate = { reason: 'legacy_route', detail: `runtime.describe does not list ${REQUIRED_CAPABILITY}` }
      }
    }

    const claim = await api(`/runs/${enc(runId)}/claim`, {
      owner, lease_s: LEASE_S, profile_id: (describe && describe.profile_id) || null
    })
    if (!claim || claim.ok !== true) {
      if (!claim || claim.code !== 'claimed') setNote(`Claim abgelehnt: ${(claim && (claim.message || claim.code)) || 'keine Antwort'}`)
      return
    }
    held.set(runId, { generation: claim.generation, tracking: false, failures: 0, busy: false })
    try {
      const run = claim.run
      if (gate) {
        if (claim.mode === 'start') await step(runId, 'refused', gate)
        return
      }
      const identity = {
        connection_id: route.connectionId, route_profile: route.profile, target_profile: route.targetProfile,
        profile_id: describe.profile_id, instance_id: describe.instance_id || null,
        subject: claim.subject || null // the principal the plugin API authenticated this claim as
      }
      if (claim.mode === 'reconcile' && !sameIdentity(run.identity, identity)) {
        if (run.state !== 'running') {
          await step(runId, 'unknown', {
            reason: 'identity_changed',
            detail: 'the start was requested under another connection identity; it is not repeated under this one'
          })
        }
        return
      }
      if (run.state === 'send_waiting' && !sameIdentity(run.identity, identity)) {
        await step(runId, 'refused', {
          reason: 'identity_changed',
          detail: 'the child session was created under another connection identity; a follow-up is sent only under that one'
        })
        return
      }
      // The retain lasts until the run is final or this window lets it go: it keeps a secondary
      // route's socket open and the child attached to it as a viewer, so a Rückfrage of the child
      // has someone to wait for (card k8, P5) and tracking dials no socket per tick (audit 9d).
      const h0 = held.get(runId)
      h0.release = await host.retainProfile(route)
      h0.cancelRequested = run.cancel_requested === true
      let outcome
      try {
        outcome = await drive(runId, run, route, identity)
      } catch (err) {
        if (err instanceof StepRefused && err.code === 'identity_mismatch') {
          await step(runId, 'unknown', { reason: 'identity_changed', detail: err.message }).catch(() => {})
          return
        }
        if (err instanceof StepRefused && MOVED_ON.includes(err.code)) {
          log('verksted: run moved on without this window', runId, err.code)
          return
        }
        throw err
      }
      if (outcome && outcome.running) {
        Object.assign(held.get(runId) || {}, {
          tracking: true, route, identity, sessionId: outcome.sessionId, admissionId: outcome.admissionId
        })
        await track(runId)
      }
    } finally {
      const h = held.get(runId)
      if (!h || !h.tracking) await letGo(runId)
    }
  }

  // -- follow a running run -----------------------------------------------------------------
  // A stop the tool stored, asked of Hermes once for our own admission. Only the terminal receipt
  // that follows says whether it came in time; a draining gateway or a lost reply asks again.
  async function sendCancel(h) {
    try {
      await rpc(h.route, 'prompt.cancel', { session_id: h.sessionId, admission_id: h.admissionId })
      h.cancelSent = true
    } catch (err) {
      if (isTransient(err)) return
      h.cancelSent = true // unknown_execution and other refusals: the receipt tells what became of the turn
      log('verksted: prompt.cancel refused', failureOf(err))
    }
  }

  async function track(runId) {
    const h = held.get(runId)
    if (!h || !h.tracking || h.busy) return
    h.busy = true
    try {
      if (h.cancelRequested && !h.cancelSent) await sendCancel(h)
      let receipt
      try {
        receipt = await rpc(h.route, 'prompt.receipt', { session_id: h.sessionId, admission_id: h.admissionId })
      } catch (err) {
        if (isTransient(err)) return // draining, storage or no reply: not a reason to give the run up
        h.failures += 1
        if (h.failures >= 3) {
          await step(runId, 'unknown', { reason: 'receipt_refused', detail: failureOf(err).detail })
          await letGo(runId)
        }
        return
      }
      h.failures = 0
      const status = receipt && receipt.status
      if (status === 'terminal') {
        const full = await rpc(h.route, 'prompt.receipt', {
          session_id: h.sessionId, admission_id: h.admissionId, include_result: true
        })
        const result = (full && full.result && typeof full.result === 'object') ? full.result : {}
        // The plugin API checks the closing result block of final_response (card k10); this part
        // sends the answer and what went unanswered, never a list of outputs of its own.
        await step(runId, 'finished', {
          identity: h.identity,
          outcome: (full && full.outcome) || receipt.outcome || 'terminal',
          final_response: typeof result.final_response === 'string' ? result.final_response : '',
          ...resultNotes(result)
        })
        await letGo(runId)
        return
      }
      if (status === 'unknown') {
        await step(runId, 'unknown', {
          reason: 'admission_unknown',
          detail: 'Hermes cannot say whether this turn ran (for example after a gateway stop); nothing is retried'
        })
        await letGo(runId)
        return
      }
      let prompts = []
      if (status === 'started') {
        try {
          const snap = await rpc(h.route, 'session.resume', { session_id: h.sessionId, omit_messages: true })
          prompts = pendingPrompts(snap)
        } catch (err) {
          if (!isRpcError(err)) return
        }
      }
      await step(runId, 'running', {
        identity: h.identity, admission_id: h.admissionId, receipt_status: status || 'queued', prompts
      })
    } catch (err) {
      if (err instanceof StepRefused) await letGo(runId) // the claim is gone; a later claim reconciles
      else log('verksted: tracking failed', runId, err)
    } finally {
      const cur = held.get(runId)
      if (cur) cur.busy = false
    }
  }

  async function pollOnce() {
    // Too old a Desktop claims nothing: it could not start or follow a run. It does not poll either,
    // so the tool keeps saying that no Desktop part polls this profile.
    const missing = missingHostMembers(host)
    if (missing.length) {
      setNote(`${CHIP_NOTES.tooOld}: es fehlt ${missing.map(name => `host.${name}`).join(', ')}. ` +
        'Hermes Desktop aktualisieren; bis dahin startet dieses Fenster keine Aufträge.', CHIP_NOTES.tooOld)
      return
    }
    let list
    try {
      // The owner names this window in the poll the plugin API records for the tool's
      // waiting_for_gui answer ("last Desktop poll … ago").
      list = await api(`/runs/claimable?owner=${enc(owner)}`)
    } catch (err) {
      if (isNotFound(err)) {
        setNote(`${CHIP_NOTES.agentOff}: die Verksted-API dieses Profils antwortet 404. Das Verksted-Plugin ` +
          'für dieses Profil aktivieren (plugins.enabled), dann das Gateway neu starten.', CHIP_NOTES.agentOff)
      } else {
        setNote(`Verksted-API nicht erreichbar: ${(err && err.message) || err}`)
      }
      return
    }
    if (!list || list.ok !== true) {
      setNote(`Verksted-API: ${(list && (list.message || list.code)) || 'keine Antwort'}`)
      return
    }
    setNote(null)
    for (const summary of list.runs || []) {
      if (disposed) return
      if (held.has(summary.run_id)) continue
      if (summary.claim && summary.claim.live && summary.claim.owner !== owner) continue
      try {
        await take(summary)
      } catch (err) {
        log('verksted: run', summary.run_id, err)
        setNote(`${summary.run_id}: ${(err && err.message) || err}`)
      }
    }
  }

  async function refreshView() {
    try {
      const all = await api('/runs')
      if (!all || all.ok !== true) return
      publish({ runs: all.runs || [], profileHome: all.profile_home || null, updatedAt: now() })
      // A stop for a run this window follows: ask Hermes now rather than on the next tick.
      for (const run of all.runs || []) {
        const h = held.get(run.run_id)
        if (!h || !run.cancel_requested || h.cancelRequested) continue
        h.cancelRequested = true
        if (h.tracking) await track(run.run_id)
      }
    } catch (err) {
      log('verksted: list failed', err)
    }
  }

  function poll() {
    if (disposed) return Promise.resolve()
    if (polling) {
      pollAgain = true
      return polling
    }
    polling = (async () => {
      try {
        do {
          pollAgain = false
          await pollOnce()
          await refreshView()
        } while (pollAgain && !disposed)
      } finally {
        polling = null
      }
    })()
    return polling
  }

  async function renewAll() {
    for (const [runId, h] of [...held]) {
      try {
        const answer = await api(`/runs/${enc(runId)}/renew`, { owner, generation: h.generation, lease_s: LEASE_S })
        if (!answer || answer.ok !== true) {
          if (answer && CLAIM_GONE.includes(answer.code)) drop(runId)
        }
      } catch (err) {
        log('verksted: renew failed', runId, err)
      }
    }
  }

  async function trackAll() {
    for (const [runId, h] of [...held]) {
      if (h.tracking) await track(runId)
    }
    await refreshView()
  }

  /** host.state.gateway changed: anything but 'open' means the primary socket may be replaced. */
  function noteGatewayState(value) {
    if (value !== 'open') epoch += 1
  }

  function onGatewayEvent(event) {
    if (event && event.type === 'gateway.ready') {
      epoch += 1 // a (re)connected primary socket
      // The new socket is attached to no child: re-attach every followed run now instead of on the
      // next tick, so a Rückfrage asked in the gap finds a viewer (card k8, P5).
      for (const [runId, h] of held) {
        if (h.tracking) track(runId).then(refreshView)
      }
      return
    }
    if (!event || typeof event.session_id !== 'string' || !event.session_id) return
    if (!/^(approval|clarify)\.(request|settled)$|^message\.complete$/.test(event.type || '')) return
    for (const [runId, h] of held) {
      if (h.tracking && h.sessionId === event.session_id) track(runId).then(refreshView)
    }
  }

  async function openSession(run) {
    const ident = (run && run.identity) || {}
    if (missingHostMembers(host).length) {
      host.notify && host.notify({ kind: 'error', message: `${CHIP_NOTES.tooOld}: Hermes Desktop aktualisieren.` })
      return
    }
    const routes = await host.profileRoutes()
    const route = (routes || []).find(r => r && r.connectionId === ident.connection_id && r.profile === ident.route_profile)
    if (!route || !run.session_id) {
      host.notify && host.notify({ kind: 'error', message: 'Die Verbindung dieses Auftrags ist in diesem Fenster nicht verfügbar.' })
      return
    }
    await host.openSession(run.session_id, { route, intent: 'tab' })
  }

  return {
    owner,
    poll,
    renewAll,
    trackAll,
    onGatewayEvent,
    noteGatewayState,
    openSession,
    held: () => new Map(held),
    getView: () => view,
    subscribe: fn => {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    // The window goes away: give every claim back so another window can take over at once rather
    // than after the lease. Best effort (the requests may not finish while the window unloads);
    // never a stop — closing a window or tab does not end a run.
    dispose: () => {
      disposed = true
      listeners.clear()
      return Promise.all([...held.keys()].map(runId => letGo(runId)))
    }
  }
}

// -- UI ---------------------------------------------------------------------------------------

const h = React.createElement

export function labelOf(run, now) {
  const prompts = run.state === 'running' && Array.isArray(run.prompts) ? run.prompts : []
  const waiting = prompts.length ? WAITING_LABELS[prompts[0].kind] : null
  const stale = STARTED.includes(run.state) && !(run.claim && run.claim.expires_at * 1000 > now)
  const stopping = run.cancel_requested === true && !FINAL.includes(run.state)
  return { text: waiting || (stopping ? STOPPING_LABEL : null) || LABELS[run.state] || run.state, waiting: !!waiting, stale }
}

/** The checked result of a finished run, line by line (card k10): what the child named and Verksted
 *  found inside the workspace, what it named but was not there, what went unanswered. */
export function resultLines(run) {
  if (run.state !== 'finished') return []
  const lines = []
  if (run.result_block && run.result_block !== 'ok') lines.push(RESULT_BLOCK_LABELS[run.result_block] || run.result_block)
  for (const o of Array.isArray(run.outputs) ? run.outputs : []) lines.push(`→ ${o.relative}${o.kind === 'folder' ? '/' : ''}`)
  for (const m of Array.isArray(run.missing) ? run.missing : []) lines.push(`✗ ${m.path} (${m.reason})`)
  for (const q of Array.isArray(run.unanswered) ? run.unanswered : []) lines.push(`Rückfrage ohne Antwort: ${q.question}`)
  for (const b of Array.isArray(run.blocked) ? run.blocked : []) lines.push(`Nicht freigegeben: ${b.command}`)
  return lines
}

function useView(worker) {
  if (typeof React.useSyncExternalStore === 'function') {
    return React.useSyncExternalStore(worker.subscribe, worker.getView, worker.getView)
  }
  const [view, setView] = React.useState(worker.getView())
  React.useEffect(() => worker.subscribe(() => setView(worker.getView())), [worker])
  return view
}

/** The status chip's text, or null to hide it: a Rückfrage or Freigabe first, then a note (its short
 *  form, else the note clipped) rather than a healthy-looking count, then the active runs. */
export function chipText(view, now) {
  const active = view.runs.filter(run => !FINAL.includes(run.state))
  const waiting = active.map(run => labelOf(run, now)).find(label => label.waiting)
  if (waiting) return `Verksted: ${waiting.text}`
  if (view.note) {
    const note = view.chip || view.note
    return `Verksted: ${note.length > CHIP_NOTE_MAX ? `${note.slice(0, CHIP_NOTE_MAX - 1).trimEnd()}…` : note}`
  }
  return active.length ? `Verksted: ${active.length} aktiv` : null
}

function StatusChip({ worker, host }) {
  const view = useView(worker)
  const text = chipText(view, Date.now())
  if (!text) return null
  return h('button', {
    type: 'button',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    title: view.note || 'Verksted-Aufträge anzeigen',
    onClick: () => host.navigate('/verksted')
  }, text)
}

/** The resolved state frozen at run, in one line (arch §3: "Die GUI zeigt den aufgelösten Stand"). */
export function configLine(run) {
  const listed = Array.isArray(run.expected_toolsets)
    ? (run.expected_toolsets.length ? run.expected_toolsets.join(', ') : 'keine')
    : 'Profil-Standard'
  const config = run.config || {}
  const parts = [`Toolsets: ${listed}`]
  if (config.workspace) parts.push(`Arbeitsbereich: ${config.workspace}`)
  if (typeof config.fingerprint === 'string') parts.push(config.fingerprint.slice(0, 19))
  return parts.join(' · ')
}

function profileLine(run) {
  const session = run.session_profile || run.parent_profile
  return session === run.parent_profile ? session : `${session} (aus ${run.parent_profile})`
}

function RunRow({ run, worker, now }) {
  const label = labelOf(run, now)
  const children = [
    h('div', { key: 'head', className: 'flex items-center gap-2' },
      h('span', { className: 'font-medium' }, run.folder),
      h('span', { className: label.waiting ? 'text-(--ui-text-warning)' : 'text-(--ui-text-secondary)' }, label.text),
      label.stale ? h('span', { className: 'text-(--ui-text-tertiary)' }, '(veraltet)') : null),
    h('div', { key: 'task', className: 'text-(--ui-text-tertiary)' }, `${profileLine(run)} · ${run.task_preview}`),
    h('div', { key: 'config', className: 'text-(--ui-text-tertiary)' }, configLine(run))
  ]
  if (run.reason) children.push(h('div', { key: 'reason' }, `${run.reason}: ${run.detail || ''}`))
  if (run.result_preview) children.push(h('div', { key: 'result', className: 'whitespace-pre-wrap' }, run.result_preview))
  const lines = resultLines(run)
  if (lines.length) children.push(h('div', { key: 'outputs', className: 'text-(--ui-text-secondary)' }, ...lines.map((line, i) => h('div', { key: i }, line))))
  if (run.session_id) {
    children.push(h('button', {
      key: 'open', type: 'button', className: 'self-start rounded border px-2 py-0.5',
      onClick: () => worker.openSession(run).catch(err => console.warn('verksted: open session failed', err))
    }, 'Session öffnen'))
  }
  return h('div', { className: 'flex flex-col gap-1 border-b py-2 text-sm' }, ...children)
}

function RunsPage({ worker }) {
  const view = useView(worker)
  const now = Date.now()
  return h('div', { className: 'flex h-full flex-col gap-2 overflow-auto p-4' },
    h('div', { className: 'text-base font-medium' }, 'Verksted'),
    view.note ? h('div', { className: 'text-(--ui-text-warning)' }, view.note) : null,
    view.runs.length
      ? view.runs.map(run => h(RunRow, { key: run.run_id, run, worker, now }))
      : h('div', { className: 'text-(--ui-text-tertiary)' }, 'Keine Aufträge in diesem Profil.'))
}

export default {
  id: PLUGIN_ID,
  name: 'Verksted',
  description: 'Starts Verksted runs as regular Hermes sessions and shows their status.',
  defaultEnabled: false,
  register(ctx) {
    const host = sdk.host
    const worker = createWorker({
      host,
      rest: (path, opts) => ctx.rest(path, opts),
      owner: `vk-win-${randomId()}`,
      log: (...args) => console.warn(...args)
    })
    ctx.registerMany([
      { id: 'status', area: sdk.STATUSBAR_AREAS.right, order: 140, render: () => h(StatusChip, { worker, host }) },
      { id: 'page', area: sdk.ROUTES_AREA, data: { path: '/verksted' }, render: () => h(RunsPage, { worker }) },
      { id: 'nav', area: sdk.SIDEBAR_NAV_AREA, data: { path: '/verksted', label: 'Verksted', codicon: 'tools' } }
    ])
    const kick = () => {
      worker.poll().catch(err => console.warn('verksted: poll failed', err))
    }
    ctx.onEvent(`plugin.${PLUGIN_ID}.work`, kick)
    ctx.onEvent('*', event => worker.onGatewayEvent(event))
    ctx.setInterval(kick, POLL_MS)
    ctx.setInterval(() => {
      worker.renewAll().catch(err => console.warn('verksted: renew failed', err))
    }, RENEW_MS)
    ctx.setInterval(() => {
      worker.trackAll().catch(err => console.warn('verksted: tracking failed', err))
    }, TRACK_MS)
    const state = host.state || {}
    const onGateway = value => {
      worker.noteGatewayState(value)
      if (value === 'open') kick()
    }
    for (const [atom, onChange] of [[state.gateway, onGateway], [state.profile, kick], [state.connectionId, kick]]) {
      if (!atom || typeof atom.listen !== 'function') continue
      const stop = atom.listen(onChange)
      if (typeof stop === 'function') ctx.onDispose(stop)
    }
    ctx.onDispose(() => worker.dispose())
    kick()
  }
}
