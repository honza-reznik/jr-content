import React from 'react'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { CONTROLS_FALLBACK_PX, stageCapPx, useStageCap } from '../use-stage-cap'

// P1-B26 (N-k1e-07): the stage above the mobile comment sheet leaves room for the
// player's measured controls, not a fixed 60px that ignored the comment markers.

const observers: Array<{ cb: () => void; targets: Element[] }> = []
beforeEach(() => {
  observers.length = 0
  vi.stubGlobal('ResizeObserver', class {
    cb: () => void
    targets: Element[] = []
    constructor(cb: () => void) { this.cb = cb; observers.push(this as any) }
    observe(el: Element) { this.targets.push(el) }
    unobserve() {}
    disconnect() { this.targets = [] }
  })
})
afterEach(() => vi.unstubAllGlobals())

function Harness({ useSheet, sheet, controls, height }: { useSheet: boolean; sheet: number; controls?: number; height: number }) {
  const ref = React.useRef<HTMLDivElement>(null)
  const { onControlsHeight } = useStageCap(ref, useSheet, sheet)
  React.useLayoutEffect(() => {
    if (ref.current) Object.defineProperty(ref.current, 'clientHeight', { configurable: true, value: height })
  })
  React.useEffect(() => { if (controls !== undefined) onControlsHeight(controls) }, [controls, onControlsHeight])
  return <div ref={ref} data-testid="root" />
}

describe('stageCapPx', () => {
  it('subtracts the sheet and the controls and keeps a minimum', () => {
    expect(stageCapPx(500, 200, 87)).toBe(213)
    expect(stageCapPx(200, 150, 87)).toBe(80)
  })
})

describe('useStageCap', () => {
  it('uses the fallback until the player reports, then the measured controls', () => {
    const { getByTestId, rerender } = render(<Harness useSheet sheet={100} height={400} />)
    const root = getByTestId('root')
    expect(root.style.getPropertyValue('--ff-stage-max')).toBe(`${400 - 100 - CONTROLS_FALLBACK_PX}px`)
    // One comment adds the marker row: ~87px of controls instead of 60.
    rerender(<Harness useSheet sheet={100} controls={87} height={400} />)
    expect(root.style.getPropertyValue('--ff-stage-max')).toBe('213px')
  })

  it('re-caps when the root changes size', () => {
    const { getByTestId, rerender } = render(<Harness useSheet sheet={100} controls={87} height={400} />)
    const root = getByTestId('root')
    Object.defineProperty(root, 'clientHeight', { configurable: true, value: 500 })
    const observer = observers.find((o) => o.targets.includes(root))
    expect(observer).toBeTruthy()
    act(() => observer!.cb())
    expect(root.style.getPropertyValue('--ff-stage-max')).toBe('313px')
    rerender(<Harness useSheet sheet={100} controls={87} height={500} />)
  })

  it('removes the cap where there is no sheet', () => {
    const { getByTestId, rerender } = render(<Harness useSheet sheet={100} controls={87} height={400} />)
    const root = getByTestId('root')
    rerender(<Harness useSheet={false} sheet={100} controls={87} height={400} />)
    expect(root.style.getPropertyValue('--ff-stage-max')).toBe('')
  })

  it('ignores a report of zero (a player not laid out yet)', () => {
    const { getByTestId } = render(<Harness useSheet sheet={100} controls={0} height={400} />)
    expect(getByTestId('root').style.getPropertyValue('--ff-stage-max')).toBe(`${400 - 100 - CONTROLS_FALLBACK_PX}px`)
  })
})
