'use client'

import { useEffect, useRef } from 'react'
import { videoTimecode } from '@/lib/video-timecode'

/** Reads presented frames, independently of the optimistic transport/seek clock. */
export function VideoTimecodeOverlay({ videoRef, source, format, fps }: {
  videoRef: React.RefObject<HTMLVideoElement>
  source: string | null
  format: string
  fps?: number | null
}) {
  const labelRef = useRef<HTMLSpanElement>(null)
  const lastFrame = useRef<{ source: string | null; time: number } | null>(null)
  useEffect(() => {
    const video = videoRef.current
    const label = labelRef.current
    if (!video || !label) return
    if (!source) {
      label.hidden = true
      lastFrame.current = null
      return
    }
    let frameId: number | undefined
    let pendingTime: number | null = null
    const hasFrames = typeof video.requestVideoFrameCallback === 'function'
    const hide = () => { label.hidden = true }
    const show = (time: number) => {
      if (video.seeking || video.readyState < 2) return
      label.textContent = videoTimecode(time, format, fps)
      label.dataset.seconds = String(time)
      label.hidden = false
      lastFrame.current = { source, time }
    }
    const onSeeking = () => { pendingTime = null; hide() }
    const onSeeked = () => {
      // A frame callback can precede seeked. Publish it only once the seek ends.
      show(pendingTime ?? video.currentTime)
    }
    const fallback = () => { if (!hasFrames) show(video.currentTime) }
    const onFrame: VideoFrameRequestCallback = (_now, metadata) => {
      pendingTime = metadata.mediaTime
      lastFrame.current = { source, time: metadata.mediaTime }
      show(metadata.mediaTime)
      frameId = video.requestVideoFrameCallback(onFrame)
    }
    const cancelFrame = () => {
      if (frameId !== undefined) video.cancelVideoFrameCallback(frameId)
      frameId = undefined
    }
    const armFrames = () => {
      if (!hasFrames) return
      cancelFrame()
      frameId = video.requestVideoFrameCallback(onFrame)
    }
    const onLoadStart = () => {
      hide()
      pendingTime = null
      lastFrame.current = null
      cancelFrame()
    }
    const onLoadedData = () => {
      // A paused first frame may already be painted before a callback can be
      // registered. loadeddata/seeked confirm decoded data is available.
      show(pendingTime ?? video.currentTime)
      armFrames()
    }
    hide()
    if (lastFrame.current?.source === source) show(lastFrame.current.time)
    video.addEventListener('seeking', onSeeking)
    video.addEventListener('seeked', onSeeked)
    video.addEventListener('emptied', onLoadStart)
    video.addEventListener('loadstart', onLoadStart)
    // HLS attaches its MediaSource after child effects. Loading it can cancel
    // an already queued callback, so arm again before the first decoded frame.
    video.addEventListener('loadedmetadata', armFrames)
    video.addEventListener('loadeddata', onLoadedData)
    video.addEventListener('timeupdate', fallback)
    video.addEventListener('pause', fallback)
    if (hasFrames) armFrames()
    else fallback()
    return () => {
      cancelFrame()
      video.removeEventListener('seeking', onSeeking)
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('emptied', onLoadStart)
      video.removeEventListener('loadstart', onLoadStart)
      video.removeEventListener('loadedmetadata', armFrames)
      video.removeEventListener('loadeddata', onLoadedData)
      video.removeEventListener('timeupdate', fallback)
      video.removeEventListener('pause', fallback)
    }
  }, [videoRef, source, format, fps])
  return <span ref={labelRef} hidden data-testid="video-timecode" aria-label="Video timecode"
    className="absolute left-2 top-2 z-20 rounded bg-black/75 px-2 py-1 font-mono text-base tabular-nums text-white pointer-events-none select-none" />
}
