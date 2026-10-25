import { describe, expect, it } from 'vitest'
import { canPostInternalComments } from '../comment-visibility'

describe('canPostInternalComments', () => {
  it('lets the JR Content team write internal comments', () => {
    expect(canPostInternalComments(true, 'owner')).toBe(true)
    expect(canPostInternalComments(true, 'editor')).toBe(true)
  })

  it('keeps clients and viewers of JR Content to public comments', () => {
    expect(canPostInternalComments(true, 'reviewer')).toBe(false)
    expect(canPostInternalComments(true, 'viewer')).toBe(false)
    expect(canPostInternalComments(true, undefined)).toBe(false)
  })

  it('does not change plain FreeFrame accounts', () => {
    expect(canPostInternalComments(false, 'reviewer')).toBe(true)
    expect(canPostInternalComments(undefined, 'viewer')).toBe(true)
  })
})
