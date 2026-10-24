import type { AssetVersion } from '@/types'

/**
 * The version a host page (JR Content) asked for with `?version=<id>` in the embedded
 * viewer. Only a version of this asset counts; anything else is ignored so the viewer
 * keeps showing the latest ready version.
 */
export function requestedEmbedVersion(
  versions: AssetVersion[],
  requested: string | null | undefined,
): AssetVersion | null {
  if (!requested) return null
  return versions.find((version) => version.id === requested) ?? null
}
