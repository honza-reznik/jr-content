import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useReviewStore } from '@/stores/review-store'

// P1-B26 (from P1-B30): a failed asset load ends loading and keeps the HTTP status,
// so the page can say "no access" or "no longer exists" instead of loading forever.

const { get, ApiError } = vi.hoisted(() => {
  class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) { super(detail); this.status = status; this.detail = detail }
  }
  return { get: vi.fn(), ApiError }
})
vi.mock('@/lib/api', () => ({ api: { get, post: vi.fn(), patch: vi.fn(), delete: vi.fn() }, ApiError }))

import { ReviewProvider, useReview } from '../review-provider'

function open() {
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ReviewProvider assetId="a1">{children}</ReviewProvider>
  )
  return renderHook(() => useReview(), { wrapper })
}

beforeEach(() => {
  useReviewStore.getState().reset()
  get.mockReset()
})

describe('ReviewProvider when the asset cannot be loaded', () => {
  it.each([403, 404, 500])('stops loading and reports status %s', async (status) => {
    get.mockImplementation(async (path: string) => {
      if (path === '/assets/a1') throw new ApiError(status, 'nope')
      return []
    })
    const { result } = open()
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.asset).toBeNull()
    expect(result.current.errorStatus).toBe(status)
    expect(result.current.error).toBe('nope')
  })

  it('reports no status for a failure that is not an HTTP answer', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/assets/a1') throw new TypeError('Failed to fetch')
      return []
    })
    const { result } = open()
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.errorStatus).toBeNull()
  })

  it('has no error after a successful load', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/assets/a1') return { id: 'a1', asset_type: 'video', latest_version: null }
      return []
    })
    const { result } = open()
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.asset?.id).toBe('a1')
    expect(result.current.errorStatus).toBeNull()
  })
})
