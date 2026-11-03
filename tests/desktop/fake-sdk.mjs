// Stand-in for '@hermes/plugin-sdk' (the names plugin.js imports). The host is whatever the test
// put on globalThis.__verkstedHost before calling register().
export const STATUSBAR_AREAS = Object.freeze({ left: 'statusbar.left', right: 'statusbar.right' })
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const host = new Proxy({}, { get: (_, key) => globalThis.__verkstedHost && globalThis.__verkstedHost[key] })
