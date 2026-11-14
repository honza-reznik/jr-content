import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { useAuthStore } from '@/stores/auth-store'

// JR Content accounts never download from FreeFrame, so the share dialogs must not offer
// "Allow downloads" to them (the API forces it off); other accounts keep the switch.

const get = vi.fn()
vi.mock('@/lib/api', () => ({
  api: { get: (...a: unknown[]) => get(...a), patch: vi.fn(), post: vi.fn() },
}))

import { ShareCreateDialog } from '../share-create-dialog'
import { ShareLinkSettingsPanel } from '../share-link-detail'

const link = {
  id: 'l1', token: 'tok', title: 'Link', permission: 'comment', visibility: 'public',
  allow_download: false, show_versions: true, show_watermark: false, is_enabled: true,
  has_password: false, appearance: {}, created_at: '2026-10-01T00:00:00Z', expires_at: null,
}

function dialog() {
  return render(
    <ShareCreateDialog
      open
      onOpenChange={vi.fn()}
      projectId="p1"
      currentFolderId={null}
      assets={[]}
      folders={[]}
      preselectedItem={{ type: 'folder', id: 'f1', name: 'Dailies' }}
      onShareCreated={vi.fn()}
    />,
  )
}

function panel() {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ShareLinkSettingsPanel token="tok" />
    </SWRConfig>,
  )
}

describe.each([
  { managed: true, shown: false },
  { managed: false, shown: true },
])('download switch, jrc_managed=$managed', ({ managed, shown }) => {
  beforeEach(() => {
    useAuthStore.setState({ user: { id: 'u1', jrc_managed: managed } as never })
    get.mockReset().mockResolvedValue(link)
  })
  afterEach(() => useAuthStore.setState({ user: null }))

  it('create dialog', () => {
    dialog()
    expect(!!screen.queryByText('Allow downloads')).toBe(shown)
  })

  it('link settings panel', async () => {
    panel()
    await screen.findByText('Show all versions')
    await waitFor(() => expect(!!screen.queryByText('Downloads')).toBe(shown))
  })
})
