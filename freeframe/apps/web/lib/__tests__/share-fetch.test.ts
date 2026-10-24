import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { shareFetch } from '../share-fetch'
import { getAccessToken, refreshAccessToken } from '../auth'

vi.mock('../auth', () => ({ getAccessToken: vi.fn(), refreshAccessToken: vi.fn() }))

beforeEach(() => { vi.resetAllMocks(); vi.stubGlobal('fetch', vi.fn()) })
afterEach(() => vi.unstubAllGlobals())

describe('authenticated share requests', () => {
  it('reads the current session for every request and preserves method, body and headers', async () => {
    vi.mocked(getAccessToken).mockReturnValueOnce('first').mockReturnValueOnce('second')
    vi.mocked(fetch).mockResolvedValue(new Response('{}'))
    await shareFetch('/share/t/comment', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } })
    await shareFetch('/share/t/assets?page=2')
    const first = vi.mocked(fetch).mock.calls[0][1]!
    expect(first.method).toBe('POST')
    expect(first.body).toBe('{}')
    expect(new Headers(first.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(first.headers).get('Authorization')).toBe('Bearer first')
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get('Authorization')).toBe('Bearer second')
  })

  it('retries an expired session with the shared refresh mechanism', async () => {
    vi.mocked(getAccessToken).mockReturnValue('expired')
    vi.mocked(refreshAccessToken).mockResolvedValue('fresh')
    vi.mocked(fetch).mockResolvedValueOnce(new Response('', { status: 401 })).mockResolvedValueOnce(new Response('{}'))
    expect((await shareFetch('/share/t/downloads/a')).status).toBe(200)
    expect(refreshAccessToken).toHaveBeenCalledOnce()
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get('Authorization')).toBe('Bearer fresh')
  })

  it('keeps public shares usable by guests and does not retry forbidden membership', async () => {
    vi.mocked(getAccessToken).mockReturnValue(null)
    vi.mocked(fetch).mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('', { status: 403 }))
    expect((await shareFetch('/share/public')).status).toBe(200)
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).has('Authorization')).toBe(false)
    vi.mocked(getAccessToken).mockReturnValue('revoked-member')
    expect((await shareFetch('/share/private')).status).toBe(403)
    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('allows public shares when browser storage is unavailable', async () => {
    vi.mocked(getAccessToken).mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
    vi.mocked(fetch).mockResolvedValue(new Response('{}'))
    expect((await shareFetch('/share/public')).status).toBe(200)
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).has('Authorization')).toBe(false)
  })
})
