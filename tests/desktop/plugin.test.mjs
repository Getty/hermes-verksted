// The Desktop part against a fake SDK host and a fake plugin API: start order, the toolset check,
// the describe gate, repeats only under the same identity, tracking, and the UI wiring.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { plugin, source } from './load-plugin.mjs'
import {
  DESCRIBE, IDENTITY, ROUTE, RpcError, TransportError, happyHandlers, info, setup, storedRun, trace
} from './fakes.mjs'

const { createWorker, checkInfo, guardCall, pickRoute, pendingPrompts, labelOf, GuardError } = plugin

const worker = ({ host, api }, extra = {}) =>
  createWorker({ host, rest: api.rest, owner: 'win-a', sleep: async () => {}, ...extra })
const calls = (log, method) => log.filter(([kind, m]) => kind === 'rpc' && m === method).map(([, , params]) => params)
const steps = log => log.filter(([kind, action]) => kind === 'rest' && action === 'step').map(([, , name, body]) => ({ name, data: body.data }))
const lastStep = log => steps(log).at(-1)
const ticks = async (n = 5) => { for (let i = 0; i < n; i++) await new Promise(resolve => setImmediate(resolve)) }

test('a start runs describe, claim, then create → check → submit on one retained route', async () => {
  const env = setup()
  await worker(env).poll()
  assert.deepEqual(trace(env.log), [
    'rest /runs/claimable', 'runtime.describe', 'rest claim',
    'retain', 'step create_requested', 'session.create', 'step session_created', 'tools.show',
    'step toolsets_checked', 'session.info', 'step submit_requested', 'prompt.submit', 'step running', 'release',
    'prompt.receipt', 'step running', 'rest /runs'
  ])
  const run = storedRun()
  assert.deepEqual(calls(env.log, 'session.create'), [run.create_params]) // exactly as stored
  assert.deepEqual(calls(env.log, 'prompt.submit'), [{ session_id: 's-1', text: run.prompt_text, submission_id: run.submission_id }])
  const claim = env.log.find(([kind, action]) => kind === 'rest' && action === 'claim')[3]
  assert.deepEqual(claim, { owner: 'win-a', lease_s: 180, profile_id: DESCRIBE.profile_id })
  assert.deepEqual(steps(env.log)[0].data.identity, IDENTITY)
  assert.equal(steps(env.log).find(s => s.name === 'running').data.admission_id, 'adm-1')
  assert.equal(env.api.state.run.state, 'running')
  // The host rejects native requests whose body names a profile other than the grant's.
  assert.ok(env.api.state.bodies.every(body => !body || !Object.hasOwn(body, 'profile')))
})

test('its own toolset is tolerated in the effective set', async () => {
  const env = setup({ handlers: happyHandlers(['file', 'terminal', 'verksted']) })
  await worker(env).poll()
  assert.equal(env.api.state.run.state, 'running')
})

for (const [name, change, reason] of [
  ['an extra toolset', h => { h['session.create'].info = info(['file', 'terminal', 'web']) }, 'toolsets_exceeded'],
  ['a missing toolset', h => { h['session.create'].info = info(['file']) }, 'toolsets_not_checkable'],
  ['an agent that already exists (lazy false)', h => { h['session.create'].info = info(['file', 'terminal'], { lazy: false }) }, 'toolsets_not_checkable'],
  ['another protocol', h => { h['session.create'].info = { ...info(['file', 'terminal']), desktop_protocol: 'tui-v6' } }, 'toolsets_not_checkable'],
  ['a failing tools.show', h => { h['tools.show'] = new RpcError(4003, 'no') }, 'toolsets_not_checkable'],
  ['a tools.show without total', h => { h['tools.show'] = { sections: [] } }, 'toolsets_not_checkable'],
  ['a set that grew before submit', h => { h['session.info'] = info(['file', 'terminal', 'web']) }, 'toolsets_exceeded']
]) {
  test(`${name} refuses the start before any prompt`, async () => {
    const handlers = happyHandlers()
    change(handlers)
    const env = setup({ handlers })
    await worker(env).poll()
    assert.equal(calls(env.log, 'prompt.submit').length, 0)
    assert.equal(lastStep(env.log).name, 'refused')
    assert.equal(lastStep(env.log).data.reason, reason)
    assert.equal(env.api.state.run.state, 'refused')
    const t = trace(env.log)
    assert.ok(t.indexOf('release') > t.indexOf('step refused'), 'the retained route is released')
    assert.ok(t.includes('rest release'), 'the claim is given up')
  })
}

test('the refusal names what exceeded', async () => {
  const handlers = happyHandlers()
  handlers['session.create'].info = info(['file', 'terminal', 'web', 'browser'])
  const env = setup({ handlers })
  await worker(env).poll()
  assert.deepEqual(lastStep(env.log).data.extra, ['browser', 'web'])
})

test('the default case records the effective set and refuses only when it grows', async () => {
  const run = storedRun({ expected_toolsets: null, create_params: { ...storedRun().create_params, toolsets: undefined } })
  delete run.create_params.toolsets
  const handlers = happyHandlers(['file', 'terminal', 'web'])
  const env = setup({ run, handlers })
  await worker(env).poll()
  assert.equal(env.api.state.run.state, 'running')
  assert.deepEqual(env.api.state.run.effective_toolsets, ['file', 'terminal', 'web'])

  const grown = happyHandlers(['file', 'terminal', 'web'])
  grown['session.info'] = info(['file', 'terminal', 'web', 'cronjob'])
  const env2 = setup({ run, handlers: grown })
  await worker(env2).poll()
  assert.equal(lastStep(env2.log).data.reason, 'toolsets_exceeded')
  assert.deepEqual(lastStep(env2.log).data.extra, ['cronjob'])
})

test('an empty selection needs tools.show to list no tool', async () => {
  const run = storedRun({ expected_toolsets: [], create_params: { ...storedRun().create_params, toolsets: ['no_mcp'] } })
  const handlers = happyHandlers([])
  const env = setup({ run, handlers })
  await worker(env).poll()
  assert.equal(lastStep(env.log).data.reason, 'toolsets_not_checkable')

  const empty = happyHandlers([])
  empty['tools.show'] = { sections: [], total: 0 }
  const env2 = setup({ run, handlers: empty })
  await worker(env2).poll()
  assert.equal(env2.api.state.run.state, 'running')
})

test('a legacy route (no runtime.describe) is refused without touching Hermes', async () => {
  const handlers = happyHandlers()
  handlers['runtime.describe'] = new RpcError(-32601, 'Method not found')
  const env = setup({ handlers })
  await worker(env).poll()
  const claim = env.log.find(([kind, action]) => kind === 'rest' && action === 'claim')[3]
  assert.equal(claim.profile_id, null)
  assert.equal(lastStep(env.log).name, 'refused')
  assert.equal(lastStep(env.log).data.reason, 'legacy_route')
  assert.deepEqual(trace(env.log).filter(t => !t.startsWith('rest') && !t.startsWith('step')), ['runtime.describe'])
})

test('a route without durable admission is refused as legacy', async () => {
  const handlers = happyHandlers()
  handlers['runtime.describe'] = { ...DESCRIBE, capabilities: ['something-else'] }
  const env = setup({ handlers })
  await worker(env).poll()
  assert.equal(lastStep(env.log).data.reason, 'legacy_route')
  assert.equal(calls(env.log, 'session.create').length, 0)
})

test('no reply to runtime.describe claims nothing; the next poll tries again', async () => {
  const handlers = happyHandlers()
  handlers['runtime.describe'] = new TransportError('socket closed')
  const env = setup({ handlers })
  await worker(env).poll()
  assert.ok(!trace(env.log).includes('rest claim'))
})

test('a start requested under another identity is marked unknown, never re-created', async () => {
  for (const state of ['create_requested', 'session_created', 'submit_requested']) {
    const run = storedRun({ state, session_id: state === 'create_requested' ? null : 's-1',
      identity: { ...IDENTITY, connection_id: 'remote-1' } })
    const env = setup({ run })
    await worker(env).poll()
    assert.equal(lastStep(env.log).name, 'unknown', state)
    assert.equal(lastStep(env.log).data.reason, 'identity_changed')
    for (const method of ['session.create', 'session.resume', 'prompt.submit']) {
      assert.equal(calls(env.log, method).length, 0, `${state}: ${method}`)
    }
  }
})

test('the same identity repeats the identical session.create after a lost confirmation', async () => {
  const run = storedRun({ state: 'create_requested', identity: { ...IDENTITY } })
  const env = setup({ run })
  await worker(env).poll()
  assert.deepEqual(calls(env.log, 'session.create'), [run.create_params])
  assert.equal(env.api.state.run.state, 'running')
  assert.ok(!steps(env.log).some(s => s.name === 'create_requested'), 'the start is not requested twice')
})

test('a gateway restart (new instance_id) is the same identity', async () => {
  const run = storedRun({ state: 'create_requested', identity: { ...IDENTITY, instance_id: 'inst-0' } })
  const env = setup({ run })
  await worker(env).poll()
  assert.equal(calls(env.log, 'session.create').length, 1)
})

test('reconciling a created session re-attaches with session.resume before checking', async () => {
  const run = storedRun({ state: 'session_created', session_id: 's-1', identity: { ...IDENTITY } })
  const env = setup({ run })
  await worker(env).poll()
  const t = trace(env.log)
  assert.equal(calls(env.log, 'session.create').length, 0)
  assert.ok(t.indexOf('session.resume') < t.indexOf('tools.show'))
  assert.deepEqual(calls(env.log, 'session.resume')[0], { session_id: 's-1', omit_messages: true })
  assert.equal(env.api.state.run.state, 'running')
})

test('reconciling an unconfirmed submit checks again and repeats the identical prompt.submit', async () => {
  const run = storedRun({ state: 'submit_requested', session_id: 's-1', effective_toolsets: ['file', 'terminal'],
    identity: { ...IDENTITY } })
  const env = setup({ run })
  await worker(env).poll()
  assert.deepEqual(calls(env.log, 'prompt.submit'), [{ session_id: 's-1', text: 'do it', submission_id: 'vk-1.submit.1' }])

  const handlers = happyHandlers()
  handlers['session.resume'] = { ...handlers['session.resume'], info: info(['file', 'terminal', 'web']) }
  const env2 = setup({ run, handlers })
  await worker(env2).poll()
  assert.equal(calls(env2.log, 'prompt.submit').length, 0)
  assert.equal(lastStep(env2.log).data.reason, 'toolsets_exceeded')
})

test('a lost reply is repeated identically; a refusal is not repeated', async () => {
  const handlers = happyHandlers()
  handlers['session.create'] = [new TransportError('timeout'), new RpcError(5001, 'storage busy'), handlers['session.create']]
  handlers['prompt.submit'] = [new TransportError('timeout'), handlers['prompt.submit']]
  const env = setup({ handlers })
  await worker(env).poll()
  const created = calls(env.log, 'session.create')
  assert.equal(created.length, 3)
  assert.ok(created.every(params => JSON.stringify(params) === JSON.stringify(storedRun().create_params)))
  const submitted = calls(env.log, 'prompt.submit')
  assert.equal(submitted.length, 2)
  assert.deepEqual(submitted[0], submitted[1])
  assert.equal(env.api.state.run.state, 'running')

  const refusing = happyHandlers()
  refusing['session.create'] = new RpcError(4001, 'cwd refused', { reason: 'cwd_not_allowed', detail: 'outside roots', field: 'cwd' })
  const env2 = setup({ handlers: refusing })
  await worker(env2).poll()
  assert.equal(calls(env2.log, 'session.create').length, 1)
  assert.deepEqual(lastStep(env2.log), { name: 'refused', data: { reason: 'cwd_not_allowed', detail: 'outside roots', field: 'cwd' } })
})

test('no reply after the repeats leaves the run for a later claim', async () => {
  const handlers = happyHandlers()
  handlers['session.create'] = new TransportError('timeout')
  const env = setup({ handlers })
  await worker(env).poll()
  assert.equal(calls(env.log, 'session.create').length, plugin.REPEATS)
  assert.equal(env.api.state.run.state, 'create_requested') // not refused, not unknown
  const t = trace(env.log)
  assert.ok(t.includes('release') && t.includes('rest release'))
})

test('the guard refuses every call and parameter outside the allow list', () => {
  for (const method of ['approval.respond', 'clarify.respond', 'clarify.lock', 'session.yolo', 'prompt.cancel', 'session.mutate']) {
    assert.throws(() => guardCall(method, {}), GuardError, method)
  }
  for (const [method, params] of [
    ['session.create', { cwd: '/x', yolo: true }],
    ['session.create', { cwd: '/x', unattended: true }],
    ['prompt.submit', { session_id: 's', text: 't', submission_id: 'x', finite: true }],
    ['prompt.submit', { session_id: 's', text: 't', submission_id: 'x', toolsets: ['web'] }]
  ]) {
    assert.throws(() => guardCall(method, params), GuardError, `${method} ${Object.keys(params)}`)
  }
  assert.doesNotThrow(() => guardCall('session.create', storedRun().create_params))
})

test('a tampered stored create call never leaves the window', async () => {
  const run = storedRun({ create_params: { ...storedRun().create_params, yolo: true } })
  const env = setup({ run })
  await worker(env).poll()
  assert.equal(calls(env.log, 'session.create').length, 0)
  assert.equal(lastStep(env.log).data.reason, 'bad_create_params')
})

test('the source sends no respond, yolo, unattended or finite and evaluates no code', () => {
  for (const banned of ['approval.respond', 'clarify.respond', 'clarify.lock', 'yolo', 'unattended', 'finite', 'eval(', 'new Function']) {
    assert.ok(!source.includes(banned), banned)
  }
  // The loader rewrites import specifiers by pattern: only the two real imports may match it.
  assert.deepEqual(source.match(/(?:from|import)\s+'[^']*'/g), ["from '@hermes/plugin-sdk'", "from 'react'"])
  assert.ok(!/\bimport\s*\(/.test(source), 'no dynamic import')
})

test('pickRoute takes the one route of the active connection and profile', () => {
  const other = { ...ROUTE, connectionId: 'remote-1', primary: false }
  assert.equal(pickRoute([ROUTE, other], { connectionId: 'remote-1', profile: 'default' }), other)
  assert.equal(pickRoute([ROUTE, other], { connectionId: null, profile: 'default' }), ROUTE)
  assert.equal(pickRoute([ROUTE, { ...ROUTE }], { connectionId: 'local', profile: 'default' }), null)
  assert.equal(pickRoute([ROUTE], { connectionId: 'local', profile: 'work' }), null)
  assert.equal(pickRoute([ROUTE], { connectionId: 'local', profile: null }), null)
})

test('without one clear route nothing is claimed', async () => {
  const env = setup({ routes: [ROUTE, { ...ROUTE }] })
  const w = worker(env)
  await w.poll()
  assert.ok(!trace(env.log).includes('rest claim'))
  assert.match(w.getView().note, /Verbindungsweg/)
})

test('a running run of another connection is left to it', async () => {
  const run = storedRun({ state: 'running', session_id: 's-1', admission_id: 'adm-1',
    identity: { ...IDENTITY, connection_id: 'remote-1' } })
  const env = setup({ run })
  await worker(env).poll()
  assert.deepEqual(trace(env.log), ['rest /runs/claimable', 'rest /runs'])
})

test('a live claim of another window is skipped', async () => {
  const run = storedRun({ claim: { owner: 'win-b', generation: 1, expires_at: 9e9, live: true } })
  const env = setup({ run })
  env.api.state.run.claim.live = false // the list still shows it, as an older snapshot would
  const list = await env.api.rest('/runs/claimable')
  list.runs[0].claim = run.claim
  const w = createWorker({ host: env.host, rest: async (path, opts) => (path === '/runs/claimable' ? list : env.api.rest(path, opts)),
    owner: 'win-a', sleep: async () => {} })
  await w.poll()
  assert.ok(!trace(env.log).includes('rest claim'))
})

test('tracking reports the terminal result', async () => {
  const handlers = happyHandlers()
  handlers['prompt.receipt'] = [
    { admission_id: 'adm-1', status: 'queued' },
    { admission_id: 'adm-1', status: 'terminal', outcome: 'completed' },
    { admission_id: 'adm-1', status: 'terminal', outcome: 'completed', result: { final_response: 'Hallo' } }
  ]
  const env = setup({ handlers })
  const w = worker(env)
  await w.poll()
  await w.trackAll()
  assert.deepEqual(calls(env.log, 'prompt.receipt').at(-1), { session_id: 's-1', admission_id: 'adm-1', include_result: true })
  assert.deepEqual(lastStep(env.log), { name: 'finished', data: { identity: IDENTITY, outcome: 'completed', final_response: 'Hallo' } })
  assert.equal(w.held().size, 0)
})

test('an admission Hermes cannot account for becomes unknown and is not retried', async () => {
  const handlers = happyHandlers()
  handlers['prompt.receipt'] = { admission_id: 'adm-1', status: 'unknown' }
  const env = setup({ handlers })
  const w = worker(env)
  await w.poll()
  assert.equal(lastStep(env.log).name, 'unknown')
  assert.equal(lastStep(env.log).data.reason, 'admission_unknown')
  assert.equal(calls(env.log, 'prompt.submit').length, 1)
})

test('a started turn reports its Rückfragen from open_requests and its approvals from prompts', async () => {
  const handlers = happyHandlers()
  handlers['prompt.receipt'] = { admission_id: 'adm-1', status: 'started' }
  handlers['session.resume'] = { session_id: 's-1', info: info(['file', 'terminal']),
    prompts: [{ kind: 'approval', prompt_id: 'ap-1', command: 'rm -rf build', choices: ['once', 'deny'] }],
    open_requests: [{ id: 'srq-3', method: 'clarify', params: { session_id: 's-1',
      questions: [{ qid: 'q1', question: 'Welche Datei?' }, { qid: 'q2', question: 'Welcher Ton?' }], answers: { q1: 'a.txt' } } }] }
  const env = setup({ handlers })
  await worker(env).poll()
  const running = lastStep(env.log)
  assert.equal(running.name, 'running')
  assert.equal(running.data.receipt_status, 'started')
  assert.deepEqual(running.data.prompts.map(p => [p.kind, p.prompt_id]), [['approval', 'ap-1'], ['clarify', 'srq-3#q2']])
})

test('pendingPrompts reads prompts[] and every kind of open request without leaking input params', () => {
  assert.deepEqual(pendingPrompts(null), [])
  assert.deepEqual(pendingPrompts({ prompts: [{ kind: 'other' }], open_requests: [{ method: 'clarify' }] }), [])
  const out = pendingPrompts({ prompts: [], open_requests: [
    { id: 'srq-1', method: 'approval', params: { command: 'git push', description: 'push' } },
    { id: 'srq-2', method: 'secret', params: { prompt: 'API key', default: 'sk-live-123' } },
    { id: 'srq-4', method: 'clarify', params: {} }
  ] })
  assert.deepEqual(out, [
    { kind: 'approval', prompt_id: 'srq-1', command: 'git push', description: 'push' },
    { kind: 'clarify', prompt_id: 'srq-2', question: 'Eingabe angefordert (secret)' },
    { kind: 'clarify', prompt_id: 'srq-4' }
  ])
  assert.ok(!JSON.stringify(out).includes('sk-live'))
})

test('receipt refusals end in unknown after three; lost replies do not count', async () => {
  const handlers = happyHandlers()
  handlers['prompt.receipt'] = [new TransportError('t'), new TransportError('t'), new RpcError(4004, 'gone'),
    new RpcError(4004, 'gone'), new RpcError(4004, 'gone')]
  const env = setup({ handlers })
  const w = worker(env)
  await w.poll() // receipt 1: lost
  await w.trackAll() // 2: lost
  await w.trackAll() // 3: refused (1)
  await w.trackAll() // 4: refused (2)
  assert.equal(env.api.state.run.state, 'running')
  await w.trackAll() // 5: refused (3)
  assert.equal(lastStep(env.log).data.reason, 'receipt_refused')
})

test('a gateway event of the child session re-tracks it at once', async () => {
  const env = setup()
  const w = worker(env)
  await w.poll()
  const before = calls(env.log, 'prompt.receipt').length
  w.onGatewayEvent({ type: 'message.complete', session_id: 'other' })
  w.onGatewayEvent({ type: 'tool.start', session_id: 's-1' })
  await ticks()
  assert.equal(calls(env.log, 'prompt.receipt').length, before)
  w.onGatewayEvent({ type: 'approval.request', session_id: 's-1' })
  await ticks()
  assert.equal(calls(env.log, 'prompt.receipt').length, before + 1)
})

test('a lost claim stops renewing and tracking', async () => {
  const env = setup()
  const w = worker(env)
  await w.poll()
  assert.equal(w.held().size, 1)
  env.api.state.generation += 1 // another window claimed it
  await w.renewAll()
  assert.equal(w.held().size, 0)
})

test('Session öffnen opens the child on its own route as a tab', async () => {
  const env = setup()
  const w = worker(env)
  await w.openSession({ session_id: 's-1', identity: { ...IDENTITY } })
  assert.deepEqual(env.log.at(-1), ['open', 's-1', { route: ROUTE, intent: 'tab' }])
  await w.openSession({ session_id: 's-1', identity: { ...IDENTITY, connection_id: 'remote-1' } })
  assert.equal(env.log.at(-1)[0], 'notify')
})

test('labels: Rückfrage and Freigabe while running, veraltet without a live claim', () => {
  const now = 1000 * 1000
  const live = { expires_at: 2000 }
  assert.deepEqual(labelOf({ state: 'running', prompts: [{ kind: 'clarify' }], claim: live }, now), { text: 'Rückfrage', waiting: true, stale: false })
  assert.equal(labelOf({ state: 'running', prompts: [{ kind: 'approval' }], claim: live }, now).text, 'Freigabe erforderlich')
  assert.deepEqual(labelOf({ state: 'running', prompts: [], claim: { expires_at: 999 } }, now), { text: 'läuft', waiting: false, stale: true })
  assert.equal(labelOf({ state: 'waiting_for_gui', claim: {} }, now).stale, false)
  assert.equal(labelOf({ state: 'finished', claim: {} }, now).text, 'fertig')
})

test('checkInfo is the k2 check', () => {
  assert.equal(checkInfo(info(['file']), ['file'], null).ok, true)
  assert.deepEqual(checkInfo(info(['file', 'verksted']), ['file'], null).effective, ['file', 'verksted'])
  assert.equal(checkInfo(info(['file', 'web']), ['file'], null).refusal.reason, 'toolsets_exceeded')
  assert.equal(checkInfo({ ...info(['file']), enabled_toolsets: undefined }, ['file'], null).refusal.reason, 'toolsets_not_checkable')
  assert.equal(checkInfo(info(['web', 'file']), null, ['file', 'web']).ok, true)
})

function render(node) {
  if (!node || typeof node !== 'object') return node
  if (typeof node.type === 'function') return render(node.type({ ...node.props, children: node.children }))
  return { ...node, children: node.children.flat().map(render) }
}
const find = (node, pred, out = []) => {
  if (node && typeof node === 'object') {
    if (pred(node)) out.push(node)
    for (const child of node.children || []) find(child, pred, out)
  }
  return out
}

test('register wires the status chip, the page, the doorbell and the timers', async () => {
  const env = setup()
  globalThis.__verkstedHost = env.host
  const registered = []
  const events = []
  const intervals = []
  const disposers = []
  const ctx = {
    registerMany: entries => registered.push(...entries),
    onEvent: (name, fn) => events.push([name, fn]),
    setInterval: (fn, ms) => intervals.push(ms),
    onDispose: fn => disposers.push(fn),
    rest: env.api.rest
  }
  assert.equal(plugin.default.id, 'verksted')
  assert.equal(plugin.default.defaultEnabled, false)
  plugin.default.register(ctx)
  assert.deepEqual(registered.map(e => [e.id, e.area]), [['status', 'statusbar.right'], ['page', 'routes'], ['nav', 'sidebar.nav']])
  assert.equal(registered[1].data.path, '/verksted')
  assert.deepEqual(events.map(([name]) => name), ['plugin.verksted.work', '*'])
  assert.deepEqual(intervals, [plugin.POLL_MS, plugin.RENEW_MS, plugin.TRACK_MS])
  await ticks(40)
  assert.equal(env.api.state.run.state, 'running') // the initial kick started the stored run

  const page = render(registered[1].render())
  const button = find(page, n => n.type === 'button' && n.children.includes('Session öffnen'))
  assert.equal(button.length, 1)
  await button[0].props.onClick()
  await ticks()
  assert.deepEqual(env.log.at(-1), ['open', 's-1', { route: ROUTE, intent: 'tab' }])
  const chip = render(registered[0].render())
  assert.equal(chip.type, 'button')
  assert.match(chip.children[0], /^Verksted: /)
  for (const fn of disposers) fn()
  delete globalThis.__verkstedHost
})

// -- k3 fix pass: regressions ------------------------------------------------------------------

test('fix 1: session.create is never titled (a title picks a session before request_id)', () => {
  assert.throws(() => guardCall('session.create', { ...storedRun().create_params, title: 'Verksted · a' }), GuardError)
  assert.ok(!Object.hasOwn(storedRun().create_params, 'title'))
})

test('fix 2: a prompt the client copies into open_requests is counted once', async () => {
  const handlers = happyHandlers()
  handlers['prompt.receipt'] = { admission_id: 'adm-1', status: 'started' }
  handlers['session.resume'] = { ...handlers['session.resume'], prompts: [
    { kind: 'approval', prompt_id: 'ap-1', command: 'rm -rf build', choices: ['once', 'deny'] },
    { kind: 'clarify', prompt_id: 'cl-1', question: 'Welche Datei?' }
  ] }
  const env = setup({ handlers })
  await worker(env).poll()
  assert.deepEqual(lastStep(env.log).data.prompts.map(p => [p.kind, p.prompt_id]), [['approval', 'ap-1'], ['clarify', 'cl-1']])
  // the same snapshot read directly (no client copy) gives the same list
  assert.deepEqual(pendingPrompts(handlers['session.resume']).map(p => p.prompt_id), ['ap-1', 'cl-1'])
})

test('fix 3: an empty selection tolerates exactly the tools of the own toolset', async () => {
  const run = storedRun({ expected_toolsets: [], create_params: { ...storedRun().create_params, toolsets: ['no_mcp'] } })
  const own = happyHandlers(['verksted'])
  own['tools.show'] = { sections: [{ name: 'verksted', tools: [{ name: 'verksted', description: 'x' }] }], total: 1 }
  const env = setup({ run, handlers: own })
  await worker(env).poll()
  assert.equal(env.api.state.run.state, 'running')

  for (const [what, shown] of [
    ['a tool of another toolset', { sections: [{ name: 'verksted', tools: [{ name: 'verksted' }] }, { name: 'web', tools: [{ name: 'web_search' }] }], total: 2 }],
    ['another tool inside the own section', { sections: [{ name: 'verksted', tools: [{ name: 'verksted' }, { name: 'sneaky' }] }], total: 2 }],
    ['a total the sections do not account for', { sections: [{ name: 'verksted', tools: [{ name: 'verksted' }] }], total: 2 }]
  ]) {
    const h = happyHandlers(['verksted'])
    h['tools.show'] = shown
    const e = setup({ run, handlers: h })
    await worker(e).poll()
    assert.equal(lastStep(e.log).data.reason, 'toolsets_not_checkable', what)
    assert.equal(calls(e.log, 'prompt.submit').length, 0, what)
  }
})

for (const [name, err] of [
  ['storage_unavailable (5001)', new RpcError(5001, 'storage_unavailable', { reason: 'storage_unavailable' })],
  ['runtime_draining (4001)', new RpcError(4001, 'runtime_draining', { reason: 'runtime_draining' })],
  ['permission_denied (4001)', new RpcError(4001, 'permission_denied', { reason: 'permission_denied' })]
]) {
  test(`fix 4: a runtime.describe refusal other than -32601 (${name}) claims nothing and leaves the run waiting`, async () => {
    const handlers = happyHandlers()
    handlers['runtime.describe'] = err
    const env = setup({ handlers })
    const w = worker(env)
    await w.poll()
    assert.ok(!trace(env.log).includes('rest claim'))
    assert.equal(env.api.state.run.state, 'waiting_for_gui')
    assert.match(w.getView().note, /runtime\.describe/)
  })
}

test('fix 5: a start requested under another principal is marked unknown before any re-send', async () => {
  const run = storedRun({ state: 'create_requested', identity: { ...IDENTITY, subject: 'alice' } })
  const env = setup({ run, subject: 'bob' })
  await worker(env).poll()
  assert.equal(lastStep(env.log).name, 'unknown')
  assert.equal(lastStep(env.log).data.reason, 'identity_changed')
  assert.equal(calls(env.log, 'session.create').length, 0)

  const same = setup({ run, subject: 'alice' })
  await worker(same).poll()
  assert.equal(calls(same.log, 'session.create').length, 1)
  assert.equal(same.api.state.run.state, 'running')
})

for (const [how, signal] of [
  ['gateway.ready', w => w.onGatewayEvent({ type: 'gateway.ready', payload: {} })],
  ['the gateway state leaving open', w => w.noteGatewayState('connecting')]
]) {
  test(`fix 6: a reconnect (${how}) between the check and the submit re-attaches and checks before submitting`, async () => {
    let env, w
    const handlers = happyHandlers()
    handlers['session.info'] = () => { env.host.reconnect(); signal(w); return info(['file', 'terminal']) }
    env = setup({ handlers })
    w = worker(env)
    await w.poll()
    const t = trace(env.log)
    assert.ok(!t.includes('implicit'), 'the client never resumes unseen')
    const resumed = t.lastIndexOf('session.resume')
    assert.ok(resumed > t.indexOf('session.info') && resumed < t.indexOf('prompt.submit'), t.join(' > '))
    assert.equal(env.api.state.run.state, 'running')

    // what the re-attach derived is checked: a grown set refuses, nothing is submitted
    let env2, w2
    const grown = happyHandlers()
    grown['session.info'] = () => { env2.host.reconnect(); signal(w2); return info(['file', 'terminal']) }
    grown['session.resume'] = { ...grown['session.resume'], info: info(['file', 'terminal', 'web']) }
    env2 = setup({ handlers: grown })
    w2 = worker(env2)
    await w2.poll()
    assert.equal(calls(env2.log, 'prompt.submit').length, 0)
    assert.equal(lastStep(env2.log).data.reason, 'toolsets_exceeded')
  })
}

test('fix 6: a lost submit reply over a reconnect re-attaches; an admitted submission is adopted, not re-sent', async () => {
  let env, w
  const handlers = happyHandlers()
  handlers['prompt.submit'] = [() => { env.host.reconnect(); w.onGatewayEvent({ type: 'gateway.ready' }); throw new TransportError('socket closed') }]
  handlers['session.resume'] = { ...handlers['session.resume'], info: info(['file', 'terminal'], { lazy: false }), running: true,
    pending: [{ admission_id: 'adm-7', input_id: 'vk-1.submit.1', status: 'started' }] }
  env = setup({ handlers })
  w = worker(env)
  await w.poll()
  assert.equal(calls(env.log, 'prompt.submit').length, 1)
  assert.ok(!trace(env.log).includes('implicit'))
  assert.equal(steps(env.log).find(s => s.name === 'running').data.admission_id, 'adm-7')
})

test('fix 7: the default case does not trust an empty inventory that tools.show contradicts', async () => {
  const run = storedRun({ expected_toolsets: null })
  delete run.create_params.toolsets
  const failOpen = happyHandlers([])
  failOpen['tools.show'] = { sections: [{ name: 'file', tools: [{ name: 'read_file' }] }], total: 1 }
  const env = setup({ run, handlers: failOpen })
  await worker(env).poll()
  assert.equal(lastStep(env.log).data.reason, 'toolsets_not_checkable')
  assert.equal(calls(env.log, 'prompt.submit').length, 0)

  const empty = happyHandlers([])
  empty['tools.show'] = { sections: [], total: 0 }
  const env2 = setup({ run, handlers: empty })
  await worker(env2).poll()
  assert.equal(env2.api.state.run.state, 'running')

  // a pre-submit read that comes back empty after a non-empty first read is the fail-open shape
  const emptied = happyHandlers(['file', 'terminal'])
  emptied['session.info'] = info([])
  const env3 = setup({ run, handlers: emptied })
  await worker(env3).poll()
  assert.equal(lastStep(env3.log).data.reason, 'toolsets_not_checkable')
  assert.equal(calls(env3.log, 'prompt.submit').length, 0)
})

test('fix 8: reconciling an unconfirmed submit never submits into a session it cannot attest', async () => {
  const run = storedRun({ state: 'submit_requested', session_id: 's-1', effective_toolsets: ['file', 'terminal'],
    identity: { ...IDENTITY } })
  const resume = happyHandlers()['session.resume']
  for (const [what, snap] of [
    ['an agent already built (lazy false)', { ...resume, info: info(['file', 'terminal'], { lazy: false }) }],
    ['no info', { ...resume, info: undefined }],
    ['a turn running', { ...resume, running: true }],
    ['another admission pending', { ...resume, pending: [{ admission_id: 'adm-x', input_id: 'user-typed', status: 'queued' }] }],
    ['no pending list', { ...resume, pending: undefined }]
  ]) {
    const h = happyHandlers()
    h['session.resume'] = snap
    const env = setup({ run, handlers: h })
    await worker(env).poll()
    assert.equal(calls(env.log, 'prompt.submit').length, 0, what)
    assert.equal(lastStep(env.log).name, 'unknown', what)
    assert.equal(lastStep(env.log).data.reason, 'submit_unconfirmed', what)
  }

  const admitted = happyHandlers()
  admitted['session.resume'] = { ...resume, info: info(['file', 'terminal'], { lazy: false }), running: true,
    pending: [{ admission_id: 'adm-9', input_id: 'vk-1.submit.1', status: 'started' }] }
  const env = setup({ run, handlers: admitted })
  await worker(env).poll()
  assert.equal(calls(env.log, 'prompt.submit').length, 0)
  assert.equal(steps(env.log).find(s => s.name === 'running').data.admission_id, 'adm-9')
})

// -- k5: a run in another profile -----------------------------------------------------------------

const RESEARCH_ROUTE = Object.freeze({ connectionId: 'local', mode: 'local', primary: false, profile: 'research', targetProfile: 'research' })
const RESEARCH_DESCRIBE = Object.freeze({ ...DESCRIBE, profile_id: '/homes/research', instance_id: 'inst-r' })
const switchedRun = (over = {}) => storedRun({ session_profile: 'research', own_toolset_tolerated: false, ...over })
const researchHandlers = (enabled = ['file', 'terminal']) => ({ ...happyHandlers(enabled), 'runtime.describe': RESEARCH_DESCRIBE })

test('k5: a run in another profile starts on that profile\'s route of the same connection', async () => {
  const other = { ...RESEARCH_ROUTE, connectionId: 'remote-1' } // same profile name elsewhere: another profile
  const env = setup({ run: switchedRun(), routes: [ROUTE, RESEARCH_ROUTE, other], handlers: researchHandlers() })
  const routesUsed = []
  const request = env.host.requestProfile
  env.host.requestProfile = async (route, method, params) => { routesUsed.push([route, method]); return request(route, method, params) }
  await worker(env).poll()
  assert.equal(env.api.state.run.state, 'running')
  assert.ok(routesUsed.length && routesUsed.every(([route]) => route === RESEARCH_ROUTE), 'every call on the research route')
  const claim = env.log.find(([kind, action]) => kind === 'rest' && action === 'claim')[3]
  assert.equal(claim.profile_id, '/homes/research') // the API checks it against the frozen session profile home
  assert.deepEqual(steps(env.log)[0].data.identity, {
    connection_id: 'local', route_profile: 'research', target_profile: 'research',
    profile_id: '/homes/research', instance_id: 'inst-r', subject: null
  })
})

test('k5: without a route to the session profile on this connection the run is refused, Hermes untouched', async () => {
  const env = setup({ run: switchedRun(), routes: [ROUTE, { ...RESEARCH_ROUTE, connectionId: 'remote-1' }] })
  await worker(env).poll()
  assert.equal(calls(env.log, 'runtime.describe').length + calls(env.log, 'session.create').length, 0)
  assert.equal(lastStep(env.log).name, 'refused')
  assert.equal(lastStep(env.log).data.reason, 'profile_route_missing')
})

test('k5: Verksted\'s own toolset is not tolerated in another profile', async () => {
  const env = setup({ run: switchedRun(), routes: [ROUTE, RESEARCH_ROUTE], handlers: researchHandlers(['file', 'terminal', 'verksted']) })
  await worker(env).poll()
  assert.equal(calls(env.log, 'prompt.submit').length, 0)
  assert.equal(lastStep(env.log).data.reason, 'toolsets_exceeded')
  assert.deepEqual(lastStep(env.log).data.extra, ['verksted'])

  // The default case and the empty selection too: there the own tool would ride in unchecked.
  const base = storedRun().create_params
  const empty = switchedRun({ expected_toolsets: [], create_params: { ...base, toolsets: ['no_mcp'] } })
  const own = researchHandlers(['verksted'])
  own['tools.show'] = { sections: [{ name: 'verksted', tools: [{ name: 'verksted' }] }], total: 1 }
  const e1 = setup({ run: empty, routes: [ROUTE, RESEARCH_ROUTE], handlers: own })
  await worker(e1).poll()
  assert.equal(lastStep(e1.log).data.reason, 'toolsets_exceeded')

  const none = researchHandlers([])
  none['tools.show'] = { sections: [{ name: 'verksted', tools: [{ name: 'verksted' }] }], total: 1 }
  const e2 = setup({ run: empty, routes: [ROUTE, RESEARCH_ROUTE], handlers: none })
  await worker(e2).poll()
  assert.equal(lastStep(e2.log).data.reason, 'toolsets_not_checkable') // tools.show lists the own tool anyway

  const quiet = researchHandlers([])
  quiet['tools.show'] = { sections: [], total: 0 }
  const e3 = setup({ run: empty, routes: [ROUTE, RESEARCH_ROUTE], handlers: quiet })
  await worker(e3).poll()
  assert.equal(e3.api.state.run.state, 'running')
})

test('k5: a run without the API saying so does not tolerate the own toolset (fail closed)', async () => {
  const run = storedRun()
  delete run.own_toolset_tolerated
  const env = setup({ run, handlers: happyHandlers(['file', 'terminal', 'verksted']) })
  await worker(env).poll()
  assert.equal(lastStep(env.log).data.reason, 'toolsets_exceeded')
})

test('k5: checkInfo refuses the own toolset only when told to', () => {
  assert.equal(checkInfo(info(['file', 'verksted']), ['file'], null, false).refusal.reason, 'toolsets_exceeded')
  assert.equal(checkInfo(info(['file', 'verksted']), null, null, false).refusal.reason, 'toolsets_exceeded')
  assert.equal(checkInfo(info(['file']), ['file'], null, false).ok, true)
})

test('k5: the run view shows the resolved state', () => {
  const { configLine } = plugin
  assert.equal(configLine(storedRun()), 'Toolsets: file, terminal · Arbeitsbereich: /work/a · sha256:0123456789ab')
  assert.equal(configLine({ expected_toolsets: null, config: {} }), 'Toolsets: Profil-Standard')
  assert.equal(configLine({ expected_toolsets: [] }), 'Toolsets: keine')
})
