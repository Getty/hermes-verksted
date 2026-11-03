// Loads verksted/desktop/plugin.js the way the Desktop loader does: the two import specifiers
// rewritten (here to the fakes beside this file), the source otherwise untouched.
import { readFileSync } from 'node:fs'

export const SOURCE_URL = new URL('../../verksted/desktop/plugin.js', import.meta.url)
export const source = readFileSync(SOURCE_URL, 'utf8')

const rewritten = source
  .replace("from '@hermes/plugin-sdk'", `from '${new URL('./fake-sdk.mjs', import.meta.url).href}'`)
  .replace("from 'react'", `from '${new URL('./fake-react.mjs', import.meta.url).href}'`)

export const plugin = await import(`data:text/javascript;base64,${Buffer.from(rewritten).toString('base64')}`)
