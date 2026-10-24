import { withBasePath } from './base-path'
import { getSessionVersion, setTokensIfSessionCurrent } from './auth'
import { safeAppDestination } from './app-destination'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const BINDING_COOKIE = 'jrc_sso_binding'

export function safeVideoDestination(value: unknown): string | null {
  if (value == null) return withBasePath('/projects')
  const destination = safeAppDestination(value)
  return destination ? withBasePath(destination) : null
}

export function clearSsoBinding(): void {
  document.cookie = `${BINDING_COOKIE}=; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=0; SameSite=Strict`
}

export async function exchangeBrowserTicket(): Promise<string> {
  const version = getSessionVersion()
  const fragment = window.location.hash
  // Replace this history entry before any await, validation or request. Never
  // read a ticket or a navigation destination from the query string.
  window.history.replaceState(null, '', window.location.pathname)
  const binding = document.cookie.split('; ').find(part => part.startsWith(`${BINDING_COOKIE}=`))?.split('=')[1]
  clearSsoBinding()
  const params = new URLSearchParams(fragment.slice(1))
  const ticket = params.get('ticket')
  if (!ticket || ticket.length > 4096 || params.getAll('ticket').length !== 1 || !binding) throw new Error('Sign-in unavailable')
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(ticket))
  const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  if (fingerprint !== binding) throw new Error('Sign-in unavailable')
  // A direct request avoids retrying a single-use ticket or refreshing an old
  // user's session on a rejected exchange. Never print the response or ticket.
  const response = await fetch(`${API_URL}/auth/jrc-exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'omit',
    cache: 'no-store',
    referrerPolicy: 'no-referrer',
    body: JSON.stringify({ ticket }),
  })
  if (!response.ok) throw new Error('Sign-in unavailable')
  const data = await response.json()
  const destination = safeVideoDestination(data.next)
  if (!destination || typeof data.access_token !== 'string' || !data.access_token ||
      typeof data.refresh_token !== 'string' || !data.refresh_token) throw new Error('Sign-in unavailable')
  if (!await setTokensIfSessionCurrent(data.access_token, data.refresh_token, version)) throw new Error('Sign-in unavailable')
  return destination
}
