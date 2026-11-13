import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { downloadAsset } from '@/lib/asset-download'

vi.mock('@/lib/api', () => ({ api: { get: vi.fn() } }))

afterEach(() => {
  vi.mocked(api.get).mockReset()
  vi.restoreAllMocks()
  document.querySelectorAll('iframe').forEach((frame) => frame.remove())
})

describe('downloadAsset (P2-B05)', () => {
  it('returns the fallback note for the page to show, never a browser dialog', async () => {
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.mocked(api.get).mockResolvedValue({
      url: '/files/video/processed/download.mp4',
      download_note: 'The original upload is unavailable. Downloading the converted MP4 instead.',
    })

    const note = await downloadAsset('asset-1', undefined, false, 'version-1')

    expect(note).toBe('The original upload is unavailable. Downloading the converted MP4 instead.')
    expect(alert).not.toHaveBeenCalled()
    expect(vi.mocked(api.get).mock.calls[0][0]).toBe('/assets/asset-1/stream?download=true&version_id=version-1')
    expect(document.querySelector('iframe')?.getAttribute('src')).toBe('/files/video/processed/download.mp4')
  })

  it('returns null when the requested file is served', async () => {
    vi.mocked(api.get).mockResolvedValue({ url: '/files/video/raw/original.mov', download_note: null })
    expect(await downloadAsset('asset-1')).toBeNull()
  })
})
