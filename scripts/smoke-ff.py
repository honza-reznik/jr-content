"""Živý test přes místní porty; běží pouze v Dockeru se sítí hostitele."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

base = f"http://127.0.0.1:{os.environ.get('JRC_FF_API_PORT', '18022')}"
out = Path('ops/test-output/F2-B02')
out.mkdir(parents=True, exist_ok=True)
token = None


def request(method, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(base + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


# Oba klíče musí zůstat omezené na vlastní bucket.
endpoint = f"http://127.0.0.1:{os.environ.get('JRC_S3_PORT', '13900')}"
for key, secret, allowed, forbidden in (
    ('JRC_FF_S3_ACCESS_KEY', 'JRC_FF_S3_SECRET_KEY', 'jrc-video', os.environ['S3_BUCKET']),
    ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', os.environ['S3_BUCKET'], 'jrc-video'),
):
    storage = boto3.client('s3', endpoint_url=endpoint, region_name='garage',
                           aws_access_key_id=os.environ[key], aws_secret_access_key=os.environ[secret],
                           config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    storage.head_bucket(Bucket=allowed)
    try:
        storage.head_bucket(Bucket=forbidden)
    except ClientError as error:
        assert error.response['ResponseMetadata']['HTTPStatusCode'] == 403
    else:
        raise AssertionError('S3 klíč má přístup k cizímu bucketu.')
print('OK: oba S3 klíče mají přístup pouze ke svému bucketu.', flush=True)
assert request('GET', '/health')['status'] == 'ok'
assert request('GET', '/setup/status')['needs_setup'] is False
credentials = {'email': os.environ['JRC_FF_SYNC_EMAIL'],
               'password': os.environ['JRC_FF_SYNC_PASSWORD']}
token = request('POST', '/auth/login', credentials)['access_token']
project = request('POST', '/projects', {'name': 'F2-B02 — testovací video'})
video = out / 'test-1080p.mp4'
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                '-t', '3', '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], check=True)
upload = request('POST', '/upload/initiate', {
    'project_id': project['id'], 'asset_name': 'Lokální 1080p',
    'original_filename': video.name, 'mime_type': 'video/mp4',
    'file_size_bytes': video.stat().st_size})
parts = []
with video.open('rb') as stream:
    while chunk := stream.read(upload['chunk_size_bytes']):
        number = len(parts) + 1
        signed = request('POST', '/upload/presign-part', {
            's3_key': upload['s3_key'], 'upload_id': upload['upload_id'], 'part_number': number})
        # Podepsaná adresa zůstává přesně ta, kterou vrací API.
        req = urllib.request.Request(signed['presigned_url'], data=chunk, method='PUT')
        with urllib.request.urlopen(req, timeout=60) as response:
            parts.append({'PartNumber': number, 'ETag': response.headers['ETag']})
completion = {key: upload[key] for key in ('upload_id', 's3_key', 'asset_id', 'version_id')}
request('POST', '/upload/complete', {**completion, 'parts': parts})
started = time.monotonic()
states = []
while time.monotonic() - started < 180:
    versions = request('GET', f"/assets/{upload['asset_id']}/versions")
    version = next(v for v in versions if v['id'] == upload['version_id'])
    state = version['processing_status']
    if not states or states[-1] != state:
        states.append(state)
        print(f'1080p: {state}', flush=True)
    if state in ('ready', 'failed'):
        break
    time.sleep(2)
assert state == 'ready', state
with urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('JRC_FF_WEB_PORT', '13022')}/video/login", timeout=60) as response:
    assert response.status == 200
report = {'source_bytes': video.stat().st_size, 'states': states,
          'processing_seconds': round(time.monotonic() - started, 2), 'version': version}
(out / 'smoke-ff.json').write_text(json.dumps(report, indent=2))
print('OK: servisní přihlášení, 1080p multipart upload do Garage, ready a web 200.')
