import { render } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ProgressBar } from '../progress-bar'

const fake = vi.hoisted(() => ({ instance: null as any }))
vi.mock('hls.js', () => {
  class Hls {
    static isSupported = () => true
    static Events = { MANIFEST_PARSED: 'manifest' }
    currentLevel = -1
    startLoad = vi.fn()
    handler: any
    constructor(public config: unknown) { fake.instance = this }
    on(_event: string, handler: unknown) { this.handler = handler }
    loadSource() {}
    attachMedia() {}
    destroy() {}
  }
  return { default: Hls }
})

it('loads only the smallest preview even when an old master sorts 1080p first', () => {
  render(<ProgressBar currentTime={0} duration={10} onSeek={() => {}} streamUrl="/master.m3u8" />)
  expect(fake.instance.config.autoStartLoad).toBe(false)
  expect(fake.instance.startLoad).not.toHaveBeenCalled()
  fake.instance.handler('manifest', { levels: [{ height: 1080 }, { height: 360 }, { height: 720 }] })
  expect(fake.instance.currentLevel).toBe(1)
  expect(fake.instance.startLoad).toHaveBeenCalledOnce()
})
