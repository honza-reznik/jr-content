/**
 * P1-B26 (from P1-B30): when the asset cannot be loaded -- no access, deleted, a
 * server error -- the review page says so instead of "Loading asset..." forever.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

const { review } = vi.hoisted(() => ({
  review: { asset: null as unknown, isLoading: false, errorStatus: null as number | null },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects/p1/assets/a2',
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('swr', () => ({ default: () => ({ data: undefined }) }))
vi.mock('@/components/review/review-provider', () => ({
  ReviewProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReview: () => ({
    asset: review.asset,
    versions: [],
    isLoading: review.isLoading,
    errorStatus: review.errorStatus,
    refetchComments: vi.fn(),
    refetchVersions: vi.fn(),
  }),
}))
vi.mock('@/hooks/use-comments', () => ({
  useComments: () => ({ comments: [], createComment: vi.fn(), resolveComment: vi.fn(), deleteComment: vi.fn(), addReaction: vi.fn(), removeReaction: vi.fn() }),
}))
vi.mock('@/hooks/use-sse', () => ({ useSSE: () => undefined }))
vi.mock('@/hooks/use-page-title', () => ({ usePageTitle: () => undefined }))
vi.mock('@/hooks/use-media-query', () => ({ useMediaQuery: () => false }))
vi.mock('@/hooks/use-media-transport', () => ({ useMediaTransport: () => ({ isPlaying: false, togglePlay: vi.fn() }) }))
const { storeHook } = vi.hoisted(() => ({
  storeHook: (state: Record<string, unknown>) =>
    (selector?: (s: Record<string, unknown>) => unknown) =>
      (typeof selector === 'function' ? selector(state) : state),
}))
vi.mock('@/stores/auth-store', () => ({ useAuthStore: storeHook({ user: { id: 'u1' } }) }))
vi.mock('@/stores/review-store', () => ({
  useReviewStore: storeHook({
    currentVersion: null, isDrawingMode: false, focusedCommentId: null, playheadTime: 0,
    seekTo: vi.fn(), setFocusedCommentId: vi.fn(), setActiveAnnotation: vi.fn(), setTimeFormat: vi.fn(),
  }),
}))
vi.mock('@/stores/upload-store', () => ({ useUploadStore: storeHook({ startVersionUpload: vi.fn(), versionsRevision: 0 }) }))
vi.mock('@/stores/view-store', () => ({ useViewStore: storeHook({ sortKey: 'custom', sortDirection: 'asc' }) }))
vi.mock('@/stores/breadcrumb-store', () => ({ useBreadcrumbStore: storeHook({ setExtraCrumbs: vi.fn(), setLabel: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn().mockResolvedValue([]), post: vi.fn() } }))
vi.mock('@/components/review/mobile-comment-sheet', () => ({ MobileCommentSheet: () => null, PEEK_PX: 56 }))

import ReviewPage from '../page'

function renderPage() {
  return render(<ReviewPage params={{ id: 'p1', assetId: 'a2' }} />)
}

beforeEach(() => {
  review.asset = null
  review.isLoading = false
  review.errorStatus = null
})

describe('review page without an asset', () => {
  it('shows the loading state only while loading', () => {
    review.isLoading = true
    renderPage()
    expect(screen.getByText('Loading asset...')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it.each([
    [403, 'You do not have access to video.'],
    [404, 'This video no longer exists.'],
    [500, 'The video could not be loaded.'],
    [null, 'The video could not be loaded.'],
  ])('after status %s it says why', (status, title) => {
    review.errorStatus = status
    renderPage()
    expect(screen.queryByText('Loading asset...')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain(title)
    expect(Boolean(screen.queryByRole('button', { name: 'Try again' }))).toBe(status !== 403 && status !== 404)
  })
})
