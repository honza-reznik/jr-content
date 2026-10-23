// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NextRequest } from 'next/server'

beforeEach(() => {
  vi.resetModules()
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/video')
  vi.stubEnv('NEXT_PUBLIC_API_URL', 'http://127.0.0.1:13000/video-api')
  vi.stubEnv('API_INTERNAL_URL', 'http://jrc-ff-api:8000')
})
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

describe('prefixed server middleware', () => {
  it('checks the internal API and still redirects an anonymous first visit to the prefixed login', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ needs_setup: false }) })
    vi.stubGlobal('fetch', fetcher)
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test:13000/projects'))
    expect(fetcher).toHaveBeenCalledWith('http://jrc-ff-api:8000/setup/status', expect.anything())
    expect(response.status).toBe(307)
    expect(response.headers.get('location')).toBe('http://jrc.test:13000/video/login?from=%2Fprojects')
    expect(response.cookies.get('ff_setup_done')?.value).toBe('1')
  })

  it('allows an existing session and caches completed setup', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ needs_setup: false }) }))
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test:13000/projects', {
      headers: { cookie: 'ff_access_token=local-session' },
    }))
    expect(response.headers.get('x-middleware-next')).toBe('1')
    expect(response.cookies.get('ff_setup_done')?.value).toBe('1')
  })

  it('keeps the auth redirect when the internal API is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('unavailable')))
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test:13000/projects'))
    expect(response.status).toBe(307)
    expect(response.headers.get('location')).toContain('/video/login?')
    expect(response.cookies.get('ff_setup_done')).toBeUndefined()
  })

  it('redirects an unfinished installation to the prefixed setup page', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ needs_setup: true }) }))
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test:13000/projects'))
    expect(response.headers.get('location')).toBe('http://jrc.test:13000/video/setup')
  })
})
