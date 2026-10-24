import { render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import LoginPage from '@/app/(auth)/login/page'

const replace = vi.hoisted(() => vi.fn())
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }))
vi.mock('@/lib/auth', () => ({ restoreSessionCookies: async () => true }))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn() } }))
vi.mock('@/components/auth/login-form', () => ({ LoginForm: () => null }))
vi.mock('@/components/shared/powered-by-badge', () => ({ PoweredByBadge: () => null }))
const originalLocation = window.location

beforeEach(() => { replace.mockClear() })
afterEach(() => Object.defineProperty(window, 'location', { value: originalLocation, configurable: true }))

it.each([
  ['https://foreign.invalid', '/projects'], ['//foreign.invalid', '/projects'],
  ['/\\foreign.invalid', '/projects'], ['/projects/../../login?from=https://foreign.invalid', '/projects'],
  ['/%2f%2fforeign.invalid', '/projects'], ['/login?from=https://foreign.invalid', '/projects'],
  ['/jrc-logout', '/projects'], ['javascript:alert(1)', '/projects'],
  ['/projects/p?embed=1', '/projects/p?embed=1'], ['/settings/profile', '/settings/profile'],
])('validates login from=%s before navigating a signed-in user', async (from, destination) => {
  Object.defineProperty(window, 'location', {
    value: { search: `?from=${encodeURIComponent(from)}` }, configurable: true,
  })
  render(<LoginPage />)
  await waitFor(() => expect(replace).toHaveBeenCalledWith(destination))
})
