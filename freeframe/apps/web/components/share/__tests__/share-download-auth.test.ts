import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fetchDownloadUrl, fetchShareDownloadOptions } from '../share-download'

beforeEach(() => {
  localStorage.setItem('ff_access_token', 'member-token')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ url: '/signed' }))))
})
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })

it('authenticates rendition choices and individual/bulk downloads, preserving the share session', async () => {
  await fetchShareDownloadOptions('t', 'a', 'password-session')
  expect(await fetchDownloadUrl('t', 'a', 'password-session', 'original')).toBe('/signed')
  expect(await fetchDownloadUrl('t', 'a', 'password-session', undefined, true)).toBe('/signed')
  for (const [url, init] of vi.mocked(fetch).mock.calls) {
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer member-token')
    expect(String(url)).toContain('share_session=password-session')
  }
  expect(vi.mocked(fetch).mock.calls[1][0]).toContain('rendition=original')
  expect(vi.mocked(fetch).mock.calls[2][0]).toContain('prefer_original=true')
})
