"""Lokální data pro Chromium; spouštět jen v API kontejneru jrc-f5b04.

Výstup obsahuje dočasnou testovací relaci: přesměrovat do ignorovaného souboru,
nevypisovat do logu. Nevolá hlavní aplikaci ani internet.
"""
import json
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
import jwt
from pathlib import Path

from apps.api.config import settings
from apps.api.database import SessionLocal
from apps.api.models.user import User, UserStatus
from apps.api.models.project import Project, ProjectMember, ProjectRole
from apps.api.models.folder import Folder
from apps.api.models.asset import Asset, AssetType, AssetVersion, ProcessingStatus, MediaFile, FileType
from apps.api.models.comment import Comment, CommentAttachment
from apps.api.models.share import ShareLink, SharePermission
from apps.api.services.auth_service import create_access_token, create_refresh_token
from apps.api.services.s3_service import get_s3_client

assert settings.frontend_url == 'http://127.0.0.1:13243/video', 'Pouze vlastní ověřovací projekt'
suffix = uuid.uuid4().hex
with SessionLocal() as db, tempfile.TemporaryDirectory() as tmp:
    user = User(email=f'share-{suffix}@example.com', name='F5-B04 Member', jrc_managed=True,
                token_version=1, status=UserStatus.active, email_verified=True)
    admin = User(email=f'admin-{suffix}@example.com', name='Local test admin', is_superadmin=True,
                 token_version=1, status=UserStatus.active)
    db.add_all([user, admin]); db.flush()
    project = Project(name='F5-B04 Share regression', created_by=admin.id, jrc_managed=True)
    db.add(project); db.flush()
    db.add_all([ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.editor),
                ProjectMember(project_id=project.id, user_id=admin.id, role=ProjectRole.owner)])
    root = Folder(project_id=project.id, name='Footage', created_by=user.id)
    db.add(root); db.flush()
    nested = Folder(project_id=project.id, parent_id=root.id, name='Nested audio', created_by=user.id)
    db.add(nested); db.flush()
    images = []
    # Small fixtures produced locally, no network download or transcode worker.
    for kind, command, filename, mime in [
        ('image', ['-f', 'lavfi', '-i', 'color=c=blue:s=320x180', '-frames:v', '1', '-threads', '1'], 'photo.png', 'image/png'),
        ('audio', ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:a', 'libmp3lame'], 'tone.mp3', 'audio/mpeg'),
    ]:
        target = Path(tmp) / filename
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *command, str(target)], check=True)
        key = f'f5b04/{suffix}/{filename}'
        content = target.read_bytes()
        get_s3_client().put_object(Bucket=settings.s3_bucket, Key=key, Body=content, ContentType=mime)
        for i in range(25 if kind == 'image' else 1):
            name = f'Photo {i + 1:02d}.png' if kind == 'image' else 'Nested tone.mp3'
            asset = Asset(project_id=project.id, folder_id=root.id if kind == 'image' else nested.id,
                          name=name, created_by=user.id, asset_type=AssetType(kind))
            db.add(asset); db.flush()
            for number in (1, 2):
                version = AssetVersion(asset_id=asset.id, version_number=number, created_by=user.id,
                                       processing_status=ProcessingStatus.ready)
                db.add(version); db.flush()
                db.add(MediaFile(version_id=version.id, file_type=FileType(kind), original_filename=filename,
                                 mime_type=mime, file_size_bytes=len(content), s3_key_raw=key, s3_key_processed=key,
                                 s3_key_thumbnail=key if kind == 'image' else None,
                                 duration_seconds=2 if kind == 'audio' else None, width=320, height=180))
            comment = Comment(asset_id=asset.id, version_id=version.id, author_id=user.id, body='Existing member comment')
            db.add(comment); db.flush()
            db.add(CommentAttachment(comment_id=comment.id, file_type=mime, s3_key=key,
                                     original_filename=filename, file_size_bytes=len(content)))
            if kind == 'image': images.append(str(asset.id))
    link = ShareLink(folder_id=root.id, token=uuid.uuid4().hex, created_by=user.id,
                     title='F5-B04 Share regression', permission=SharePermission.comment, allow_download=True,
                     show_versions=True, appearance={'layout': 'grid', 'theme': 'dark', 'open_in_viewer': True})
    db.add(link); db.commit()
    print(json.dumps({'access': create_access_token(str(user.id), token_version=1),
                      'refresh': create_refresh_token(str(user.id), token_version=1),
                      'expired': jwt.encode({'sub': str(user.id), 'type': 'access', 'ver': 1,
                                             'exp': datetime.now(timezone.utc) - timedelta(seconds=1)},
                                            settings.jwt_secret, algorithm=settings.jwt_algorithm),
                      'admin': create_access_token(str(admin.id), token_version=1),
                      'project': str(project.id), 'user': str(user.id), 'share': link.token, 'images': images}))
