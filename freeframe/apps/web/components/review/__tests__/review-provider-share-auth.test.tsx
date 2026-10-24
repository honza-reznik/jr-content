import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { ReviewProvider, useReview } from '../review-provider'
import { useReviewStore } from '@/stores/review-store'

beforeEach(() => {
  useReviewStore.getState().reset()
  localStorage.setItem('ff_access_token', 'current-member')
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    let body: unknown = { url: '/signed', version_id: 'v1', asset_type: 'image', name: 'Photo' }
    if (url.includes('/versions')) body = [{ id: 'v1', version_number: 1, processing_status: 'ready' }]
    if (url.includes('/comments?')) body = []
    if (init?.method === 'POST') body = { id: 'c1', body: 'Member comment', replies: [] }
    return new Response(JSON.stringify(body))
  }))
})
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); useReviewStore.getState().reset() })

it('authenticates streams, versions, comment reads/writes and switching versions with the password session', async () => {
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ReviewProvider assetId="a1" shareToken="t" shareSession="password-session">{children}</ReviewProvider>
  )
  const { result } = renderHook(() => useReview(), { wrapper })
  await waitFor(() => expect(result.current.isLoading).toBe(false))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/comments?'))).toBe(true))
  await act(async () => { await result.current.addComment({ body: 'Member comment' }) })
  act(() => useReviewStore.getState().setCurrentVersion({ id: 'v0' } as never))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('version_id=v0'))).toBe(true))
  for (const [url, init] of vi.mocked(fetch).mock.calls) {
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer current-member')
    expect(String(url)).toContain('share_session=password-session')
  }
})
