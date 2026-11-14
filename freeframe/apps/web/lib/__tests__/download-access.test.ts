import { describe, expect, it } from 'vitest'
import { canDownloadFiles } from '../download-access'

describe('canDownloadFiles', () => {
  it('refuses accounts managed by JR Content', () => {
    expect(canDownloadFiles({ jrc_managed: true })).toBe(false)
  })

  it('keeps plain FreeFrame accounts unchanged', () => {
    expect(canDownloadFiles({ jrc_managed: false })).toBe(true)
    expect(canDownloadFiles({})).toBe(true)
  })

  it('refuses a signed-out visitor', () => {
    expect(canDownloadFiles(null)).toBe(false)
    expect(canDownloadFiles(undefined)).toBe(false)
  })
})
