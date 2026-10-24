import { StrictMode } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import JrcSsoPage from '@/app/(auth)/jrc-sso/page'
import JrcLogoutPage from '@/app/(auth)/jrc-logout/page'
import { exchangeBrowserTicket, clearSsoBinding } from '@/lib/jrc-sso'
import { removeTokens } from '@/lib/auth'
import { consumeLogoutGrant } from '@/lib/jrc-logout'

vi.mock('@/lib/jrc-sso', () => ({ exchangeBrowserTicket: vi.fn(), clearSsoBinding: vi.fn() }))
vi.mock('@/lib/auth', () => ({ removeTokens: vi.fn() }))
vi.mock('@/lib/jrc-logout', () => ({ consumeLogoutGrant: vi.fn() }))
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

it('clears all FreeFrame cookies only with a grant and returns to a fixed Brightbean address', async () => {
  vi.mocked(consumeLogoutGrant).mockResolvedValue(true)
  document.cookie = 'ff_setup_done=1; path=/'
  render(<StrictMode><JrcLogoutPage /></StrictMode>)
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/accounts/login/'))
  expect(consumeLogoutGrant).toHaveBeenCalledOnce()
  expect(removeTokens).toHaveBeenCalled()
  expect(clearSsoBinding).toHaveBeenCalled()
  expect(document.cookie).not.toContain('ff_setup_done')
  expect(replace).toHaveBeenCalledWith('/accounts/login/')
})

it('requires an explicit click for a direct or forged logout link', async () => {
  vi.mocked(consumeLogoutGrant).mockResolvedValue(false)
  document.cookie = 'ff_setup_done=1; path=/'
  render(<JrcLogoutPage />)
  const button = await screen.findByRole('button', { name: 'Sign out of video' })
  expect(removeTokens).not.toHaveBeenCalled()
  expect(clearSsoBinding).not.toHaveBeenCalled()
  expect(replace).not.toHaveBeenCalled()
  expect(document.cookie).toContain('ff_setup_done')
  fireEvent.click(button)
  expect(removeTokens).toHaveBeenCalledOnce()
  expect(replace).toHaveBeenCalledWith('/accounts/login/')
})
