/**
 * Who may write a team-only ("Internal") comment (F7-B02).
 *
 * Accounts managed by JR Content follow Brightbean: the team (project role owner or editor)
 * writes internal comments, a client (reviewer) and a viewer comment in public only. Plain
 * FreeFrame accounts are unchanged. The API enforces the same rule; this keeps the switch
 * out of the client's way.
 */
export function canPostInternalComments(
  jrcManaged: boolean | undefined,
  projectRole: string | undefined,
): boolean {
  if (!jrcManaged) return true
  return projectRole === 'owner' || projectRole === 'editor'
}
