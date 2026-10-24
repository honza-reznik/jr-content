const GRANT_COOKIE = 'jrc_logout_binding'

function readGrant(): string | undefined {
  return document.cookie.split('; ').find(part => part.startsWith(`${GRANT_COOKIE}=`))?.split('=')[1]
}

export async function consumeLogoutGrant(): Promise<boolean> {
  const fragment = window.location.hash
  window.history.replaceState(null, '', window.location.pathname)
  const params = new URLSearchParams(fragment.slice(1))
  const grant = params.get('grant')
  const binding = readGrant()
  if (!binding || !grant || !/^[A-Za-z0-9_-]{43}$/.test(grant) || params.getAll('grant').length !== 1) return false
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(grant))
  const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  if (fingerprint !== binding || readGrant() !== binding) return false
  document.cookie = `${GRANT_COOKIE}=; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=0; SameSite=Strict`
  return true
}
