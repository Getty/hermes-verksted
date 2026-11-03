// Harness probe Desktop part: exercises exactly the SDK surface the shim claims to provide.
import { host } from '@hermes/plugin-sdk'
import { createElement } from 'react'

export default {
  id: 'e2eprobe',
  name: 'E2E probe',
  register(ctx) {
    ctx.register({ id: 'status', area: 'statusBar.right', render: () => createElement('span', null, 'e2eprobe') })
    ctx.onEvent('plugin.e2eprobe.ping', event => {
      ctx.rest('/pong', { method: 'POST', body: { type: event.type, note: event.payload?.note ?? null } })
    })
    ctx.setInterval(async () => { await ctx.rest('/tick', { method: 'POST', body: {} }) }, 30000)
    // Reachable from tests: the route list is read once at load, as a real part would.
    host.profileRoutes().then(routes => ctx.storage.set('routes', routes))
  },
}
