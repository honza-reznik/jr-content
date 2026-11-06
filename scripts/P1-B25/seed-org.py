"""P1-B25 × P1-B23: vymyšlená druhá organizace pro živou kontrolu cíle organizačních zápisů."""
import json

from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

ws = Workspace.objects.get(name='P1-B25 Studio')
owner = WorkspaceMembership.objects.get(workspace=ws, workspace_role='owner').user
org_a = ws.organization
OrgMembership.objects.update_or_create(user=owner, organization=org_a, defaults={'org_role': 'owner'})
org_b, _ = Organization.objects.get_or_create(name='P1-B25 Druhá organizace')
OrgMembership.objects.update_or_create(user=owner, organization=org_b, defaults={'org_role': 'member'})
ws_b, _ = Workspace.objects.get_or_create(organization=org_b, name='P1-B25 Klient B')
WorkspaceMembership.objects.get_or_create(user=owner, workspace=ws_b, defaults={'workspace_role': 'owner'})
print('ORG=' + json.dumps({'a': str(org_a.pk), 'a_name': org_a.name, 'b': str(org_b.pk), 'b_name': org_b.name}))
