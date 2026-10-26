import { withBasePath } from './base-path'
import { safeAppDestination } from './app-destination'

const ACCESS_TOKEN_KEY = 'ff_access_token'
const REFRESH_TOKEN_KEY = 'ff_refresh_token'
const SESSION_VERSION_KEY = 'ff_session_version'
const SESSION_LOCK_NAME = 'ff_session_tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

// Read shared storage at response time, not an in-memory value or a storage
// event: another tab may have signed out while this tab awaited the network.
export function getSessionVersion(): string | null {
  return localStorage.getItem(SESSION_VERSION_KEY)
}

function withSessionLock<T>(operation: () => T): Promise<T> {
  // An in-memory mutex or a storage event cannot exclude another tab. Never
  // fall back to an unlocked write, including in an unsupported browser.
  if (typeof navigator === 'undefined' || !navigator.locks?.request) {
    return Promise.reject(new Error('This browser cannot safely update the video session.'))
  }
  return navigator.locks.request(SESSION_LOCK_NAME, operation)
}

export function setTokensIfSessionCurrent(access: string, refresh: string, version: string | null): Promise<boolean> {
  return withSessionLock(() => {
    if (getSessionVersion() !== version) return false
    writeTokens(access, refresh)
    return true
  })
}

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setTokens(access: string, refresh: string): Promise<void> {
  return withSessionLock(() => writeTokens(access, refresh))
}

// Re-read the pair under the same lock as logout; never restore a cookie
// from a token captured before another tab ended the session.
export function restoreSessionCookies(): Promise<boolean> {
  return withSessionLock(() => {
    const access = getAccessToken()
    const refresh = getRefreshToken()
    if (!access || !refresh) return false
    writeTokens(access, refresh)
    return true
  })
}

// Only call these storage writers while holding SESSION_LOCK_NAME. The check,
// marker, localStorage pair and cookies belong to the same critical section.
function writeTokens(access: string, refresh: string): void {
  localStorage.setItem(SESSION_VERSION_KEY, crypto.randomUUID())
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
  // Set cookies so middleware can check auth on server side
  document.cookie = `${ACCESS_TOKEN_KEY}=${access}; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=${60 * 60 * 24 * 7}; SameSite=Lax${window.location.protocol === 'https:' ? '; Secure' : ''}`
  document.cookie = `${REFRESH_TOKEN_KEY}=${refresh}; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=${60 * 60 * 24 * 7}; SameSite=Lax${window.location.protocol === 'https:' ? '; Secure' : ''}`
}

export function removeTokens(): Promise<void> {
  return withSessionLock(deleteTokens)
}

function deleteTokens(): void {
  // Keep the marker after logout, including when there were no stored tokens.
  localStorage.setItem(SESSION_VERSION_KEY, crypto.randomUUID())
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  // Clear auth cookies
  document.cookie = `${ACCESS_TOKEN_KEY}=; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=0`
  document.cookie = `${REFRESH_TOKEN_KEY}=; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=0`
}

export async function clearTokens(version?: string | null): Promise<void> {
  const cleared = await withSessionLock(() => {
    if (version !== undefined && getSessionVersion() !== version) return false
    deleteTokens()
    return true
  })
  if (cleared) {
    const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''
    const pathname = window.location.pathname
    const appPath = basePath && (pathname === basePath || pathname.startsWith(`${basePath}/`))
      ? pathname.slice(basePath.length) || '/'
      : pathname
    const destination = safeAppDestination(`${appPath}${window.location.search}`) || '/projects'
    const loginUrl = new URL(withBasePath('/login'), window.location.origin)
    loginUrl.searchParams.set('from', destination)
    window.location.href = `${loginUrl.pathname}${loginUrl.search}`
  }
}

// Deduplicate concurrent refresh calls — when access token expires, multiple
// API calls may simultaneously get 401 and try to refresh. Only one should run.
let _refreshPromise: Promise<string | null> | null = null

export async function refreshAccessToken(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise

  _refreshPromise = _doRefresh()
  try {
    return await _refreshPromise
  } finally {
    _refreshPromise = null
  }
}

async function _doRefresh(): Promise<string | null> {
  if (typeof navigator === 'undefined' || !navigator.locks?.request) return null
  const version = getSessionVersion()
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    await clearTokens(version)
    return null
  }

  try {
    const response = await fetch(`${API_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!response.ok) {
      // Only a refusal ends the session. 401 or 403 is the server saying this
      // refresh token is no longer valid, which is what signing out is for.
      // Anything else -- a 502 from a proxy mid-deploy, a 500, a rate limit --
      // is the server being briefly unavailable, and signing the user out for
      // it loses whatever they had open.
      if (response.status === 401 || response.status === 403) {
        await clearTokens(version)
      }
      return null
    }

    const data = await response.json()
    const newAccessToken: string = data.access_token
    const newRefreshToken: string = data.refresh_token ?? refreshToken

    if (!await setTokensIfSessionCurrent(newAccessToken, newRefreshToken, version)) return null
    return newAccessToken
  } catch {
    // Transport failures preserve the session until the network comes back.
    // Lock/storage failures also fail closed: never retry an unlocked write.
    // Callers treat null as "could not refresh" and surface the original error.
    return null
  }
}
