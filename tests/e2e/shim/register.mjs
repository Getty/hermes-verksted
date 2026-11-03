// Registers the module-resolution hooks that make a Desktop plugin.js loadable under plain Node.
import { register } from 'node:module'

export function installHooks(data) {
  register('./hooks.mjs', { parentURL: import.meta.url, data })
}
