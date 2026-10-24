import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { consumeLogoutGrant } from '../jrc-logout'

const grant = 'a'.repeat(43)
const replaceState = vi.fn()
let hash = ''

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/video')
  vi.stubGlobal('crypto', webcrypto)
  window.history.replaceState(null, '', '/video/jrc-logout')
  vi.stubGlobal('window', {
    location: { get hash() { return hash }, pathname: '/video/jrc-logout' },
    history: { replaceState },
  })
  hash = `#grant=${grant}`
  document.cookie = 'jrc_logout_binding=; path=/video; max-age=0'
})
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

async function bind(value = grant) {
  const digest = await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  document.cookie = `jrc_logout_binding=${hex}; path=/video`
}

it('accepts the protected POST grant only once and erases its fragment before awaiting', async () => {
  await bind()
  const result = consumeLogoutGrant()
  expect(replaceState).toHaveBeenCalledWith(null, '', '/video/jrc-logout')
  expect(await result).toBe(true)
  expect(document.cookie).not.toContain('jrc_logout_binding')
  expect(await consumeLogoutGrant()).toBe(false)
})

it.each(['', '#grant=forged', `#grant=${grant}&grant=${grant}`, '#other=x'])(
  'requires both a valid fragment and its browser cookie: %s', async fragment => {
    await bind()
    hash = fragment
    expect(await consumeLogoutGrant()).toBe(false)
  },
)

it('rejects a grant from another browser and a mismatched grant', async () => {
  expect(await consumeLogoutGrant()).toBe(false)
  await bind('b'.repeat(43))
  expect(await consumeLogoutGrant()).toBe(false)
})
