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
    // The API answers 404 both for a deleted video and for one in a client the
    // person is not a member of, so that an id says nothing about what exists.
    return {
      title: 'This video does not exist or you do not have access to it.',
      detail: 'It may have been deleted. If you should see it, ask the team to add you to that client.',
      retry: false,
    }
  }
  return {
    title: 'The video could not be loaded.',
    detail: 'Try again in a minute.',
    retry: true,
  }
}
