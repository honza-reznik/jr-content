import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

let pathname = '/projects/p1'
let query = ''
const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))

vi.mock('next/navigation', () => ({
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(query),
  useRouter: () => ({ replace }),
}))
vi.mock('@/stores/auth-store', () => ({ useAuthStore: () => ({ fetchUser: vi.fn() }) }))
vi.mock('@/stores/upload-store', () => ({
  useUploadStore: (select?: (s: object) => unknown) => {
    const state = { fetchHistory: vi.fn() }
    return select ? select(state) : state
  },
}))
vi.mock('@/components/layout/sidebar', () => ({ Sidebar: () => <div data-testid="sidebar">rail</div> }))
vi.mock('@/components/layout/header', () => ({ Header: () => <div data-testid="header">header</div> }))
vi.mock('@/components/layout/command-palette', () => ({ CommandPalette: () => <div data-testid="command-palette" /> }))
vi.mock('@/components/layout/uploads-panel', () => ({ UploadsPanel: () => <div data-testid="uploads-panel" /> }))
vi.mock('@/components/layout/upload-sse-bridge', () => ({ UploadSSEBridge: () => <div data-testid="upload-bridge" /> }))
vi.mock('@/components/shared/powered-by-badge', () => ({ PoweredByBadge: () => <div data-testid="badge" /> }))

import DashboardLayout from '../layout'
import { useEmbedMode } from '@/lib/embed-mode'

function EmbedStatus() {
  return <span data-testid="embed-status">{String(useEmbedMode())}</span>
}

beforeEach(() => {
  pathname = '/projects/p1'
  query = ''
  replace.mockClear()
  window.sessionStorage.clear()
})

describe('embedded dashboard shell', () => {
  it('keeps the page full-width and hides dashboard navigation and utilities', () => {
    query = 'embed=1'
    render(<DashboardLayout><><div>player</div><EmbedStatus /></></DashboardLayout>)

    expect(screen.getByText('player')).toBeTruthy()
    expect(screen.queryByTestId('sidebar')).toBeNull()
    expect(screen.queryByTestId('header')).toBeNull()
    expect(screen.queryByTestId('badge')).toBeNull()
    expect(screen.queryByTestId('uploads-panel')).toBeNull()
    expect(screen.queryByTestId('upload-bridge')).toBeNull()
    expect(screen.queryByTestId('command-palette')).toBeNull()
    expect(screen.getByText('player').closest('main')?.className).toContain('ml-0')
    expect(screen.getByTestId('embed-status').textContent).toBe('true')
    expect(window.sessionStorage.getItem('ff_embed')).toBeNull()
  })

  it('adds embed=1 to the next dashboard route after client navigation', () => {
    query = 'embed=1'
    const view = render(<DashboardLayout><div>player</div></DashboardLayout>)
    pathname = '/projects/p2'
    query = ''
    view.rerender(<DashboardLayout><div>next asset</div></DashboardLayout>)

    expect(replace).toHaveBeenCalledWith('/projects/p2?embed=1', { scroll: false })
  })

  it('does not carry embed mode into a fresh ordinary page in the same tab', () => {
    query = 'embed=1'
    const view = render(<DashboardLayout><EmbedStatus /></DashboardLayout>)
    expect(screen.getByTestId('embed-status').textContent).toBe('true')
    view.unmount()

    query = ''
    render(<DashboardLayout><EmbedStatus /></DashboardLayout>)
    expect(screen.getByTestId('embed-status').textContent).toBe('false')
  })
})
