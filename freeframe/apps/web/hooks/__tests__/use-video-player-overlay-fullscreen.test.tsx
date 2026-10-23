import React from 'react'
import { act, fireEvent, render } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useVideoPlayer, type UseVideoPlayerReturn } from '../use-video-player'

it('keeps the timecode visible when the browser refuses element fullscreen', async () => {
  let player: UseVideoPlayerReturn
  function Harness() {
    player = useVideoPlayer(null, { preserveOverlayFullscreen: true })
    return <video ref={player.videoRef} />
  }
  const view = render(<Harness />)
  const container = document.createElement('div')
  const requestFullscreen = vi.fn().mockRejectedValue(new Error('Fullscreen denied'))
  Object.assign(container, { requestFullscreen })
  await act(async () => player.toggleFullscreen(container))
  expect(requestFullscreen).toHaveBeenCalledOnce()
  expect(player!.isFullscreen).toBe(true)
  expect(document.body.style.overflow).toBe('hidden')
  view.unmount()
  expect(document.body.style.overflow).toBe('')
})

it('keeps overlays in window fullscreen, exits with Escape, restores scrolling and cleans up', () => {
  let player: UseVideoPlayerReturn
  function Harness() {
    player = useVideoPlayer(null, { preserveOverlayFullscreen: true })
    return <video ref={player.videoRef} />
  }
  document.body.style.overflow = 'auto'
  const view = render(<Harness />)
  const container = document.createElement('div')
  const native = vi.fn()
  Object.assign(view.container.querySelector('video')!, { webkitEnterFullscreen: native })
  act(() => player.toggleFullscreen(container))
  expect(player!.isFullscreen).toBe(true)
  expect(native).not.toHaveBeenCalled()
  expect(document.body.style.overflow).toBe('hidden')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(player!.isFullscreen).toBe(false)
  expect(document.body.style.overflow).toBe('auto')
  act(() => player.toggleFullscreen(container))
  act(() => player.toggleFullscreen(container))
  expect(player!.isFullscreen).toBe(false)
  act(() => player.toggleFullscreen(container))
  view.unmount()
  expect(document.body.style.overflow).toBe('auto')
  document.body.style.overflow = ''
})
