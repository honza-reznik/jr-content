import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { exchangeBrowserTicket, safeVideoDestination } from '../jrc-sso'
import { setTokens, removeTokens } from '../auth'

vi.mock('../auth', () => ({ setTokens: vi.fn(), removeTokens: vi.fn() }))

let hash = '#ticket=local-test-ticket'
const replaceState = vi.fn()
const fetcher = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/video')
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', fetcher)
  window.history.replaceState(null, '', '/video/jrc-sso')
  vi.stubGlobal('window', {
    location: { get hash() { return hash }, pathname: '/video/jrc-sso', search: '?next=//foreign.invalid' },
    history: { replaceState },
  })
  hash = '#ticket=local-test-ticket'
  document.cookie = 'jrc_sso_binding=; path=/video; max-age=0'
  document.cookie = 'jrc_sso_binding=; path=/; max-age=0'
})
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

async function bind(ticket = 'local-test-ticket') {
  const digest = await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(ticket))
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  document.cookie = `jrc_sso_binding=${hex}; path=/video`
}

describe('SSO destination validation', () => {
  it.each(['//foreign.invalid', 'https://foreign.invalid', '/\\evil', '/%2fevil', '/%252fevil',
    '/%5cevil', '/../accounts', '/projects/../../accounts', '/a/./b', '/a//b', '/projects#ticket=x',
    '/projects?next=%2f%2fevil', '/a\nb', '', 42, {}, '/' + 'a'.repeat(512)])('refuses %j', value => {
    expect(safeVideoDestination(value)).toBeNull()
  })
  it('prefixes only validated app paths and defaults absent next', () => {
    expect(safeVideoDestination('/projects/p?embed=1')).toBe('/video/projects/p?embed=1')
    expect(safeVideoDestination(null)).toBe('/video/projects')
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '')
    expect(safeVideoDestination('/projects')).toBe('/projects')
  })
})

describe('browser ticket exchange', () => {
  it('erases the address synchronously, binds the browser, and uses only the signed response next', async () => {
    await bind()
    fetcher.mockResolvedValue({ ok: true, json: async () => ({ access_token: 'access', refresh_token: 'refresh', next: '/projects/p' }) })
    const result = exchangeBrowserTicket()
    expect(replaceState).toHaveBeenCalledWith(null, '', '/video/jrc-sso')
    expect(removeTokens).toHaveBeenCalledOnce()
    expect(await result).toBe('/video/projects/p')
    expect(document.cookie).not.toContain('jrc_sso_binding')
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining('/auth/jrc-exchange'), expect.objectContaining({
      method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer',
      body: JSON.stringify({ ticket: 'local-test-ticket' }),
    }))
    expect(setTokens).toHaveBeenCalledWith('access', 'refresh')
  })
  it('rejects a foreign ticket even with an existing browser binding', async () => {
    await bind('another-ticket')
    await expect(exchangeBrowserTicket()).rejects.toThrow()
    expect(fetcher).not.toHaveBeenCalled()
    expect(setTokens).not.toHaveBeenCalled()
  })
  it.each(['', '#ticket=x&ticket=y', '#other=x'])('rejects missing/duplicate fragments: %s', async fragment => {
    hash = fragment
    await bind()
    await expect(exchangeBrowserTicket()).rejects.toThrow()
    expect(replaceState).toHaveBeenCalled()
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('rejects a ticket without browser binding', async () => {
    await expect(exchangeBrowserTicket()).rejects.toThrow()
    expect(fetcher).not.toHaveBeenCalled()
  })
  it.each([401, 503])('does not retry an exchange refused with %s', async status => {
    await bind()
    fetcher.mockResolvedValue({ ok: false, status })
    await expect(exchangeBrowserTicket()).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledOnce()
    expect(setTokens).not.toHaveBeenCalled()
  })
  it.each([
    { access_token: 'a', refresh_token: 'r', next: '//foreign.invalid' },
    { access_token: 'a', refresh_token: 'r', next: '/../accounts' },
    { access_token: 'a' },
  ])('never stores tokens with an unsafe/incomplete response', async data => {
    await bind()
    fetcher.mockResolvedValue({ ok: true, json: async () => data })
    await expect(exchangeBrowserTicket()).rejects.toThrow()
    expect(setTokens).not.toHaveBeenCalled()
  })
})
