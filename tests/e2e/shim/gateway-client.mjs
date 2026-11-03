// Linux stand-in for the Desktop's canonical gateway client.
//
// Faithful to (Hermes ad2b705, apps/desktop/src/):
//   api/canonical-protocol.ts  openGatewaySocket (subprotocols), prepareCreate, preparePayload,
//                              preparePromptResponse, result/admissionReceipt/snapshotResult, event()
//   api/client.ts              request() flow: attachForRequest (auto session.resume before
//                              ATTACH_REQUIRED methods), clarify_batch flag, timeouts
//   shared/json-rpc-channel.ts `client.capabilities {server_requests:true}` after gateway.ready
//
// NOT implemented (throws or is ignored): session.mutate rewrites (title/branch/model/...), rewind
// splitting, legacy (non-canonical) contract, reconnect backoff, heartbeat, secondary sockets.
// The primary route of a local Desktop is the only route (docs/hermes-interfaces.md §1).
//
// Unlike the renderer, server->client requests are NOT answered here: the real app answers them in
// its native chat UI, the plugin SDK has no door to them. They are recorded in `serverRequests` and
// only the test's "user" side (window.mjs `user.*` commands) may settle them.

const CANONICAL_PROTOCOL = 'hermes-gateway-v1'
const ATTACH_REQUIRED = new Set(['prompt.submit', 'approval.respond', 'clarify.respond', 'session.interrupt',
  'prompt.cancel', 'session.rewind', 'prompt.steer_queued', 'prompt.replace_queued'])
const CREATE_ALLOWED = new Set(['request_id', 'source', 'cwd', 'model', 'provider', 'toolsets', 'reasoning', 'profile',
  'cols', 'title', 'hidden', 'follow_profile_config', 'service_tier', 'cwd_explicit'])
const CREATE_KEPT = new Set(['request_id', 'cwd', 'model', 'provider', 'toolsets', 'reasoning', 'title', 'hidden',
  'follow_profile_config', 'service_tier', 'cwd_explicit'])
const PROMPT_SUBMIT_TIMEOUT_MS = 1_800_000
const DEFAULT_TIMEOUT_MS = 30_000

const profileOf = p => (typeof p === 'string' && p ? p : 'default')
const skey = (sid, profile) => JSON.stringify([profileOf(profile), sid])
const uuid = () => crypto.randomUUID()

export class GatewayClient {
  constructor({ wsUrl, mintTicket, log }) {
    this.wsUrl = wsUrl
    this.mintTicket = mintTicket
    this.log = log
    this.ws = null
    this.nextId = 1
    this.pending = new Map()
    this.eventListeners = new Set()
    this.attached = new Set()
    this.generations = new Map()
    this.prompts = new Map()
    this.creates = new Map()
    this.clarifyBatch = false
    this.serverRequests = []
    this.rpcLog = []
    this.wireSeq = 0
    this.injections = []
    this.connectionGeneration = 0
    this.openedAt = 0
  }

  onEvent(fn) { this.eventListeners.add(fn); return () => this.eventListeners.delete(fn) }

  async connect() {
    this.connectionGeneration++
    this.attached.clear()
    this.prompts.clear()
    this.generations.clear()
    this.clarifyBatch = false
    // Interactive grant for the shared-primary route carries scope 'host' (local-gateway.ts ticketScope).
    const ticket = await this.mintTicket('interactive')
    const ws = new WebSocket(this.wsUrl, [CANONICAL_PROTOCOL, `hermes-gateway-ticket.${ticket}`])
    this.ws = ws
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', () => resolve(), { once: true })
      ws.addEventListener('error', e => reject(new Error(`gateway socket error: ${e.message ?? 'dial failed'}`)), { once: true })
    })
    if (ws.protocol !== CANONICAL_PROTOCOL) throw new Error(`gateway did not echo ${CANONICAL_PROTOCOL} (got "${ws.protocol}")`)
    const generation = this.connectionGeneration
    ws.addEventListener('message', ev => this.#frame(String(ev.data), generation))
    ws.addEventListener('close', () => {
      if (generation !== this.connectionGeneration) return
      for (const [, p] of this.pending) p.reject(new Error('Hermes gateway disconnected'))
      this.pending.clear()
    })
    this.openedAt = Date.now()
  }

  get open() { return this.ws?.readyState === WebSocket.OPEN }

  async close() {
    this.connectionGeneration++
    const ws = this.ws
    this.ws = null
    if (ws && ws.readyState <= WebSocket.OPEN) {
      await new Promise(resolve => { ws.addEventListener('close', () => resolve(), { once: true }); ws.close() })
    }
    for (const [, p] of this.pending) p.reject(new Error('Hermes gateway disconnected'))
    this.pending.clear()
  }

  #frame(text, generation) {
    if (generation !== this.connectionGeneration) return
    let frame
    try { frame = JSON.parse(text) } catch { return }
    if (frame.method === 'event' && frame.params && typeof frame.params.type === 'string') {
      if (frame.params.type === 'gateway.ready') this.#advertise()
      this.#event(frame.params)
      return
    }
    if (frame.id !== undefined && frame.method) {
      // Server -> client request. The renderer's app answers; here only the test's user side may.
      const rec = { id: frame.id, method: frame.method, params: frame.params, receivedAt: Date.now(), answered: false, cancelled: null }
      this.serverRequests.push(rec)
      return
    }
    const waiter = this.pending.get(frame.id)
    if (!waiter) return
    this.pending.delete(frame.id)
    if (waiter.lose) { waiter.resolve(undefined); return } // injected "lost reply": executed, answer swallowed
    if (frame.error) {
      waiter.reject(Object.assign(new Error(frame.error.message ?? 'gateway error'), { code: frame.error.code, data: frame.error.data }))
    } else waiter.resolve(frame.result)
  }

  // json-rpc-channel.ts advertiseCapabilities(): once per connection, on gateway.ready.
  #advertise() {
    this.#raw('client.capabilities', { server_requests: true }).then(r => {
      this.clarifyBatch = r?.canonical_clarify_batch === true
    }).catch(() => {})
  }

  #send(method, params, { lose = false } = {}) {
    const id = this.nextId++
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
    return { id, promise: new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, lose })) }
  }

  async #raw(method, params, timeoutMs = DEFAULT_TIMEOUT_MS, opts) {
    if (!this.open) throw new Error('Hermes gateway is not connected')
    const { id, promise } = this.#send(method, params, opts)
    let timer
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`request timed out: ${method}`)) }, timeoutMs)
    })
    try { return await Promise.race([promise, deadline]) } finally { clearTimeout(timer) }
  }

  // ---- fault injection (test side only) ------------------------------------------------------
  // mode 'lose_reply': the request reaches the gateway and is executed, the reply is swallowed and the
  //   caller sees a timeout-style error (a lost confirmation).
  // mode 'fail_before_send': the caller sees an error and nothing is sent.
  // mode 'merge_result': the reply is shallow-merged with `merge` before the caller sees it (e.g. a
  //   runtime.describe whose capabilities lack durable-admission-v1).
  // `prefix` ({param: string}): only calls whose params start with these strings count, e.g.
  //   {submission_id: 'vk-'} hits the plugin's child submits and leaves the test's own calls alone.
  inject(method, mode, times = 1, merge = null, prefix = null) { this.injections.push({ method, mode, times, merge, prefix }) }

  #takeInjection(method, params) {
    const hits = x => !x.prefix || Object.entries(x.prefix).every(([k, v]) => typeof params?.[k] === 'string' && params[k].startsWith(v))
    const i = this.injections.find(x => x.method === method && x.times > 0 && hits(x))
    if (i) i.times--
    return i
  }

  // ---- client.ts request() -------------------------------------------------------------------
  async request(method, params = {}, timeoutMs) {
    const entry = { seq: this.rpcLog.length, method, params_in: structuredClone(params), at: Date.now() }
    this.rpcLog.push(entry)
    try {
      const out = await this.#request(method, params, timeoutMs, entry)
      entry.ok = true
      return out
    } catch (e) {
      entry.ok = false
      entry.error = { message: e.message, code: e.code, reason: e.data?.reason }
      throw e
    }
  }

  async #request(method, params, timeoutMs, entry) {
    const generation = this.connectionGeneration
    const inj = this.#takeInjection(method, params)
    if (inj?.mode === 'fail_before_send') { entry.injected = inj.mode; throw new Error(`injected failure before send: ${method}`) }

    await this.#attachForRequest(method, params)
    if (generation !== this.connectionGeneration) throw new Error('Hermes gateway connection changed; reopen the session')

    if (method === 'prompt.submit' && this.clarifyBatch) params = { ...params, clarify_batch: true }
    const prepared = this.#prepare(method, params)
    entry.params_wire = structuredClone(prepared)
    entry.wire_seq = ++this.wireSeq
    const t = timeoutMs ?? (method === 'prompt.submit' ? PROMPT_SUBMIT_TIMEOUT_MS : DEFAULT_TIMEOUT_MS)
    if (inj?.mode === 'lose_reply') {
      entry.injected = inj.mode
      // Send, wait for the gateway to answer (so it has executed), discard the reply, then time out.
      await this.#raw(method, prepared, t, { lose: true }).catch(() => {})
      throw new Error(`request timed out: ${method}`)
    }
    const result = await this.#raw(method, prepared, t)
    if (generation !== this.connectionGeneration) throw new Error('Hermes gateway connection changed; reopen the session')
    const patched = inj?.mode === 'merge_result' ? { ...result, ...inj.merge } : result
    if (inj?.mode === 'merge_result') entry.injected = inj.mode
    const settled = this.#result(method, prepared, patched)
    if (settled && typeof settled.session_id === 'string') entry.result_session_id = settled.session_id
    if (['session.resume', 'session.create'].includes(method)) this.#adopt(settled, prepared.profile)
    return settled
  }

  async #attachForRequest(method, params) {
    const sid = typeof params.session_id === 'string' ? params.session_id : null
    if (sid && ATTACH_REQUIRED.has(method) && !this.attached.has(skey(sid, params.profile))) {
      await this.request('session.resume', { session_id: sid, defer_history: true, omit_messages: true,
        ...(params.profile ? { profile: params.profile } : {}) })
    }
  }

  #adopt(settled, profile) {
    if (settled?.session_id) this.attached.add(skey(settled.session_id, profile))
  }

  // ---- canonical-protocol.ts -----------------------------------------------------------------
  #prepare(method, params) {
    let prepared
    if (method === 'session.create') prepared = this.#prepareCreate(params)
    else if (method === 'session.interrupt') {
      const generation = params.execution_generation ?? this.generations.get(skey(params.session_id, params.profile))
      if (typeof generation !== 'number') throw new Error('Session execution identity unavailable; reconnect before controlling this turn')
      prepared = { ...params, execution_generation: generation }
    } else if (method === 'approval.respond' || method === 'clarify.respond') prepared = this.#preparePromptResponse(method, params)
    else if (method === 'prompt.submit' && !params.submission_id && !params.input_id) prepared = { ...params, submission_id: uuid() }
    else prepared = params
    return method !== 'session.create' && params.profile !== undefined ? { ...prepared, profile: params.profile } : prepared
  }

  #prepareCreate(input) {
    const { reasoning_effort: effort, fast, service_tier: picked, ...params } = input
    const tier = typeof picked === 'string' && picked ? picked : fast === true ? 'priority' : fast === false ? 'normal' : undefined
    if (typeof effort === 'string' && effort) params.reasoning = effort
    if (tier) params.service_tier = tier
    if (typeof params.cwd_explicit !== 'boolean') delete params.cwd_explicit
    const unsupported = Object.keys(params).filter(k => !CREATE_ALLOWED.has(k))
    if (unsupported.length) throw new Error(`Canonical gateway does not support explicit session options: ${unsupported.join(', ')}`)
    const result = Object.fromEntries(Object.entries(params).filter(([k, v]) =>
      CREATE_KEPT.has(k) || (k === 'profile' && typeof v === 'string' && v !== '')))
    const key = JSON.stringify(result)
    const requestId = params.request_id ?? this.creates.get(key) ?? uuid()
    this.creates.set(key, String(requestId))
    // source is REPLACED by 'gui' (canonical-protocol.ts:540-548).
    return { ...result, request_id: requestId, source: 'gui' }
  }

  #preparePromptResponse(method, params) {
    const id = String(params.prompt_id ?? params.request_id ?? '')
    const prompt = this.prompts.get(skey(id, params.profile))
    const sessionId = params.session_id ?? prompt?.session_id
    if (!prompt || prompt.session_id !== sessionId || prompt.profile !== profileOf(params.profile)
        || prompt.execution_generation !== this.generations.get(skey(sessionId, params.profile))) {
      throw new Error('Prompt is stale or unavailable; reconnect before responding')
    }
    const field = method === 'approval.respond' ? 'choice' : 'answer'
    return { session_id: sessionId, execution_generation: prompt.execution_generation, prompt_id: id, [field]: params[field] }
  }

  #result(method, params, value) {
    if (!value || typeof value !== 'object') return value
    if (method === 'prompt.submit') {
      if (value.voice_stopped === true && value.admission_id === undefined && value.ref === undefined) {
        return { voice_stopped: true, session_id: params.session_id, submission_id: params.submission_id ?? params.input_id }
      }
      if (value.ref?.session_id !== params.session_id || typeof value.admission_id !== 'string') {
        throw new Error('Canonical admission receipt destination mismatch')
      }
      return { ...value, session_id: value.ref.session_id, submission_id: params.submission_id ?? params.input_id }
    }
    if (method === 'prompt.receipt' || method === 'prompt.cancel') {
      if (value.ref && (value.ref.session_id !== params.session_id || value.admission_id !== params.admission_id)) {
        throw new Error('Canonical admission receipt destination mismatch')
      }
      return value.ref ? { ...value, session_id: value.ref.session_id } : value
    }
    if (method === 'session.create') {
      for (const [k, id] of this.creates) if (id === params.request_id) this.creates.delete(k)
    }
    if (method === 'session.resume' || method === 'session.create') return this.#snapshotResult(value, params.profile)
    if (method === 'session.events.since') for (const ev of value.events ?? []) this.#track({ ...ev, profile: profileOf(params.profile) })
    return value
  }

  #snapshotResult(value, route) {
    const sid = value.session_id
    const profile = profileOf(route)
    this.#track({ type: 'session.info', session_id: sid, profile, payload: value })
    const prompts = (value.prompts ?? []).map(p => {
      const projected = { ...p, request_id: p.prompt_id }
      this.#track({ type: `${p.kind}.request`, session_id: sid, profile, payload: projected })
      return projected
    })
    const open_requests = [...(value.open_requests ?? []), ...prompts.map(p => ({ id: p.prompt_id, method: p.kind }))]
    return { ...value, open_requests,
      pending_approval: prompts.find(p => p.kind === 'approval'), pending_clarify: prompts.find(p => p.kind === 'clarify'),
      info: { ...value.info, stored_session_id: value.stored_session_id, pending_submissions: value.pending_submissions,
        replay_epoch: value.replay_epoch, last_sequence: value.last_sequence, execution_generation: value.execution_generation, running: value.running } }
  }

  // event(): generation fence + prompt registry
  #track(event) {
    const payload = event.payload
    if (!payload || !event.session_id) return
    const profile = profileOf(event.profile)
    const owner = skey(event.session_id, profile)
    const generation = event.execution_generation ?? payload.execution_generation
    if (typeof generation === 'number') {
      const current = this.generations.get(owner) ?? -1
      if (generation < current) return
      this.generations.set(owner, generation)
    }
    if (typeof payload.prompt_id === 'string') {
      if (event.type.endsWith('.settled')) { this.prompts.delete(skey(payload.prompt_id, profile)); return }
      if (event.type === 'approval.request' || event.type === 'clarify.request') {
        payload.request_id = payload.prompt_id
        this.prompts.set(skey(payload.prompt_id, profile), { ...payload, session_id: event.session_id, profile })
      }
    }
  }

  #event(params) {
    this.#track(params)
    if (params.type === 'request.cancel' && params.payload?.id) {
      for (const r of this.serverRequests) if (r.id === params.payload.id) r.cancelled = params.payload.reason ?? true
    }
    for (const fn of this.eventListeners) {
      try { fn(params) } catch (e) { this.log('event listener failed', e.message) }
    }
  }

  // ---- user-side only (never reachable from plugin code) -------------------------------------
  respondServerRequest(id, result) {
    const rec = this.serverRequests.find(r => r.id === id)
    if (!rec) throw new Error(`no server request ${id}`)
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', id, result }))
    rec.answered = true
  }
}
