// A fake SDK host (routes, retained profiles, gateway calls) and a fake plugin API (ctx.rest) that
// share one ordered log, so a test can assert the exact interleaving of a start.

export class RpcError extends Error {
  constructor(code, message, data) {
    super(message)
    this.code = code
    if (data) this.data = data
  }
}

/** A lost reply: no JSON-RPC code (timeout, closed socket). */
export class TransportError extends Error {}

export const ROUTE = Object.freeze({ connectionId: 'local', mode: 'local', primary: true, profile: 'default', targetProfile: 'default' })
export const DESCRIBE = Object.freeze({ profile_id: '/homes/default', instance_id: 'inst-1', capabilities: ['durable-admission-v1'] })
export const IDENTITY = Object.freeze({ connection_id: 'local', route_profile: 'default', target_profile: 'default',
  profile_id: '/homes/default', instance_id: 'inst-1', subject: null })

export const info = (enabled, extra = {}) => ({ desktop_protocol: 'hermes-gateway-v1', lazy: true, enabled_toolsets: enabled, ...extra })

export function storedRun(over = {}) {
  return {
    run_id: 'vk-1', state: 'waiting_for_gui', folder: '/work/a', task_preview: 'do it', parent_profile: 'default',
    session_profile: 'default', own_toolset_tolerated: true,
    config: { workspace: '/work/a', toolsets: ['terminal', 'file'], ceilings: [], files: [], fingerprint: 'sha256:0123456789abcdef' },
    expected_toolsets: ['file', 'terminal'], effective_toolsets: null, session_id: null, admission_id: null,
    identity: { connection_id: null, route_profile: null, target_profile: null, profile_id: null, instance_id: null },
    claim: { owner: null, generation: 0, expires_at: null, live: false },
    request_id: 'vk-1.create',
    create_params: { cwd: '/work/a', cwd_explicit: true, request_id: 'vk-1.create', toolsets: ['file', 'terminal', 'no_mcp'] },
    submission_id: 'vk-1.submit.1', prompt_text: 'do it',
    ...over
  }
}

export function happyHandlers(enabled = ['file', 'terminal']) {
  return {
    'runtime.describe': DESCRIBE,
    'session.create': { session_id: 's-1', stored_session_id: 's-1', info: info(enabled), running: false, pending: [], prompts: [] },
    'tools.show': { sections: [], total: 7 },
    'session.info': info(enabled),
    'prompt.submit': { admission_id: 'adm-1', status: 'queued' },
    'prompt.receipt': { admission_id: 'adm-1', status: 'queued' },
    'session.resume': { session_id: 's-1', info: info(enabled), running: false, pending: [], prompts: [], open_requests: [] }
  }
}

// What Desktop's canonical client hands a caller for session.create / session.resume, ported from
// Hermes ad2b705 apps/desktop/src/api/canonical-protocol.ts: canonicalPromptParams (:39-46) and
// snapshotResult (:743-761). Every prompts[] entry is copied into open_requests (id = prompt_id),
// a clarify without `questions` becoming one question whose qid is the prompt_id.
export function canonicalPromptParams(kind, sessionId, prompt) {
  const choices = Array.isArray(prompt.choices) ? prompt.choices : []
  return kind === 'clarify'
    ? { session_id: sessionId, answers: prompt.answers,
        questions: Array.isArray(prompt.questions) ? prompt.questions : [{ qid: prompt.prompt_id, question: prompt.question, choices, multi_select: prompt.multi_select }] }
    : { session_id: sessionId, request_id: prompt.prompt_id, command: prompt.command, description: prompt.description, choices,
        allow_permanent: choices.includes('always'), edit: prompt.edit }
}

export function canonicalSnapshot(value) {
  if (!value || typeof value !== 'object') return value
  const sid = value.session_id
  const prompts = (value.prompts ?? []).map(prompt => ({ ...prompt, request_id: prompt.prompt_id }))
  const open_requests = [...(value.open_requests ?? []), ...prompts.map(prompt => ({
    id: prompt.prompt_id, method: prompt.kind, params: canonicalPromptParams(prompt.kind, sid, prompt)
  }))]
  return { ...value, open_requests, pending_approval: prompts.find(p => p.kind === 'approval'), pending_clarify: prompts.find(p => p.kind === 'clarify'),
    info: { ...value.info, stored_session_id: value.stored_session_id, pending_submissions: value.pending_submissions,
      replay_epoch: value.replay_epoch, last_sequence: value.last_sequence, execution_generation: value.execution_generation, running: value.running } }
}

// apps/desktop/src/api/client.ts:46-53 (ATTACH_REQUIRED) and :482-499 (attachForRequest): a method
// that needs the session attached on THIS socket first resumes it there, unseen by the caller.
const ATTACH_REQUIRED = new Set(['prompt.submit'])

const atom = value => ({ get: () => value, listen: () => () => {} })

/** handlers: method → answer | Error | (params) => answer | [answers in call order]. */
export function makeHost({ routes = [ROUTE], handlers = happyHandlers(), log = [] } = {}) {
  const attached = new Set()
  const answer = (method, params) => {
    let a = handlers[method]
    if (Array.isArray(a)) a = a.length > 1 ? a.shift() : a[0]
    if (typeof a === 'function') a = a(params)
    if (a === undefined) throw new RpcError(-32601, `unknown method ${method}`)
    if (a instanceof Error) throw a
    a = structuredClone(a)
    if (method === 'session.create' || method === 'session.resume') {
      a = canonicalSnapshot(a)
      if (a && a.session_id) attached.add(a.session_id)
    }
    return a
  }
  return {
    log,
    handlers,
    attached,
    /** The socket reconnected: nothing is attached on the new one (client.ts:189-197). */
    reconnect: () => attached.clear(),
    state: { profile: atom('default'), connectionId: atom('local'), gateway: atom('open') },
    activeConnectionId: () => 'local',
    profileRoutes: async () => routes,
    retainProfile: async route => {
      log.push(['retain', route.connectionId])
      return () => log.push(['release', route.connectionId])
    },
    requestProfile: async (route, method, params) => {
      log.push(['rpc', method, structuredClone(params)])
      if (ATTACH_REQUIRED.has(method) && params && !attached.has(params.session_id)) {
        log.push(['implicit', 'session.resume', { session_id: params.session_id }])
        answer('session.resume', { session_id: params.session_id, defer_history: true, omit_messages: true })
      }
      return answer(method, params)
    },
    openSession: async (id, opts) => log.push(['open', id, opts]),
    notify: note => log.push(['notify', note]),
    navigate: path => log.push(['navigate', path])
  }
}

const FINAL = ['finished', 'refused', 'unknown']

/** The plugin API as the Desktop part sees it through ctx.rest: one run, claims and steps.
 *  `subject`: the principal subject the host authenticates this window's requests as. */
export function makeApi(run, { log = [], refuse = {}, mode, subject = null } = {}) {
  const state = { run: structuredClone(run), generation: run.claim ? run.claim.generation : 0, bodies: [] }
  const rest = async (path, opts) => {
    const body = opts && opts.body
    state.bodies.push(body)
    const parts = path.split('/').filter(Boolean)
    if (!opts) {
      log.push(['rest', path])
      const runs = path === '/runs/claimable' && (FINAL.includes(state.run.state) || state.run.claim.live) ? [] : [state.run]
      return { ok: true, profile_home: '/homes/default', runs: structuredClone(runs) }
    }
    const action = parts[2]
    log.push(['rest', action, action === 'step' ? body.step : undefined, structuredClone(body)])
    if (refuse[action]) return refuse[action]
    if (action === 'claim') {
      state.generation += 1
      state.run.claim = { owner: body.owner, generation: state.generation, live: true }
      return { ok: true, generation: state.generation, expires_at: 9e9, subject,
        mode: mode || (state.run.state === 'waiting_for_gui' ? 'start' : 'reconcile'), run: structuredClone(state.run) }
    }
    if (body.generation !== state.generation) return { ok: false, code: 'stale_generation' }
    if (action === 'renew') return { ok: true, generation: state.generation, expires_at: 9e9 }
    if (action === 'release') {
      state.run.claim = { ...state.run.claim, live: false }
      return { ok: true, released: true }
    }
    if (action === 'step') {
      if (refuse[`step:${body.step}`]) return refuse[`step:${body.step}`]
      const data = body.data || {}
      const r = state.run
      r.state = body.step
      if (body.step === 'create_requested') r.identity = { ...data.identity, subject } // the store takes it from the principal
      if (data.session_id) r.session_id = data.session_id
      if (data.admission_id) r.admission_id = data.admission_id
      if (Array.isArray(data.enabled_toolsets)) r.effective_toolsets = data.enabled_toolsets
      if (body.step === 'running') r.prompts = data.prompts || []
      if (['refused', 'unknown'].includes(body.step)) Object.assign(r, { reason: data.reason, detail: data.detail })
      if (body.step === 'finished') Object.assign(r, { outcome: data.outcome, result_preview: data.final_response })
      return { ok: true, run: structuredClone(r) }
    }
    return { ok: false, code: 'not_found' }
  }
  return { rest, state, log }
}

export function setup({ run = storedRun(), handlers, routes, refuse, mode, subject } = {}) {
  const log = []
  const host = makeHost({ routes, handlers: handlers || happyHandlers(), log })
  const api = makeApi(run, { log, refuse, mode, subject })
  return { log, host, api }
}

/** The log as compact strings: "rpc session.create", "rest step create_requested", "retain local". */
export const trace = log => log.map(([kind, a, b]) => (kind === 'rest' && a === 'step' ? `step ${b}` : kind === 'rpc' ? a : kind === 'rest' ? `rest ${a}` : `${kind}`))
