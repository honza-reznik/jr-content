import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useVideoPlayer, type UseVideoPlayerReturn } from '../use-video-player'

vi.mock('hls.js', () => ({ default: { isSupported: () => false } }))
let player: UseVideoPlayerReturn
const manifest = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n0/playlist.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n1/playlist.m3u8\n'
function Harness({ src = '/stream/hls/master.m3u8' }: { src?: string }) {
  player = useVideoPlayer(src)
  return <video ref={player.videoRef} />
}
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('maybe')
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, text: async () => manifest })))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('native HLS starts on the 720p variant and switches to 1080p only by choice', async () => {
  const view = render(<Harness />)
  const video = view.container.querySelector('video')!
  await waitFor(() => expect(video.src).toContain('/1/playlist.m3u8'))
  expect(player.selectedQuality).toBe(-1)
  expect(fetch).toHaveBeenCalledWith('/stream/hls/master.m3u8', expect.objectContaining({ credentials: 'same-origin' }))
  video.currentTime = 3
  act(() => player.setQuality(0))
  expect(video.src).toContain('/0/playlist.m3u8')
  act(() => video.dispatchEvent(new Event('loadedmetadata')))
  expect(video.currentTime).toBe(3)
  act(() => player.setQuality(-1))
  expect(video.src).toContain('/1/playlist.m3u8')
})

it('a late native manifest cannot restore the source after a version switch', async () => {
  let resolve!: (response: Response) => void
  vi.mocked(fetch).mockImplementationOnce(() => new Promise(done => { resolve = done }) as Promise<Response>)
  const view = render(<Harness />)
  view.rerender(<Harness src="/new.mp4" />)
  await act(async () => { resolve({ ok: true, text: async () => manifest } as Response) })
  expect(view.container.querySelector('video')!.src).toContain('/new.mp4')
})
