import { api } from '@/lib/api'
import type { DownloadOption } from '@/types'

/** What an asset can be downloaded as: the original, then the transcode. */
export function fetchAssetDownloadOptions(assetId: string): Promise<DownloadOption[]> {
  return api.get<DownloadOption[]>(`/assets/${assetId}/downloads`)
}

/**
 * Download an asset. `rendition` is an id from fetchAssetDownloadOptions;
 * left out, the server serves the transcode where there is one. `preferOriginal`
 * (bulk downloads) asks for the original when the instance allows originals.
 * Resolves to the server's note when it had to serve another file than asked
 * (JR Content: converted MP4 for a missing original), for the caller to show
 * in its own UI; null otherwise.
 */
export async function downloadAsset(
  assetId: string, rendition?: string, preferOriginal = false, versionId?: string,
): Promise<string | null> {
  let query = rendition ? `&rendition=${encodeURIComponent(rendition)}` : ''
  if (preferOriginal) query += '&prefer_original=true'
  if (versionId) query += `&version_id=${encodeURIComponent(versionId)}`
  const data = await api.get<{ url: string; download_note?: string | null }>(`/assets/${assetId}/stream?download=true${query}`)
  if (!data?.url) return null
  // An iframe rather than navigation, so several downloads in a row (bulk)
  // do not cancel each other, and the page is never left.
  const iframe = document.createElement('iframe')
  iframe.style.display = 'none'
  iframe.src = data.url
  document.body.appendChild(iframe)
  setTimeout(() => iframe.remove(), 30000)
  return data.download_note ?? null
}
