import React, { useRef } from 'react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { VideoTimecodeOverlay } from '../video-timecode-overlay'
import { videoTimecode } from '@/lib/video-timecode'

let callback: VideoFrameRequestCallback
let seeking = false
function Harness({ source = 'first.mp4', format = 'timecode', fps = 25 }: {
  source?: string | null; format?: string; fps?: number
}) {
  const ref = useRef<HTMLVideoElement>(null)
  return <><video ref={ref} /><VideoTimecodeOverlay videoRef={ref} source={source} format={format} fps={fps} /></>
}
beforeEach(() => {
  seeking = false
  vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(2)
  vi.spyOn(HTMLMediaElement.prototype, 'seeking', 'get').mockImplementation(() => seeking)
  HTMLVideoElement.prototype.requestVideoFrameCallback = vi.fn(cb => { callback = cb; return 7 })
  HTMLVideoElement.prototype.cancelVideoFrameCallback = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  // jsdom does not implement frame callbacks.
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).requestVideoFrameCallback
  delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).cancelVideoFrameCallback
})
function frame(time: number) { callback(0, { mediaTime: time } as VideoFrameCallbackMetadata) }

describe('video timecode', () => {
  it('uses file FPS, handles fractional FPS as non-drop and unknown FPS as seconds', () => {
    expect(videoTimecode(1.48, 'timecode', 25)).toBe('00:00:01:12')
    expect(videoTimecode(1.48, 'frames', 25)).toBe('37')
    expect(videoTimecode(1, 'frames', 60)).toBe('60')
    expect(videoTimecode(60, 'timecode', 30000 / 1001)).toBe('00:00:59:28')
    expect(videoTimecode(1.5, 'timecode', null)).toBe('0:01')
    expect(videoTimecode(1.5, 'frames', NaN)).toBe('0:01')
    expect(videoTimecode(NaN, 'timecode', 25)).toBe('00:00:00:00')
  })
  it('reads the presented frame rather than an optimistic seek clock', () => {
    const view = render(<Harness />)
    const video = view.container.querySelector('video')!
    const label = view.getByTestId('video-timecode')
    video.currentTime = 10
    frame(1.48)
    expect(label.textContent).toBe('00:00:01:12')
    expect(label.dataset.seconds).toBe('1.48')
    expect(label.hidden).toBe(false)
    seeking = true
    fireEvent.seeking(video)
    fireEvent.timeUpdate(video)
    frame(10)
    expect(label.hidden).toBe(true)
    expect(label.textContent).toBe('00:00:01:12')
    seeking = false
    fireEvent.seeked(video)
    expect(label.hidden).toBe(false)
    expect(label.textContent).toBe('00:00:10:00')
  })
  it('uses the completed seek, supports format changes while paused and resets on source change', () => {
    const view = render(<Harness />)
    const video = view.container.querySelector('video')!
    const label = view.getByTestId('video-timecode')
    frame(1.48)
    view.rerender(<Harness format="frames" />)
    expect(label.textContent).toBe('37')
    seeking = true
    fireEvent.seeking(video)
    seeking = false
    fireEvent.seeked(video)
    expect(label.hidden).toBe(false)
    frame(5)
    expect(label.textContent).toBe('125')
    view.rerender(<Harness source="second.mp4" />)
    expect(label.hidden).toBe(true)
    frame(0)
    expect(label.textContent).toBe('00:00:00:00')
    view.rerender(<Harness source={null} />)
    expect(label.hidden).toBe(true)
    view.unmount()
    expect(HTMLVideoElement.prototype.cancelVideoFrameCallback).toHaveBeenCalledWith(7)
  })
  it('fallback ignores timeupdate during a seek and updates on seeked', () => {
    delete (HTMLVideoElement.prototype as Partial<HTMLVideoElement>).requestVideoFrameCallback
    const view = render(<Harness />)
    const video = view.container.querySelector('video')!
    const label = view.getByTestId('video-timecode')
    seeking = true
    fireEvent.seeking(video)
    video.currentTime = 9
    fireEvent.timeUpdate(video)
    expect(label.hidden).toBe(true)
    seeking = false
    fireEvent.seeked(video)
    expect(label.textContent).toBe('00:00:09:00')
    expect(label.hidden).toBe(false)
  })
  it('re-arms the frame callback when HLS attaches a new MediaSource after mount', () => {
    const view = render(<Harness />)
    const video = view.container.querySelector('video')!
    const label = view.getByTestId('video-timecode')
    const requests = HTMLVideoElement.prototype.requestVideoFrameCallback
    expect(requests).toHaveBeenCalledTimes(1)
    fireEvent.loadStart(video)
    expect(HTMLVideoElement.prototype.cancelVideoFrameCallback).toHaveBeenCalledWith(7)
    fireEvent.loadedMetadata(video)
    expect(requests).toHaveBeenCalledTimes(2)
    frame(0)
    expect(label.hidden).toBe(false)
    expect(label.textContent).toBe('00:00:00:00')
  })
  it('shows an already decoded paused first frame without waiting for playback', () => {
    const view = render(<Harness />)
    const video = view.container.querySelector('video')!
    const label = view.getByTestId('video-timecode')
    fireEvent.loadStart(video)
    fireEvent.loadedMetadata(video)
    fireEvent.loadedData(video)
    expect(label.hidden).toBe(false)
    expect(label.textContent).toBe('00:00:00:00')
  })
})
