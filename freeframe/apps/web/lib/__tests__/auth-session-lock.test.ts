import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mockSessionLocks } from '@/test/session-locks'

beforeEach(() => {
  vi.resetModules()
  localStorage.clear()
  mockSessionLocks()
  vi.stubGlobal('crypto', webcrypto)
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '')
  window.history.replaceState(null, '', '/jrc-sso#ticket=test-ticket')
  for (const key of ['ff_access_token', 'ff_refresh_token', 'jrc_sso_binding']) {
    document.cookie = `${key}=; path=/; max-age=0`
  }
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

async function bindTicket() {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('test-ticket'))
  const binding = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  document.cookie = `jrc_sso_binding=${binding}; path=/`
}

it.each(['conditional write', 'SSO exchange', 'token refresh'])(
  '%s: logout from tab B cannot complete between the version check and write in tab A', async flow => {
    const tabA = await import('../auth')
    await tabA.setTokens('old', 'old-r')
    const version = tabA.getSessionVersion()
    const sso = await import('../jrc-sso')
    vi.resetModules()
    const tabB = await import('../auth') // Independent tab/module, shared storage and lock manager.
    await bindTicket()
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, json: async () => ({ access_token: 'late', refresh_token: 'late-r', next: '/projects' }),
    })))

    const read = Storage.prototype.getItem
    let versionReads = 0
    let logout: Promise<void> | undefined
    let checked = false
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key) {
      const value = read.call(this, key)
      // Precisely after A reads the current marker, before A can write. SSO
      // and refresh also read once before starting their network request.
      if (key === 'ff_session_version' && ++versionReads === (flow === 'conditional write' ? 1 : 2)) {
        checked = true
        logout = tabB.removeTokens()
      }
      return value
    })

    if (flow === 'conditional write') await tabA.setTokensIfSessionCurrent('late', 'late-r', version)
    else if (flow === 'SSO exchange') await sso.exchangeBrowserTicket()
    else await tabA.refreshAccessToken()
    expect(checked).toBe(true)
    await logout
    // Without a shared lock, B completes its removal in the gap and A leaves
    // its late pair behind. With the lock, B clears A's write before finishing.
    expect(tabB.getAccessToken()).toBeNull()
    expect(tabB.getRefreshToken()).toBeNull()
    expect(document.cookie).not.toMatch(/ff_(access|refresh)_token=/)
  },
)

it('rejects a queued A write when B finishes logout before A acquires the lock', async () => {
  const request = mockSessionLocks()
  const tabA = await import('../auth')
  await tabA.setTokens('old', 'old-r')
  const version = tabA.getSessionVersion()
  vi.resetModules()
  const tabB = await import('../auth')
  const name = request.mock.calls[0][0]
  let release!: () => void
  const blocker = request(name, () => new Promise<void>(resolve => { release = resolve }))
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  const logout = tabB.removeTokens()
  const write = tabA.setTokensIfSessionCurrent('late', 'late-r', version)
  release()
  await blocker
  await logout
  await expect(write).resolves.toBe(false)
  expect(tabA.getAccessToken()).toBeNull()
  expect(tabA.getRefreshToken()).toBeNull()
  expect(new Set(request.mock.calls.map(([lockName]) => lockName)).size).toBe(1)
})

it.each(['missing', 'refused'])('fails closed when Web Locks are %s', async failure => {
  const auth = await import('../auth')
  if (failure === 'missing') vi.stubGlobal('navigator', {})
  else vi.stubGlobal('navigator', { locks: { request: vi.fn().mockRejectedValue(new Error('Denied')) } })
  const writes = vi.spyOn(Storage.prototype, 'setItem')
  await expect(auth.setTokens('late', 'late-r')).rejects.toThrow()
  await expect(auth.setTokensIfSessionCurrent('late', 'late-r', null)).rejects.toThrow()
  await expect(auth.removeTokens()).rejects.toThrow()
  await expect(auth.restoreSessionCookies()).rejects.toThrow()
  await expect(auth.clearTokens()).rejects.toThrow()
  await bindTicket()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, json: async () => ({ access_token: 'late', refresh_token: 'late-r', next: '/projects' }),
  })))
  const sso = await import('../jrc-sso')
  await expect(sso.exchangeBrowserTicket()).rejects.toThrow()
  localStorage.setItem('ff_refresh_token', 'old-r')
  writes.mockClear()
  await expect(auth.refreshAccessToken()).resolves.toBeNull()
  expect(writes).not.toHaveBeenCalled()
  expect(auth.getAccessToken()).toBeNull()
  expect(document.cookie).not.toMatch(/ff_(access|refresh)_token=/)
})

it('cookie restoration queued after logout cannot resurrect either cookie', async () => {
  const request = mockSessionLocks()
  const auth = await import('../auth')
  await auth.setTokens('old', 'old-r')
  let release!: () => void
  const blocker = request(request.mock.calls[0][0], () => new Promise<void>(resolve => { release = resolve }))
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  const logout = auth.removeTokens()
  const restore = auth.restoreSessionCookies()
  release()
  await blocker
  await logout
  await expect(restore).resolves.toBe(false)
  expect(document.cookie).not.toMatch(/ff_(access|refresh)_token=/)
})
