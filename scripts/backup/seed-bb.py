"""Vymyšlená testovací data Brightbean pro test záloh (F2-B09). Jen ověřovací projekt.

Spouští se přes `manage.py shell` v jrc-bb-web; poslední řádek výstupu je JSON.
"""
import hashlib
import io
import json
import secrets

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.accounts.models import User
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_texts import services as texts
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

# Jedinečný e-mail: opakované spuštění v téže instanci nenarazí na účet z minula (FreeFrame vrací 409).
email = f"zaloha-{secrets.token_hex(4)}@example.test"
password = secrets.token_urlsafe(24)
user = User.objects.create_user(email=email, password=password, tos_accepted_at=timezone.now())
org = Organization.objects.create(name="Zálohový test")
ws = Workspace.objects.create(organization=org, name="Zálohový test – klient")
OrgMembership.objects.create(user=user, organization=org, org_role=OrgMembership.OrgRole.OWNER)
WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER)
create_manual_channel(ws, "instagram", "Zálohový Instagram")
title = "Zálohový test – zahrada v říjnu"
post = create_content(ws, title, user)
script = "Scénář zálohy: **důležitý** text s diakritikou – příliš žluťoučký kůň úpěl ďábelské ódy."
texts.create_document(post.pk, user, markdown=script)

image = Image.new("RGB", (320, 200), (30, 120, 60))
draw = ImageDraw.Draw(image)
for x in range(0, 320, 16):
    draw.line([(x, 0), (320 - x, 200)], fill=(240, 220, 80))
draw.text((20, 90), "JR Content zaloha", fill=(255, 255, 255))
buffer = io.BytesIO()
image.save(buffer, "PNG")
png = buffer.getvalue()
asset = MediaAsset(
    organization=org, workspace=ws, uploaded_by=user, filename="zaloha.png", media_type="image",
    mime_type="image/png", file_size=len(png), width=320, height=200, title="Zálohový obrázek",
    alt_text="Vymyšlený testovací obrázek",
)
asset.file.save("zaloha.png", ContentFile(png), save=False)
asset.save()
print(json.dumps({
    "email": email, "password": password, "workspace_id": str(ws.id), "post_id": str(post.pk),
    "asset_id": str(asset.id), "title": title, "script": script, "png_sha256": hashlib.sha256(png).hexdigest(),
    "png_key": asset.file.name,
}))
