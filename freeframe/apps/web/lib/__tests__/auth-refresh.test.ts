import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

/**
 * A failed refresh used to clear both tokens and hard-navigate to /login,
 * whatever the reason. `fetch` rejects only on a transport failure, so being
 * offline for one request signed the user out and took the page they had open
 * with it. A 502 from a proxy mid-deploy did the same to everyone at once.
 *
 * Only the server refusing the refresh token ends the session.
 */

const store: Record<string, string> = {}

beforeEach(() => {
  vi.resetModules()
  for (const k of Object.keys(store)) delete store[k]
  store['ff_access_token'] = 'old-access'
  store['ff_refresh_token'] = 'the-refresh-token'
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
  })
  // `clearTokens` navigates; jsdom would warn and the assertion we care about
  // is whether the tokens survived.
  vi.stubGlobal('location', { href: '' })
})
afterEach(() => vi.unstubAllGlobals())

async function refresh() {
  const mod = await import('../auth')
  return mod.refreshAccessToken()
}

const sessionSurvived = () =>
  store['ff_refresh_token'] === 'the-refresh-token'

describe('refreshAccessToken', () => {
  it('discards a refresh delayed until after logout in another tab', async () => {
    let respond!: (response: unknown) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { respond = resolve })))
    const auth = await import('../auth')
    const pending = auth.refreshAccessToken()
    auth.removeTokens()
    respond({ ok: true, json: async () => ({ access_token: 'late', refresh_token: 'late-r' }) })
    await expect(pending).resolves.toBeNull()
    expect(auth.getAccessToken()).toBeNull()
    expect(auth.getRefreshToken()).toBeNull()
  })

  it('does not clear a newer session when an old refresh is refused', async () => {
    let respond!: (response: unknown) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { respond = resolve })))
    const auth = await import('../auth')
    const pending = auth.refreshAccessToken()
    auth.removeTokens()
    auth.setTokens('new-user', 'new-refresh')
    respond({ ok: false, status: 401 })
    await expect(pending).resolves.toBeNull()
    expect(auth.getAccessToken()).toBe('new-user')
  })
  it('keeps the session when the request never gets an answer', async () => {
    // The regression: this is what being offline looks like to fetch.
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))

    await expect(refresh()).resolves.toBeNull()
    expect(sessionSurvived()).toBe(true)
  })

  it('keeps the session when the server is briefly unavailable', async () => {
    // A proxy returning 502 during a deploy should not sign everyone out.
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 502 })))

    await expect(refresh()).resolves.toBeNull()
    expect(sessionSurvived()).toBe(true)
  })

  it('ends the session when the refresh token is refused', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401 })))

    await expect(refresh()).resolves.toBeNull()
    expect(sessionSurvived()).toBe(false)
  })

  it('ends the session on a forbidden refresh, which revocation returns', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403 })))

    await expect(refresh()).resolves.toBeNull()
    expect(sessionSurvived()).toBe(false)
  })

  it('stores the new pair on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ access_token: 'fresh', refresh_token: 'rotated' }),
    })))

    await expect(refresh()).resolves.toBe('fresh')
    expect(store['ff_access_token']).toBe('fresh')
    expect(store['ff_refresh_token']).toBe('rotated')
  })
})
