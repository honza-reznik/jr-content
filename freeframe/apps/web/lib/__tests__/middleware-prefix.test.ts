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
  for (const basePath of ['', '/video']) {
    for (const prefix of ['', '/video']) {
      for (const route of ['/jrc-sso', '/jrc-logout']) {
        for (const cookie of ['', 'ff_setup_done=1']) {
          // /video is an app prefix only when configured as such.
          if (prefix && !basePath) continue
          it(`allows ${prefix}${route} with base ${basePath} and cookie ${cookie}`, async () => {
            vi.stubEnv('NEXT_PUBLIC_BASE_PATH', basePath)
            const fetcher = vi.fn()
            vi.stubGlobal('fetch', fetcher)
            const { middleware } = await import('../../middleware')
            const response = await middleware(new NextRequest(`http://jrc.test${prefix}${route}`, {
              headers: { cookie },
            }))
            expect(response.headers.get('x-middleware-next')).toBe('1')
            expect(fetcher).not.toHaveBeenCalled()
          })
        }
      }
    }
  }

  it('does not treat SSO-like prefixes as public routes', async () => {
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test/video/jrc-sso-elsewhere', {
      headers: { cookie: 'ff_setup_done=1' },
    }))
    expect(response.status).toBe(307)
  })

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

  it('preserves a validated embed query through the login redirect', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ needs_setup: false }) }))
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test/video/projects/p/assets/a?embed=1'))
    const location = new URL(response.headers.get('location')!)
    expect(location.searchParams.get('from')).toBe('/projects/p/assets/a?embed=1')
  })

  it('drops a query rejected by the existing destination validation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ needs_setup: false }) }))
    const { middleware } = await import('../../middleware')
    const response = await middleware(new NextRequest('http://jrc.test/video/projects/p?next=%2F%2Fevil.invalid'))
    const location = new URL(response.headers.get('location')!)
    expect(location.searchParams.get('from')).toBe('/projects/p')
    expect(location.origin).toBe('http://jrc.test')
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
