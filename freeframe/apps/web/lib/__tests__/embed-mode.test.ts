import { describe, expect, it } from 'vitest'
import { shouldShowEmbedCommentsPanel } from '../embed-mode'

describe('embedded review comment panel', () => {
  it('shows the comments column after a phone rotates to landscape even when it started closed', () => {
    expect(shouldShowEmbedCommentsPanel(true, true, false, false)).toBe(true)
  })

  it('keeps the portrait comment sheet and normal closed sidebar behavior', () => {
    expect(shouldShowEmbedCommentsPanel(true, false, false, true)).toBe(false)
    expect(shouldShowEmbedCommentsPanel(false, true, false, false)).toBe(false)
  })
})
