"""P1-B16: živá DB, FFmpeg a Celery; restart řídí run-recovery.sh mimo tento proces."""
import os
import time
from pathlib import Path
from unittest.mock import patch

from celery.exceptions import Retry
from sqlalchemy import text

from apps.api.database import SessionLocal, engine
from apps.api.models.asset import Asset, AssetVersion, MediaFile, ProcessingStatus
from apps.api.models.project import Project
from apps.api.tasks import transcode_tasks

assert os.environ['FRONTEND_URL'] == 'http://127.0.0.1:13583/jrc/video'
root = Path('/workspace/.pytest_cache/p1b16')
root.mkdir(parents=True, exist_ok=True)
phase = os.environ['JRC_RECOVERY_PHASE']
assert phase in {'idle', 'commit'}


def restart_here():
    (root / f'{phase}-waiting').touch()
    deadline = time.monotonic() + 90
    while not (root / f'{phase}-restarted').exists():
        assert time.monotonic() < deadline, 'Čekání na restart DB vypršelo'
        time.sleep(.2)


with SessionLocal() as db:
    project = db.query(Project).filter(Project.name == 'P1-B16 Garden').one()
    asset = db.query(Asset).filter(Asset.project_id == project.id).one()
    original = db.query(AssetVersion).filter(AssetVersion.asset_id == asset.id).order_by(AssetVersion.version_number.desc()).first()
    source = db.query(MediaFile).filter(MediaFile.version_id == original.id).one()
    assert original.processing_status == ProcessingStatus.ready
    number = db.query(AssetVersion).filter(AssetVersion.asset_id == asset.id).count() + 1
    version = AssetVersion(asset_id=asset.id, version_number=number, processing_status=ProcessingStatus.processing, created_by=original.created_by)
    db.add(version)
    db.flush()
    db.add(MediaFile(version_id=version.id, file_type=source.file_type, original_filename=source.original_filename, mime_type=source.mime_type, file_size_bytes=source.file_size_bytes, s3_key_raw=source.s3_key_raw))
    db.commit()
    asset_id, version_id = str(asset.id), str(version.id)

task = transcode_tasks.process_asset
task.push_request(retries=0, called_directly=False, args=(asset_id, version_id), kwargs={})
try:
    if phase == 'idle':
        held = SessionLocal()
        held.execute(text('SELECT 1'))  # otevřená transakce nepřežije restart
        restart_here()
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT 1')) == 1  # pool_pre_ping
        with patch.object(transcode_tasks, 'SessionLocal', return_value=held):
            try:
                task.run(asset_id, version_id)
                raise AssertionError('Přerušená transakce se měla opakovat')
            except Retry:
                print('OK: přerušené první čtení naplánovalo skutečnou úlohu Celery.', flush=True)
    else:
        original_process = transcode_tasks._process_video

        def interrupted_video(db, *args, **kwargs):
            original_process(db, *args, **kwargs)  # skutečný převod a flush metadat
            restart_here()  # ztracená transakce před commitem ready

        with patch.object(transcode_tasks, '_process_video', side_effect=interrupted_video):
            try:
                task.run(asset_id, version_id)
                raise AssertionError('Přerušený commit se měl opakovat')
            except Retry:
                print('OK: přerušený zápis výsledku naplánoval skutečnou úlohu Celery.', flush=True)
finally:
    task.pop_request()

# Čekáme na opakování běžícím workerem, nikoli na náhradní/mockovaný převod.
deadline = time.monotonic() + 160
while True:
    with SessionLocal() as db:
        row = db.get(AssetVersion, __import__('uuid').UUID(version_id))
        status = row.processing_status
        if status == ProcessingStatus.ready:
            media = db.query(MediaFile).filter(MediaFile.version_id == row.id).one()
            assert media.s3_key_processed and media.s3_key_download
            print(f'OK {phase}: worker po restartu dokončil video a MP4.', flush=True)
            break
        assert status != ProcessingStatus.failed, 'Worker selhal'
    assert time.monotonic() < deadline, f'Video zůstalo {status}'
    time.sleep(1)
