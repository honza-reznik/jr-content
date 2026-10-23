import { formatTime } from './utils'

/** Metadata FPS; unknown rates are displayed as seconds rather than invented frames. */
export function videoTimecode(seconds: number, format: string, fps?: number | null): string {
  const time = Number.isFinite(seconds) ? Math.max(0, seconds) : 0
  if (format === 'standard' || !fps || !Number.isFinite(fps) || fps <= 0) {
    return formatTime(time + 0.000001)
  }
  const totalFrames = Math.floor(time * fps + 0.0001)
  if (format === 'frames') return String(totalFrames)
  // Non-drop timecode: fractional rates use an integer nominal frame count.
  const nominal = Math.max(1, Math.round(fps))
  const wholeSeconds = Math.floor(totalFrames / nominal)
  return [Math.floor(wholeSeconds / 3600), Math.floor(wholeSeconds / 60) % 60,
    wholeSeconds % 60, totalFrames % nominal].map(n => String(n).padStart(2, '0')).join(':')
}
