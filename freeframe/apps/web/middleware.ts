import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { withBasePath } from './lib/base-path'
import { safeAppDestination } from './lib/app-destination'

const PUBLIC_ROUTES = ['/login', '/setup', '/jrc-sso', '/jrc-logout']
const PUBLIC_PREFIXES = ['/invite/', '/share/']

// Server reaches the API over the private container network.
const API_URL = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

function isPublicRoute(pathname: string): boolean {
  if (PUBLIC_ROUTES.includes(pathname)) return true
  if (PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return true
  return false
}

export async function middleware(request: NextRequest) {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''
  const rawPathname = request.nextUrl.pathname
  const pathname = basePath && (rawPathname === basePath || rawPathname.startsWith(`${basePath}/`))
    ? rawPathname.slice(basePath.length) || '/'
    : rawPathname

  // Always allow public routes
  if (isPublicRoute(pathname)) {
    return NextResponse.next()
  }

  // Check if setup is needed — redirect to /setup if no superadmin exists
  // Uses a cookie cache to avoid calling the API on every request
  const setupDone = request.cookies.get('ff_setup_done')?.value
  let cacheSetupDone = false
  if (!setupDone) {
    try {
      const res = await fetch(`${API_URL}/setup/status`, {
        next: { revalidate: 60 }, // Cache for 60 seconds
      })
      if (res.ok) {
        const data = await res.json()
        if (data.needs_setup) {
          return NextResponse.redirect(new URL(withBasePath('/setup'), request.url))
        }
        // Cache setup only after the auth check below has also run.
        cacheSetupDone = true
      }
    } catch {
      // API unreachable — let the request through, the page will show errors
    }
  }

  // Check for auth tokens
  const accessToken = request.cookies.get('ff_access_token')?.value
  const refreshToken = request.cookies.get('ff_refresh_token')?.value

  let response: NextResponse
  if (!accessToken && !refreshToken) {
    const loginUrl = new URL(withBasePath('/login'), request.url)
    const candidate = `${pathname}${request.nextUrl.search}`
    loginUrl.searchParams.set('from', safeAppDestination(candidate) ?? safeAppDestination(pathname) ?? '/projects')
    response = NextResponse.redirect(loginUrl)
  } else {
    response = NextResponse.next()
  }

  if (cacheSetupDone) {
    response.cookies.set('ff_setup_done', '1', { path: '/', maxAge: 60 * 60 * 24 })
  }
  return response
}

export const config = {
  matcher: [
    /*
     * Match all paths except:
     * - _next/static (static files)
     * - _next/image (image optimization)
     * - favicon.ico
     * - api routes
     * - public assets (images, fonts, etc.)
     */
    '/((?!_next/static|_next/image|favicon\\.ico|api/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff|woff2|ttf|otf)).*)',
  ],
}
