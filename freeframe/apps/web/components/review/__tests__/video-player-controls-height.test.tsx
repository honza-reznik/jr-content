import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as React from 'react'
import { render, act } from '@testing-library/react'

// P1-B26 (N-k1e-07): the player reports the real height of its controls, so the
// review screens can keep Play above the mobile comment sheet. With a comment the
// progress area gains a row of markers, which a fixed 60px did not count.

vi.mock('@/lib/api', () => ({ api: { get: vi.fn(async () => ({ data: {} })) } }))
vi.mock('../review-provider', () => ({ useReview: () => ({ registerPauseHandler: () => {} }) }))
vi.mock('@/hooks/use-video-player', () => ({
  useVideoPlayer: () => ({
    videoRef: { current: null }, hlsRef: { current: null },
    isPlaying: false, currentTime: 0, duration: 100, buffered: 0,
    volume: 1, isMuted: false, playbackRate: 1,
    qualityLevels: [], currentQuality: -1, selectedQuality: -1, isLoading: false, isFullscreen: false, error: null,
    pause: () => {}, togglePlay: () => {}, seek: () => {}, setPlaybackRate: () => {},
    setQuality: () => {}, setVolume: () => {}, toggleMute: () => {}, toggleFullscreen: () => {},
  }),
}))

import { VideoPlayer } from '../video-player'

vi.stubGlobal('matchMedia', (query: string) => ({
  matches: false, media: query, onchange: null,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
}))

const observers: Array<{ cb: () => void; targets: Element[] }> = []
let heights = new Map<Element, number>()
beforeEach(() => {
  observers.length = 0
  heights = new Map()
  vi.stubGlobal('ResizeObserver', class {
    cb: () => void
    targets: Element[] = []
    constructor(cb: () => void) { this.cb = cb; observers.push(this as any) }
    observe(el: Element) { this.targets.push(el) }
    unobserve() {}
    disconnect() { this.targets = [] }
  })
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const height = heights.get(this) ?? 0
    return { height, width: 0, top: 0, left: 0, right: 0, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect
  })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => vi.restoreAllMocks())

const props = { assetId: 'a1', comments: [], initialStreamUrl: 'x.m3u8' } as unknown as React.ComponentProps<typeof VideoPlayer>

describe('VideoPlayer onControlsHeight', () => {
  it('reports the progress area plus the transport row, and again when they resize', () => {
    const reported: number[] = []
    const { container } = render(<VideoPlayer {...props} onControlsHeight={(px) => reported.push(px)} />)
    const progress = container.querySelector('[data-player-controls="progress"]')!
    const transport = container.querySelector('[data-player-controls="transport"]')!
    // Everything below the stage: the Play button is in the transport row.
    expect(transport.querySelector('[aria-label="Play"]')).toBeTruthy()
    const observer = observers.find((o) => o.targets.includes(progress) && o.targets.includes(transport))
    expect(observer).toBeTruthy()

    heights.set(progress, 12).set(transport, 48)
    act(() => observer!.cb())
    expect(reported.at(-1)).toBe(60)

    // A comment adds the marker row (h-6 + mt-0.5).
    heights.set(progress, 39)
    act(() => observer!.cb())
    expect(reported.at(-1)).toBe(87)
  })

  it('does not observe the controls without a listener', () => {
    const { container } = render(<VideoPlayer {...props} />)
    const progress = container.querySelector('[data-player-controls="progress"]')!
    expect(observers.some((o) => o.targets.includes(progress))).toBe(false)
  })
})
