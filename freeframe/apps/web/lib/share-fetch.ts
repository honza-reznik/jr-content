import { getAccessToken, refreshAccessToken } from './auth'

/** Share links can also require live project membership. Read the current session
 * for every request, including pagination and downloads, while allowing guests. */
export async function shareFetch(url: string, init?: RequestInit): Promise<Response> {
  const execute = (token: string | null) => {
    const headers = new Headers(init?.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    return fetch(url, { ...init, headers })
  }
  let token: string | null = null
  try { token = getAccessToken() } catch { /* Public shares also work without browser storage. */ }
  const response = await execute(token)
  if (response.status === 401 && token) {
    const refreshed = await refreshAccessToken()
    if (refreshed) return execute(refreshed)
  }
  return response
}
