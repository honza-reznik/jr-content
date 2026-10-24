import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import JrcSsoPage from '@/app/(auth)/jrc-sso/page'
import JrcLogoutPage from '@/app/(auth)/jrc-logout/page'
import { exchangeBrowserTicket, clearSsoBinding } from '@/lib/jrc-sso'
import { removeTokens } from '@/lib/auth'

vi.mock('@/lib/jrc-sso', () => ({ exchangeBrowserTicket: vi.fn(), clearSsoBinding: vi.fn() }))
vi.mock('@/lib/auth', () => ({ removeTokens: vi.fn() }))
const replace = vi.fn()
const originalLocation = window.location
beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'location', {
    value: { replace, search: '?next=//foreign.invalid' }, writable: true, configurable: true,
  })
})
afterEach(() => Object.defineProperty(window, 'location', { value: originalLocation, writable: true, configurable: true }))

it('exchanges once under StrictMode and replaces the SSO history entry', async () => {
  vi.mocked(exchangeBrowserTicket).mockResolvedValue('/video/projects/p')
  render(<StrictMode><JrcSsoPage /></StrictMode>)
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/video/projects/p'))
  expect(exchangeBrowserTicket).toHaveBeenCalledOnce()
})

it('offers a plain return link without exposing technical errors or tickets', async () => {
  vi.mocked(exchangeBrowserTicket).mockRejectedValue(new Error('SECRET-TICKET technical failure'))
  render(<JrcSsoPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Video sign-in could not be completed')
  expect(screen.getByRole('link')).toHaveAttribute('href', '/')
  expect(screen.queryByText(/SECRET-TICKET/)).toBeNull()
})

it('clears all FreeFrame cookies and tokens and returns to a fixed Brightbean address', () => {
  document.cookie = 'ff_setup_done=1; path=/'
  render(<JrcLogoutPage />)
  expect(removeTokens).toHaveBeenCalled()
  expect(clearSsoBinding).toHaveBeenCalled()
  expect(document.cookie).not.toContain('ff_setup_done')
  expect(replace).toHaveBeenCalledWith('/accounts/login/')
})
