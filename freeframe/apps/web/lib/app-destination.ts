// App-relative destinations for SSO and login's `from`. Auth/public routes
// can perform another redirect, so they must never be navigation targets here.
export function safeAppDestination(value: unknown): string | null {
  if (typeof value !== 'string' || value.length < 1 || value.length > 512 ||
      !value.startsWith('/') || /[\\%#\s\u0000-\u001f\u007f]/.test(value)) return null
  const pathname = value.split('?')[0]
  if (pathname.includes('//') || pathname.split('/').some(part => part === '.' || part === '..')) return null
  if (!/^\/(projects|assets|notifications|settings)(\/|$)/.test(pathname)) return null
  return value
}
