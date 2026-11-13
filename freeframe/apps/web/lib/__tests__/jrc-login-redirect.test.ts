// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NextRequest } from 'next/server'
import { middleware } from '../../middleware'

beforeEach(() => {
  vi.stubEnv('JRC_MODE', 'prod')
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/jrc/video')
  vi.stubEnv('JRC_PUBLIC_ORIGIN', 'https://jrc.test')
  vi.stubGlobal('fetch', vi.fn())
})
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

it.each(['/login', '/login?from=/projects/p/assets/a?embed=1', '/magic-code', '/set-password',
  '/accept-invite', '/forgot-password', '/invite/token', '/setup', '/register'])('redirects auth page %s through Brightbean', async path => {
  const response = await middleware(new NextRequest(`http://internal/jrc/video${path}`))
  const login = new URL(response.headers.get('location')!)
  expect(login.origin + login.pathname).toBe('https://jrc.test/jrc/accounts/login/')
  const sso = new URL(login.searchParams.get('next')!, login)
  expect(sso.pathname).toBe('/jrc/video-sso/')
  expect(sso.searchParams.get('next')).toBe(path.includes('from=') ? '/projects/p/assets/a?embed=1' : '/projects')
  expect(response.headers.get('cache-control')).toBe('no-store')
  expect(fetch).not.toHaveBeenCalled()
})

it.each(['', '/jrc'])('preserves the player and query with base %s', async base => {
  vi.stubEnv('NEXT_PUBLIC_BASE_PATH', `${base}/video`)
  const response = await middleware(new NextRequest(`http://internal${base}/video/projects/p/assets/a?embed=1`))
  const login = new URL(response.headers.get('location')!)
  const sso = new URL(login.searchParams.get('next')!, login)
  expect(sso.pathname).toBe(`${base}/video-sso/`)
  expect(sso.searchParams.get('next')).toBe('/projects/p/assets/a?embed=1')
})

it.each(['//evil.invalid', '/jrc-sso', '/login', '/projects/../login', '/projects?bad=%2F'])('rejects unsafe return %s', async destination => {
  const response = await middleware(new NextRequest(`http://internal/jrc/video/login?${new URLSearchParams({ from: destination })}`))
  const login = new URL(response.headers.get('location')!)
  expect(new URL(login.searchParams.get('next')!, login).searchParams.get('next')).toBe('/projects')
})

it.each(['/share/token', '/share/token/review', '/jrc-sso', '/jrc-logout'])('keeps %s public without loops', async path => {
  const response = await middleware(new NextRequest(`http://internal/jrc/video${path}`))
  expect(response.headers.get('x-middleware-next')).toBe('1')
})

it('lets signed-in player requests through', async () => {
  const response = await middleware(new NextRequest('http://internal/jrc/video/projects/p/assets/a', {
    headers: { cookie: 'ff_access_token=token' },
  }))
  expect(response.headers.get('x-middleware-next')).toBe('1')
})
