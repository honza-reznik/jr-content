import { describe, expect, it } from 'vitest'
import { requestedEmbedVersion } from '../embed-version'
import type { AssetVersion } from '@/types'

const versions = [{ id: 'a' }, { id: 'b' }] as AssetVersion[]

describe('requestedEmbedVersion', () => {
  it('finds a version of this asset by id', () => {
    expect(requestedEmbedVersion(versions, 'b')).toBe(versions[1])
  })
  it('ignores a missing, empty or foreign id', () => {
    expect(requestedEmbedVersion(versions, null)).toBeNull()
    expect(requestedEmbedVersion(versions, '')).toBeNull()
    expect(requestedEmbedVersion(versions, 'zzz')).toBeNull()
    expect(requestedEmbedVersion([], 'a')).toBeNull()
  })
})
