import { describe, expect, it } from 'vitest'
import { canEditAssets } from '../asset-permissions'

describe('canEditAssets (New Version and Share buttons)', () => {
  it('allows the team: owner and editor', () => {
    expect(canEditAssets('owner')).toBe(true)
    expect(canEditAssets('editor')).toBe(true)
  })
  it('hides them from a client (reviewer), a viewer and an unknown role', () => {
    expect(canEditAssets('reviewer')).toBe(false)
    expect(canEditAssets('viewer')).toBe(false)
    expect(canEditAssets(undefined)).toBe(false)
  })
})
