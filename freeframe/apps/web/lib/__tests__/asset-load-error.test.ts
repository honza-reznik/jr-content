import { describe, expect, it } from 'vitest'
import { assetLoadError } from '../asset-load-error'

// P1-B26 (from P1-B30): the review page names the reason a video cannot be shown.
describe('assetLoadError', () => {
  it('says there is no access for 403 and 401, without offering a retry', () => {
    for (const status of [401, 403]) {
      expect(assetLoadError(status)).toMatchObject({ title: 'You do not have access to video.', retry: false })
    }
  })

  it('says a missing video no longer exists', () => {
    expect(assetLoadError(404)).toMatchObject({ title: 'This video does not exist or you do not have access to it.', retry: false })
  })

  it('offers a retry for anything else, including no status at all', () => {
    for (const status of [500, 502, 503, null, undefined]) {
      expect(assetLoadError(status)).toMatchObject({ title: 'The video could not be loaded.', retry: true })
    }
  })
})
