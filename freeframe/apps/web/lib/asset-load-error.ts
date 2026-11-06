// JR Content P1-B26: what the review page says when the asset itself cannot be
// loaded. Before this the page only knew "loading" and showed "Loading asset..."
// forever after a 403 or 404 (P1-B30).

export interface AssetLoadError {
  title: string
  detail: string
  /** Whether trying again can help (not for a missing video or missing access). */
  retry: boolean
}

export function assetLoadError(status: number | null | undefined): AssetLoadError {
  if (status === 401 || status === 403) {
    return {
      title: 'You do not have access to video.',
      detail: 'Ask the team that shared it to give you access.',
      retry: false,
    }
  }
  if (status === 404) {
    return {
      title: 'This video no longer exists.',
      detail: 'It may have been deleted. Go back and choose another version or video.',
      retry: false,
    }
  }
  return {
    title: 'The video could not be loaded.',
    detail: 'Try again in a minute.',
    retry: true,
  }
}
