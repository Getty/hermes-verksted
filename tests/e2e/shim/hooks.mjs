// Node loader hooks (run on the loader thread).
//
// A Desktop plugin.js is "one uncompiled ESM file" that may import ONLY `@hermes/plugin-sdk`,
// `react` and `react/jsx-runtime` (docs/hermes-interfaces.md §2, Declaration). This resolver
// enforces that: those three specifiers are redirected to shim modules, anything else imported
// FROM the plugin file is refused, as the real loader would refuse it.
//
// `@hermes/plugin-sdk` is generated per run: one named export per name the plugin imports, each
// read from `globalThis.__vkShim.sdk` (the main thread fills it). Names are checked against the
// pinned SDK barrel by window.mjs before the plugin is imported.
let pluginUrl = null
let sdkNames = []
const here = new URL('./', import.meta.url).href

export async function initialize(data) {
  pluginUrl = data.pluginUrl
  sdkNames = data.sdkNames
}

export async function resolve(specifier, context, next) {
  const fromPlugin = context.parentURL === pluginUrl
  if (specifier === '@hermes/plugin-sdk') return { url: 'vk-shim:sdk', shortCircuit: true }
  if (specifier === 'react') return { url: here + 'react-stub.mjs', shortCircuit: true }
  if (specifier === 'react/jsx-runtime') return { url: here + 'jsx-runtime-stub.mjs', shortCircuit: true }
  if (fromPlugin) {
    throw new Error(`plugin.js may import only @hermes/plugin-sdk, react and react/jsx-runtime; got "${specifier}"`)
  }
  return next(specifier, context)
}

export async function load(url, context, next) {
  if (url === 'vk-shim:sdk') {
    const lines = sdkNames.map(n => `export const ${n} = globalThis.__vkShim.sdk[${JSON.stringify(n)}]`)
    return { format: 'module', shortCircuit: true, source: lines.join('\n') + '\n' }
  }
  return next(url, context)
}
