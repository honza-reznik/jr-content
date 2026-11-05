"""Živá regrese review1: pouze jrc-p1b16, skutečná DB, Redis a worker."""
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from celery.exceptions import Retry
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.project import Project
from apps.api.models.user import User
from apps.api.services.s3_service import get_s3_client
from apps.api.config import settings
from apps.api.tasks import transcode_tasks as tasks

assert os.environ['FRONTEND_URL'] == 'http://127.0.0.1:13583/jrc/video'
phase = os.environ['JRC_RECOVERY_PHASE']
assert phase in {'rollback', 'terminal'}
root = Path('/workspace/.pytest_cache/p1b16-review')
root.mkdir(parents=True, exist_ok=True)


def wait_for(marker, seconds=90):
    deadline = time.monotonic() + seconds
    while not (root / marker).exists():
        assert time.monotonic() < deadline, f'Čekání na {marker} vypršelo'
        time.sleep(.2)


video = root / 'review.mp4'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2',
                '-f', 'lavfi', '-i', 'color=c=blue:s=640x360:r=25:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', '-shortest', str(video)],
               check=True, timeout=20)
s3_key = f'raw/p1b16-review/{phase}.mp4'
get_s3_client().put_object(Bucket=settings.s3_bucket, Key=s3_key, Body=video.read_bytes())
with SessionLocal() as db:
    user = db.query(User).first()
    project = Project(name=f'P1-B16 review {phase}', created_by=user.id)
    db.add(project)
    db.flush()
    asset = Asset(project_id=project.id, name=phase, asset_type=AssetType.video, created_by=user.id)
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, created_by=user.id,
                           processing_status=ProcessingStatus.processing)
    db.add(version)
    db.flush()
    db.add(MediaFile(version_id=version.id, file_type=FileType.video, original_filename='review.mp4',
                     mime_type='video/mp4', file_size_bytes=video.stat().st_size, s3_key_raw=s3_key))
    db.commit()
    asset_id, version_id = str(asset.id), str(version.id)


rollback_errors = []


def interrupted(db, *args, **kwargs):
    db.execute(text('SELECT 1'))  # otevřená transakce pro skutečně neúspěšný rollback
    raw = db.connection().connection.driver_connection
    real_rollback = db.rollback

    def rollback():
        try:
            return real_rollback()
        except DBAPIError as exc:
            rollback_errors.append(exc)
            raise

    db.rollback = rollback
    (root / f'{phase}-waiting').touch()
    wait_for(f'{phase}-stopped')
    if phase == 'rollback':
        raw.close()  # skutečný ovladač, deterministicky uzavřené spojení při rollbacku
        raise RuntimeError('Test: conversion failed while the database disconnected')
    db.execute(text('SELECT 1'))  # skutečná chyba spojení na posledním pokusu


task = tasks.process_asset
task.push_request(retries=0 if phase == 'rollback' else 3, called_directly=False,
                  args=(asset_id, version_id), kwargs={})
try:
    with patch.object(tasks, '_process_video', side_effect=interrupted):
        try:
            task.run(asset_id, version_id)
            raise AssertionError('Očekávána chyba nebo skutečně naplánované opakování')
        except Retry:
            assert phase == 'rollback'
            assert rollback_errors, 'Ověření musí skutečně projít chybou rollbacku'
            print('OK: skutečná chyba rollbacku nezabránila naplánování opakování Celery.', flush=True)
        except OperationalError:
            assert phase == 'terminal'
            store = tasks._failure_store()
            assert store.hexists(tasks._PENDING_FAILURES, version_id)
            try:
                tasks.finish_failed_transcodes()
                raise AssertionError('DB má být stále nedostupná')
            except DBAPIError:
                assert store.hexists(tasks._PENDING_FAILURES, version_id)
            print('OK: limit zůstal 3, konečný zápis je v Redis a přežil další chybu DB.', flush=True)
finally:
    task.pop_request()

(root / f'{phase}-recorded').touch()
wait_for(f'{phase}-recovered')
deadline = time.monotonic() + 160
expected = ProcessingStatus.ready if phase == 'rollback' else ProcessingStatus.failed
while True:
    with SessionLocal() as db:
        version = db.get(AssetVersion, __import__('uuid').UUID(version_id))
        if version.processing_status == expected:
            media = db.query(MediaFile).filter(MediaFile.version_id == version.id).one()
            if phase == 'rollback':
                assert media.s3_key_processed and media.s3_key_download
            else:
                assert media.s3_key_processed is None
                assert not tasks._failure_store().hexists(tasks._PENDING_FAILURES, version_id)
            assert db.query(AssetVersion).filter(AssetVersion.asset_id == version.asset_id).count() == 1
            break
    assert time.monotonic() < deadline, f'Verze nedosáhla stavu {expected}'
    time.sleep(1)
print(f'OK {phase}: běžící worker dokončil stav {expected.value}, bez nové verze.', flush=True)
