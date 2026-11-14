/**
 * Whether the signed-in person may download files from FreeFrame itself.
 *
 * Accounts managed by JR Content never do: the download rule lives in Brightbean, and the API
 * answers 403 for them. The page leaves the download actions out so no dead button shows.
 */
export function canDownloadFiles(user: { jrc_managed?: boolean } | null | undefined): boolean {
  return !!user && user.jrc_managed !== true
}
