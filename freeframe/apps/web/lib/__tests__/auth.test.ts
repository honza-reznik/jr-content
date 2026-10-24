import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setTokens, getAccessToken, getRefreshToken, clearTokens } from '../auth'
import { mockSessionLocks } from '@/test/session-locks'

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs() })

describe('Token management', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    mockSessionLocks()
  })

  it('setTokens stores access and refresh tokens in localStorage', async () => {
    await setTokens('access-123', 'refresh-456')
    expect(localStorage.getItem('ff_access_token')).toBe('access-123')
    expect(localStorage.getItem('ff_refresh_token')).toBe('refresh-456')
  })

  it('getAccessToken retrieves access token from localStorage', () => {
    localStorage.setItem('ff_access_token', 'my-access-token')
    expect(getAccessToken()).toBe('my-access-token')
  })

  it('getAccessToken returns null when no token stored', () => {
    expect(getAccessToken()).toBeNull()
  })

  it('getRefreshToken retrieves refresh token from localStorage', () => {
    localStorage.setItem('ff_refresh_token', 'my-refresh-token')
    expect(getRefreshToken()).toBe('my-refresh-token')
  })

  it('getRefreshToken returns null when no token stored', () => {
    expect(getRefreshToken()).toBeNull()
  })

  it('clearTokens removes both tokens from localStorage', async () => {
    localStorage.setItem('ff_access_token', 'access-123')
    localStorage.setItem('ff_refresh_token', 'refresh-456')

    // Mock window.location.href setter to avoid navigation errors
    const locationMock = {
      href: '', origin: 'http://jrc.test', pathname: '/projects', search: '',
    }
    vi.stubGlobal('location', locationMock)

    await clearTokens()

    expect(localStorage.getItem('ff_access_token')).toBeNull()
    expect(localStorage.getItem('ff_refresh_token')).toBeNull()
  })

  it('clearTokens redirects to /login', async () => {
    const locationMock = {
      href: '', origin: 'http://jrc.test', pathname: '/projects', search: '',
    }
    vi.stubGlobal('location', locationMock)

    await clearTokens()

    expect(window.location.href).toBe('/login?from=%2Fprojects')
  })

  it('keeps the current embed asset as the safe return destination after session expiry', async () => {
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/video')
    const locationMock = {
      href: '', origin: 'http://jrc.test', pathname: '/video/projects/p/assets/a', search: '?embed=1',
    }
    vi.stubGlobal('location', locationMock)

    await clearTokens()

    expect(window.location.href).toBe('/video/login?from=%2Fprojects%2Fp%2Fassets%2Fa%3Fembed%3D1')
  })

  it('uses the projects fallback when the current route is not a safe login destination', async () => {
    vi.stubEnv('NEXT_PUBLIC_BASE_PATH', '/video')
    const locationMock = {
      href: '', origin: 'http://jrc.test', pathname: '/video/login', search: '?from=%2F%2Fevil.test',
    }
    vi.stubGlobal('location', locationMock)

    await clearTokens()

    expect(window.location.href).toBe('/video/login?from=%2Fprojects')
  })
})
