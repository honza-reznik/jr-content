/**
 * F5-B08: JR Content embeds the viewer as `?embed=1&version=<id>`. The page hands that version to
 * the provider (which opens it instead of the newest), and the clock reads "0:17" (not SMPTE).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'

const { search, store, embed, providerProps } = vi.hoisted(() => ({
  providerProps: [] as Array<Record<string, unknown>>,
  search: { value: 'embed=1&version=v2' },
  embed: { on: true },
  store: {
    currentVersion: { id: 'v1', version_number: 1 } as Record<string, unknown>,
    isDrawingMode: false,
    focusedCommentId: null,
    playheadTime: 0,
    seekTo: vi.fn(),
    setFocusedCommentId: vi.fn(),
    setActiveAnnotation: vi.fn(),
    setTimeFormat: vi.fn(),
  },
}))
const versions = [
  { id: 'v1', version_number: 1, processing_status: 'ready' },
  { id: 'v2', version_number: 2, processing_status: 'ready' },
]
const asset = { id: 'a2', name: 'Reel', project_id: 'p1', asset_type: 'video', folder_id: null, latest_version: versions[1] }

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects/p1/assets/a2',
  useSearchParams: () => new URLSearchParams(search.value),
}))
vi.mock('swr', () => ({ default: () => ({ data: undefined }) }))
vi.mock('@/lib/embed-mode', async (original) => ({
  ...(await original<typeof import('@/lib/embed-mode')>()),
  useEmbedMode: () => embed.on,
}))
vi.mock('@/components/review/review-provider', () => ({
  ReviewProvider: ({ children, ...props }: { children: React.ReactNode }) => {
    providerProps.push(props)
    return <>{children}</>
  },
  useReview: () => ({ asset, versions, isLoading: false, refetchComments: vi.fn(), refetchVersions: vi.fn() }),
}))
vi.mock('@/hooks/use-comments', () => ({
  useComments: () => ({ comments: [], createComment: vi.fn(), resolveComment: vi.fn(), deleteComment: vi.fn(), updateComment: vi.fn(), refetch: vi.fn() }),
}))
vi.mock('@/hooks/use-sse', () => ({ useSSE: () => undefined }))
vi.mock('@/hooks/use-page-title', () => ({ usePageTitle: () => undefined }))
vi.mock('@/hooks/use-media-query', () => ({ useMediaQuery: () => true }))
vi.mock('@/hooks/use-media-transport', () => ({ useMediaTransport: () => ({ currentTime: 0, duration: 0, isPlaying: false }) }))
const { storeHook } = vi.hoisted(() => ({
  storeHook: (state: Record<string, unknown>) =>
    (selector?: (s: Record<string, unknown>) => unknown) => (typeof selector === 'function' ? selector(state) : state),
}))
vi.mock('@/stores/auth-store', () => ({ useAuthStore: storeHook({ user: { id: 'u1', name: 'Ella' } }) }))
vi.mock('@/stores/review-store', () => ({ useReviewStore: storeHook(store) }))
vi.mock('@/stores/upload-store', () => ({ useUploadStore: storeHook({ startVersionUpload: vi.fn(), versionsRevision: 0 }) }))
vi.mock('@/stores/view-store', () => ({ useViewStore: storeHook({ sortKey: 'custom', sortDirection: 'asc' }) }))
vi.mock('@/stores/breadcrumb-store', () => ({ useBreadcrumbStore: storeHook({ setExtraCrumbs: vi.fn(), setLabel: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ api: { get: vi.fn().mockResolvedValue([]), post: vi.fn() } }))
vi.mock('@/components/review/video-player', () => ({ VideoPlayer: () => null }))
vi.mock('@/components/review/audio-player', () => ({ AudioPlayer: () => null }))
vi.mock('@/components/review/image-viewer', () => ({ ImageViewer: () => null }))
vi.mock('@/components/review/annotation-canvas', () => ({ AnnotationCanvas: () => null }))
vi.mock('@/components/review/annotation-overlay', () => ({ AnnotationOverlay: () => null }))
vi.mock('@/components/review/comment-panel', () => ({ CommentPanel: () => null }))
vi.mock('@/components/review/comment-input', () => ({ CommentInput: () => null }))
vi.mock('@/components/review/mobile-comment-sheet', () => ({ MobileCommentSheet: () => null, PEEK_PX: 56 }))
vi.mock('@/components/review/version-switcher', () => ({ VersionSwitcher: () => null }))
vi.mock('@/components/review/share-dialog', () => ({ ShareDialog: () => null }))
vi.mock('@/components/review/compare/compare-overlay', () => ({ CompareOverlay: () => null }))

import ReviewPage from '../page'

beforeEach(() => {
  vi.clearAllMocks()
  providerProps.length = 0
  search.value = 'embed=1&version=v2'
  embed.on = true
})

describe('embedded viewer driven by the host page', () => {
  it('asks the provider to open the version named in the address', () => {
    render(<ReviewPage params={{ id: 'p1', assetId: 'a2' }} />)
    expect(providerProps.at(-1)).toMatchObject({ assetId: 'a2', preferredVersionId: 'v2' })
  })

  it('shows the clock as m:ss in the embedded viewer', () => {
    render(<ReviewPage params={{ id: 'p1', assetId: 'a2' }} />)
    expect(store.setTimeFormat).toHaveBeenCalledWith('standard')
  })

  it('leaves the normal viewer alone: no version from the address, timecode stays', () => {
    embed.on = false
    search.value = 'version=v2'
    render(<ReviewPage params={{ id: 'p1', assetId: 'a2' }} />)
    expect(providerProps.at(-1)?.preferredVersionId).toBeNull()
    expect(store.setTimeFormat).not.toHaveBeenCalled()
  })
})
