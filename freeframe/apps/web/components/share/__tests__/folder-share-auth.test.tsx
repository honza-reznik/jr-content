import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FolderShareViewer } from '../folder-share-viewer'

beforeEach(() => {
  localStorage.setItem('ff_access_token', 'folder-member')
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', (media: string) => ({ matches: media.includes('min-width'), media,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }))
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    let body: unknown = { assets: [{ id: 'a1', name: 'Member photo', asset_type: 'image', latest_version_id: 'v1' }], subfolders: [], total: 25 }
    if (new URL(url).searchParams.get('page') === '2') body = { assets: [{ id: 'a2', name: 'Next page photo', asset_type: 'image' }], subfolders: [], total: 25 }
    if (url.includes('/comments?')) body = [{ id: 'c1', body: 'Private member comment', created_at: new Date().toISOString(), guest_name: 'Member' }]
    return new Response(JSON.stringify(body))
  }))
})
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals() })

it('authenticates the initial folder, pagination and preview comments with the share password session', async () => {
  render(<FolderShareViewer token="t" title="Folder" folderName="Folder" description={null}
    permission="comment" allowDownload={false} showVersions={false} shareSession="password-session"
    appearance={{ open_in_viewer: true } as never} branding={null} />)
  await screen.findByText('Member photo')
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await screen.findByText('Next page photo')
  fireEvent.click(screen.getByText('Member photo'))
  await screen.findByText('Private member comment')
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThanOrEqual(3))
  for (const [url, init] of vi.mocked(fetch).mock.calls) {
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer folder-member')
    expect(String(url)).toContain('share_session=password-session')
  }
})
