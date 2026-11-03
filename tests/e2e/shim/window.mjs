// Linux stand-in for ONE Hermes Desktop window running ONE Desktop plugin (plugin.js).
//
//   node window.mjs <config.json>      control protocol: JSON lines on stdin -> JSON lines on stdout
//
// THIS IS NOT THE DESKTOP. Real: the backend (Hermes gateway, canonical session control,
// plugin API with native-http auth), the wire protocol, the ticket minting over the gateway's
// control socket (mirrors electron/local-gateway.ts mintLocalGatewayTicket), the client-side rewrites
// of canonical-protocol.ts (see gateway-client.mjs), plugin.js loaded as an ES module with only
// @hermes/plugin-sdk / react / react/jsx-runtime resolvable.
//
// Faked or missing (a test built on this is a "Linux stand-in", never "Desktop tested"):
//   - no Electron, no Chromium: no renderer, no React rendering (react is a descriptor stub), no
//     timer throttling in hidden windows (ctx.setInterval is manual by default: `tick`), no
//     Origin/CORS behaviour, no preload bridge (window.hermesDesktop), no IPC grant minting
//     (the shim mints grants itself, same control socket, same purposes).
//   - ONE connection ('local'), ONE profile ('default'), primary route only. host.profileRoutes()
//     returns that single descriptor; other routes, ctx.socket, ctx.os/pet/files/i18n/preview/
//     runAction/listActions throw "not implemented by the shim". Contributions are recorded, never rendered.
//   - host.openSession only RECORDS the call (no tab opens, no hydration).
//   - ctx.rest: Node fetch with X-Hermes-Gateway-Ticket (purpose native-http, minted per call, like
//     nativeGatewayHttpHeaders). Error shape is approximated: Error with .status and .body.
//     `?profile=` is not appended (the default profile is the launch profile).
//   - the app's own handling of approval / clarify cards does not exist: server requests are recorded
//     in `serverRequests`; only the `user.*` commands (test side) settle them.
//   - no window lifecycle other than start/stop; "two windows" = two shim processes.
//
// Control commands (Python: vk_e2e/desktop.py):
//   state | events {since} | tick {id?} | rpc {method, params} | rest {path, opts} | reconnect |
//   inject {method, mode, times} | user.respondServerRequest {id, result} | close
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import readline from 'node:readline'
import { pathToFileURL } from 'node:url'

import { GatewayClient } from './gateway-client.mjs'
import { installHooks } from './register.mjs'

const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
const { home, endpoint, pluginId, pluginFile, pinnedRoot } = config
const intervalMode = config.intervalMode ?? 'manual'
const logStream = config.logFile ? fs.createWriteStream(config.logFile, { flags: 'a' }) : null
const log = (...a) => logStream?.write(`[${new Date().toISOString()}] ${a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' ')}\n`)

// ---- ticket minting: electron/local-gateway.ts mintLocalGatewayTicket (POSIX path) -------------
function controlSocketPath() {
  const direct = path.join(home, 'gateway.sock')
  if (fs.existsSync(direct)) return direct
  return fs.readFileSync(path.join(home, 'gateway.sock.path'), 'utf8').trim()
}

function mintTicket(purpose) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(controlSocketPath())
    let buf = ''
    const deadline = setTimeout(() => socket.destroy(new Error('Gateway ticket deadline')), 5000)
    socket.on('error', () => reject(new Error('Gateway ticket bootstrap failed')))
    socket.on('close', () => { clearTimeout(deadline); reject(new Error('Gateway ticket connection closed')) })
    socket.on('connect', () => socket.write(JSON.stringify({ protocol: 1, id: 1, verb: 'session-ticket',
      params: { profile_id: endpoint.profile_id, instance_id: endpoint.instance_id, purpose, ...(purpose === 'interactive' ? { scope: 'host' } : {}) } }) + '\n'))
    socket.on('data', chunk => {
      buf += chunk.toString()
      if (!buf.includes('\n')) return
      try {
        const reply = JSON.parse(buf.split('\n')[0])
        const grant = reply.result
        if (!reply.ok || reply.protocol !== 1 || reply.id !== 1 || grant?.instance_id !== endpoint.instance_id
            || grant?.profile_id !== endpoint.profile_id || typeof grant?.ticket !== 'string') throw new Error('Invalid gateway ticket')
        resolve(grant.ticket)
      } catch { reject(new Error('Invalid gateway ticket response')) }
      socket.destroy()
    })
  })
}

// ---- shim state ---------------------------------------------------------------------------------
const route = { connectionId: 'local', mode: 'local', primary: true, profile: config.profile ?? 'default', targetProfile: config.profile ?? 'default' }
const rec = {
  registrations: [], settingsPages: [], disposers: 0, openSessionCalls: [], restCalls: [], intervals: [], timeouts: [],
  eventLog: [], errors: [], stubUses: new Map(), loaded: false, registerError: null, retainCalls: 0, releaseCalls: 0, navigations: [], notifications: [],
}
let eventSeq = 0
const listeners = new Map()
const client = new GatewayClient({
  wsUrl: endpoint.api_origin.replace(/^http/, 'ws') + '/api/ws', mintTicket, log,
})
client.onEvent(params => {
  rec.eventLog.push({ seq: ++eventSeq, at: Date.now(), type: params.type, session_id: params.session_id, payload: params.payload })
  const event = { ...params }
  for (const type of [params.type, '*']) {
    for (const fn of listeners.get(type) ?? []) {
      try { fn(event) } catch (e) { rec.errors.push({ where: `onEvent:${type}`, message: String(e?.stack ?? e) }) }
    }
  }
})

function addListener(type, fn) {
  const set = listeners.get(type) ?? new Set()
  set.add(fn)
  listeners.set(type, set)
  return () => { set.delete(fn); if (!set.size) listeners.delete(type) }
}

const notImplemented = what => () => { throw new Error(`shim: ${what} is not implemented by the Linux stand-in`) }

function checkRoute(r) {
  if (typeof r === 'string') throw new Error(`shim: the string-route overload is not used by Verksted; pass a descriptor from host.profileRoutes()`)
  if (!r?.connectionId?.trim?.() || !r?.profile?.trim?.() || !r?.targetProfile?.trim?.()) {
    throw new Error('Profile route must include connectionId, profile, and targetProfile') // profile-request.ts
  }
  if (r.connectionId !== route.connectionId || r.profile !== route.profile || r.targetProfile !== route.targetProfile) {
    throw new Error(`shim: only the primary route ${JSON.stringify(route)} exists; got ${JSON.stringify(r)}`)
  }
}

// ---- host (sdk/index.ts `host`) -------------------------------------------------------------------
function atom(value) {
  const subs = new Set()
  return { get: () => value, listen: fn => { subs.add(fn); return () => subs.delete(fn) }, _set: v => { value = v; subs.forEach(f => f(v)) } }
}

const host = new Proxy({
  profileRoutes: async () => [{ ...route }],
  requestProfile: async (r, method, params = {}, timeoutMs, _options) => { checkRoute(r); return client.request(method, params, timeoutMs) },
  retainProfile: async (r, _options) => { checkRoute(r); rec.retainCalls++; let done = false; return () => { if (!done) { done = true; rec.releaseCalls++ } } },
  request: async (method, params = {}, timeoutMs) => client.request(method, params, timeoutMs),
  onEvent: (type, fn) => addListener(type, fn),
  // sdk/index.ts host.state: readonly atoms {get, listen}. Values of a healthy single local window.
  state: {
    gateway: atom('open'), profile: atom(config.profile ?? 'default'), connectionId: atom('local'),
    activeSessionId: atom(null), focusedSessionId: atom(null), cwd: atom(''), model: atom('local-wire-stub'),
  },
  activeConnectionId: () => 'local',
  navigate: path => { rec.navigations.push(path) },
  notify: n => { rec.notifications.push(n) },
  openSession: async (storedSessionId, options = {}) => { rec.openSessionCalls.push({ storedSessionId, options: structuredClone(options), at: Date.now() }) },
}, {
  get(target, prop) {
    if (prop in target) return target[prop]
    if (typeof prop === 'symbol' || prop === 'then') return undefined
    throw new Error(`shim: host.${String(prop)} is not implemented by the Linux stand-in (add it to window.mjs, faithfully to apps/desktop/src/sdk/index.ts)`)
  },
})

// ---- ctx (contrib/plugin.ts createPluginContext) ----------------------------------------------------
function pathSuffix(p) {
  const suffix = p.startsWith('/') ? p : `/${p}`
  if (suffix.split(/[?#]/, 1)[0].split('/').includes('..')) throw new Error(`pluginRest: illegal path traversal in "${p}"`)
  return suffix
}

async function pluginRest(p, opts = {}) {
  const suffix = pathSuffix(p)
  const method = (opts.method ?? 'GET').toUpperCase()
  const url = `${endpoint.api_origin}/api/plugins/${pluginId}${suffix}`
  const call = { path: p, method, body: opts.body, at: Date.now() }
  rec.restCalls.push(call)
  const ticket = await mintTicket('native-http')
  const headers = { 'X-Hermes-Gateway-Ticket': ticket }
  let body
  if (opts.body !== undefined && method !== 'GET') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body) }
  const res = await fetch(url, { method, headers, body, signal: AbortSignal.timeout(opts.timeoutMs ?? 15_000) })
  const text = await res.text()
  call.status = res.status
  if (!res.ok) throw Object.assign(new Error(`${res.status} ${res.statusText}: ${text.slice(0, 300)}`), { status: res.status, body: text })
  try { return JSON.parse(text) } catch { return text }
}

const storageData = new Map()
function makeCtx() {
  const track = d => { rec.disposers++; return d }
  const nI = notImplemented
  return {
    source: `plugin:${pluginId}`,
    register: c => { rec.registrations.push({ ...c, id: `${pluginId}:${c.id}`, source: `plugin:${pluginId}` }); return track(() => {}) },
    registerMany: cs => { for (const c of cs) rec.registrations.push({ ...c, id: `${pluginId}:${c.id}`, source: `plugin:${pluginId}` }); return track(() => {}) },
    registerSettingsPage: page => { rec.settingsPages.push(page); return track(() => {}) },
    onDispose: fn => { track(fn) },
    onEvent: (type, fn) => track(addListener(type, fn)),
    setTimeout: (fn, ms) => { const t = globalThis.setTimeout(() => runGuarded('setTimeout', fn), ms); rec.timeouts.push(ms); return track(() => clearTimeout(t)) },
    setInterval: (fn, ms) => {
      const id = rec.intervals.length
      const entry = { id, ms, fn, runs: 0, active: true, timer: null }
      rec.intervals.push(entry)
      if (intervalMode === 'real') entry.timer = globalThis.setInterval(() => { entry.runs++; runGuarded(`interval#${id}`, fn) }, ms)
      return track(() => { entry.active = false; if (entry.timer) clearInterval(entry.timer) })
    },
    addEventListener: nI('ctx.addEventListener'),
    rest: pluginRest,
    socket: nI('ctx.socket'), os: nI('ctx.os'), pet: nI('ctx.pet'), runAction: nI('ctx.runAction'), listActions: nI('ctx.listActions'),
    preview: nI('ctx.preview'), files: nI('ctx.files'), i18n: nI('ctx.i18n'),
    storage: { get: (k, fallback) => storageData.has(k) ? JSON.parse(storageData.get(k)) : fallback, set: (k, v) => void storageData.set(k, JSON.stringify(v)), remove: k => void storageData.delete(k) },
  }
}

async function runGuarded(where, fn) {
  try { await fn() } catch (e) { rec.errors.push({ where, message: String(e?.stack ?? e) }) }
}

// ---- sdk names: whatever the plugin imports, checked against the pinned barrel ------------------------
function importedSdkNames(source) {
  const names = new Set()
  for (const m of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]@hermes\/plugin-sdk['"]/g)) {
    for (const part of m[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/)[0].trim()
      if (name) names.add(name)
    }
  }
  // `import * as sdk from '@hermes/plugin-sdk'`: every `sdk.NAME` the file touches
  for (const m of source.matchAll(/import\s*\*\s*as\s+(\w+)\s+from\s*['"]@hermes\/plugin-sdk['"]/g)) {
    for (const use of source.matchAll(new RegExp(`\\b${m[1]}\\.(\\w+)`, 'g'))) names.add(use[1])
  }
  return [...names]
}

function assertExistsAtPinned(names) {
  if (!pinnedRoot) return
  const sdkDir = path.join(pinnedRoot, 'apps/desktop/src/sdk')
  const text = ['index.ts', 'areas.ts', 'plugin-contract.ts', 'composer.ts', 'sessions.ts', 'runtime.ts', 'i18n.ts', 'settings.ts', 'viewport.ts']
    .map(f => { try { return fs.readFileSync(path.join(sdkDir, f), 'utf8') } catch { return '' } }).join('\n')
  const missing = names.filter(n => !new RegExp(`\\b${n}\\b`).test(text))
  if (missing.length) throw new Error(`plugin.js imports names not found in the pinned @hermes/plugin-sdk sources: ${missing.join(', ')}`)
}

function stubFor(name) {
  const make = () => new Proxy(function () {}, {
    get: (_t, p) => p === Symbol.toPrimitive ? () => name : p === 'toString' ? () => name : typeof p === 'symbol' ? undefined : make(),
    apply: () => { rec.stubUses.set(name, (rec.stubUses.get(name) ?? 0) + 1); return undefined },
  })
  return make()
}

// ---- boot -----------------------------------------------------------------------------------------------
async function boot() {
  const source = fs.readFileSync(pluginFile, 'utf8')
  const names = importedSdkNames(source)
  assertExistsAtPinned(names)
  globalThis.__vkShim = { sdk: Object.fromEntries(names.map(n => [n, n === 'host' ? host : stubFor(n)])) }
  const pluginUrl = pathToFileURL(path.resolve(pluginFile)).href
  installHooks({ pluginUrl, sdkNames: names })
  await client.connect()
  const mod = await import(pluginUrl)
  const plugin = mod.default
  if (!plugin || typeof plugin.register !== 'function' || typeof plugin.id !== 'string') throw new Error('plugin.js must default-export { id, register(ctx) }')
  if (plugin.id !== pluginId) throw new Error(`plugin id "${plugin.id}" must equal its folder name "${pluginId}"`)
  rec.plugin = { id: plugin.id, name: plugin.name, defaultEnabled: plugin.defaultEnabled, capabilities: plugin.capabilities }
  globalThis.__vkCtx = makeCtx()
  await plugin.register(globalThis.__vkCtx) // the real loader also bounds this at 10 s
  rec.loaded = true
}

const summary = () => ({
  loaded: rec.loaded, registerError: rec.registerError, plugin: rec.plugin, connected: client.open, clarifyBatch: client.clarifyBatch,
  registrations: rec.registrations, settingsPages: rec.settingsPages, disposers: rec.disposers,
  openSessionCalls: rec.openSessionCalls, restCalls: rec.restCalls, retainCalls: rec.retainCalls, releaseCalls: rec.releaseCalls,
  intervals: rec.intervals.map(i => ({ id: i.id, ms: i.ms, runs: i.runs, active: i.active })), timeouts: rec.timeouts,
  navigations: rec.navigations, notifications: rec.notifications, rpcLog: client.rpcLog, serverRequests: client.serverRequests, errors: rec.errors,
  stubUses: Object.fromEntries(rec.stubUses), lastEventSeq: eventSeq,
  prompts: [...client.prompts.values()],
  storage: Object.fromEntries([...storageData].map(([k, v]) => [k, JSON.parse(v)])),
})

// ---- minimal renderer over the react stub's descriptors (NOT React: function components are called once,
// hooks are inert, useSyncExternalStore returns the current snapshot). Enough to read labels and click
// buttons of a plugin written with createElement.
function expand(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return null
  if (Array.isArray(node)) return node.map(expand).flat().filter(x => x !== null)
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (node.$$typeof !== 'vk-shim-element') return null
  if (typeof node.type === 'function') return expand(node.type({ ...node.props, children: node.children }))
  return { tag: String(node.type), props: node.props, children: expand(node.children) ?? [] }
}
const textOf = n => typeof n === 'string' ? n : (n?.children ?? []).map(textOf).join(' ')
const flat = (n, out = []) => { if (n && typeof n !== 'string') { out.push(n); n.children.forEach(c => flat(c, out)) } return out }
const contribution = id => {
  const c = rec.registrations.find(r => r.id === `${pluginId}:${id}`)
  if (!c || typeof c.render !== 'function') throw new Error(`no renderable contribution ${id}`)
  return c
}

const commands = {
  'ui.render': async ({ id }) => { const t = expand(contribution(id).render()); return { text: textOf(t ?? ''), buttons: flat(t).filter(n => n.tag === 'button').map(textOf) } },
  'ui.click': async ({ id, text, nth = 0 }) => {
    const t = expand(contribution(id).render())
    const hits = flat(t).filter(n => n.tag === 'button' && textOf(n).includes(text))
    if (!hits[nth]) throw new Error(`no button "${text}" in ${id}`)
    await hits[nth].props.onClick()
    return true
  },
  state: async () => summary(),
  events: async ({ since = 0 }) => rec.eventLog.filter(e => e.seq > since),
  tick: async ({ id } = {}) => {
    let n = 0
    for (const i of rec.intervals) {
      if (!i.active || (id !== undefined && i.id !== id)) continue
      i.runs++; n++
      await runGuarded(`interval#${i.id}`, i.fn)
    }
    return { fired: n, errors: rec.errors }
  },
  rpc: async ({ method, params, timeoutMs }) => client.request(method, params ?? {}, timeoutMs), // test-side direct call, same client
  rest: async ({ path: p, opts }) => pluginRest(p, opts ?? {}),
  inject: async ({ method, mode, times, merge }) => { client.inject(method, mode, times ?? 1, merge ?? null); return true },
  reconnect: async () => { await client.close(); await client.connect(); return true },
  disconnect: async () => { await client.close(); return true },
  'user.respondServerRequest': async ({ id, result }) => { client.respondServerRequest(id, result); return true },
  close: async () => { for (const i of rec.intervals) if (i.timer) clearInterval(i.timer); await client.close(); setImmediate(() => process.exit(0)); return true },
}

function reply(obj) { process.stdout.write(JSON.stringify(obj) + '\n') }

process.on('unhandledRejection', e => { rec.errors.push({ where: 'unhandledRejection', message: String(e?.stack ?? e) }) })

try {
  await boot()
  reply({ ready: true })
} catch (e) {
  rec.registerError = String(e?.stack ?? e)
  log('boot failed', rec.registerError)
  reply({ ready: false, error: rec.registerError })
}

const rl = readline.createInterface({ input: process.stdin })
rl.on('line', async line => {
  let msg
  try { msg = JSON.parse(line) } catch { return }
  const fn = commands[msg.cmd]
  if (!fn) return reply({ id: msg.id, ok: false, error: { message: `unknown command ${msg.cmd}` } })
  try { reply({ id: msg.id, ok: true, result: await fn(msg.args ?? {}) }) }
  catch (e) { reply({ id: msg.id, ok: false, error: { message: e.message, code: e.code, data: e.data, status: e.status } }) }
})
rl.on('close', async () => { try { await client.close() } catch {} process.exit(0) })
