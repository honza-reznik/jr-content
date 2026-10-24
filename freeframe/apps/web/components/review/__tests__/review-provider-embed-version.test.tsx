import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'
import { ReviewProvider, useReview } from '../review-provider'

// F5-B08: embedded in JR Content the viewer opens the version the host page asked for
// (?version=<id>), not the newest one, so the wrong video is never loaded first.

const versions = [
  { id: 'v1', asset_id: 'a1', version_number: 1, processing_status: 'ready', files: [] },
  { id: 'v2', asset_id: 'a1', version_number: 2, processing_status: 'ready', files: [] },
]
const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({ api: { get, post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))

beforeEach(() => {
  useReviewStore.getState().reset()
  get.mockReset()
  get.mockImplementation(async (path: string) => {
    if (path === '/assets/a1/versions') return versions.map((v) => ({ ...v }))
    if (path === '/assets/a1') return { id: 'a1', asset_type: 'video', latest_version: versions[1] }
    return []
  })
})

function open(preferredVersionId?: string | null) {
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ReviewProvider assetId="a1" preferredVersionId={preferredVersionId}>{children}</ReviewProvider>
  )
  return renderHook(() => useReview(), { wrapper })
}

describe('ReviewProvider with a preferred version', () => {
  it('opens the requested version of this asset', async () => {
    const { result } = open('v1')
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(useReviewStore.getState().currentVersion?.id).toBe('v1')
  })

  it('opens the newest ready version when nothing or a foreign id is requested', async () => {
    for (const requested of [undefined, null, 'other']) {
      useReviewStore.getState().reset()
      const { result } = open(requested)
      await waitFor(() => expect(result.current.isLoading).toBe(false))
      expect(useReviewStore.getState().currentVersion?.id).toBe('v2')
    }
  })
})
