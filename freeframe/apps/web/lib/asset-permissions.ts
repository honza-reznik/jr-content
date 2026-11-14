/**
 * Who may add a version to an asset or create share links for it (P2-B19).
 *
 * The API needs project role owner or editor for both (upload initiation and share creation);
 * a client (reviewer) and a viewer are refused. This only keeps the buttons out of their way.
 */
export function canEditAssets(projectRole: string | undefined): boolean {
  return projectRole === 'owner' || projectRole === 'editor'
}
