"""Živý audit přes HTTP; běží v jednorázovém Docker kontejneru se sítí hostitele."""
import json
import os
from pathlib import Path
import time
import urllib.request

BASE = 'http://127.0.0.1:18020'
OUT = Path('/results')
token = None


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


credentials = {'email': 'honza.audit@example.com', 'password': os.environ['AUDIT_PASSWORD']}
if request('GET', '/setup/status')['needs_setup']:
    result = request('POST', '/setup/create-superadmin', {**credentials, 'name': 'Honza Audit'})
else:
    result = request('POST', '/auth/login', credentials)
token = result['access_token']
project = request('POST', '/projects', {'name': 'Ella – audit F1-B02', 'description': 'Pouze vymyšlená data'})
video = Path('/results/test-1080p.mp4')
measurements = []
asset = None
versions = []
for number in (1, 2):
    payload = {'project_id': project['id'], 'asset_name': 'Reel pro Ellu – IG',
               'original_filename': f'reel-v{number}.mp4', 'mime_type': 'video/mp4',
               'file_size_bytes': video.stat().st_size}
    endpoint = '/upload/initiate' if asset is None else f'/assets/{asset}/versions'
    (OUT / 'phase').write_text(f'v{number}-upload')
    started = time.monotonic()
    upload = request('POST', endpoint, payload)
    asset = upload['asset_id']
    parts = []
    with video.open('rb') as stream:
        part_number = 1
        while chunk := stream.read(upload['chunk_size_bytes']):
            signed = request('POST', '/upload/presign-part', {
                's3_key': upload['s3_key'], 'upload_id': upload['upload_id'], 'part_number': part_number})
            req = urllib.request.Request(signed['presigned_url'], data=chunk, method='PUT')
            with urllib.request.urlopen(req, timeout=120) as response:
                parts.append({'PartNumber': part_number, 'ETag': response.headers['ETag']})
            part_number += 1
    completion = {key: upload[key] for key in ('upload_id', 's3_key', 'asset_id', 'version_id')}
    request('POST', '/upload/complete', {**completion, 'parts': parts})
    uploaded = time.monotonic()
    (OUT / 'phase').write_text(f'v{number}-processing')
    statuses = []
    while time.monotonic() - uploaded < 1800:
        versions_now = request('GET', f'/assets/{asset}/versions')
        version = next(v for v in versions_now if v['id'] == upload['version_id'])
        state = version['processing_status']
        if not statuses or statuses[-1] != state:
            statuses.append(state)
            print(f'v{number}: {state}', flush=True)
        if state in ('ready', 'failed'):
            break
        time.sleep(2)
    assert state == 'ready', state
    finished = time.monotonic()
    measurements.append({'version': number, 'upload_seconds': round(uploaded - started, 2),
                         'processing_seconds': round(finished - uploaded, 2),
                         'total_seconds': round(finished - started, 2),
                         'source_bytes': video.stat().st_size, 'statuses': statuses})
    versions.append(version)
    request('POST', f'/assets/{asset}/comments', {'version_id': version['id'],
            'body': f'Celé video v{number}: zkontrolováno v auditu.', 'timecode_start': None})
    request('POST', f'/assets/{asset}/comments', {'version_id': version['id'],
            'body': f'V čase 0:17 zkrátit – verze {number}.', 'timecode_start': 17.0})
    save('measurements.json', measurements)
    save('state.json', {'project_id': project['id'], 'asset_id': asset, 'versions': versions})
    (OUT / 'phase').write_text(f'v{number}-done')

comments = {}
for version in versions:
    data = request('GET', f"/assets/{asset}/comments?version_id={version['id']}")
    assert len(data) == 2 and all(c['version_id'] == version['id'] for c in data)
    assert {c['timecode_start'] for c in data} == {None, 17.0}
    comments[str(version['version_number'])] = data
approval = request('POST', f'/assets/{asset}/approve', {'version_id': versions[-1]['id'], 'note': 'Audit schválení'})
choices = request('GET', f'/assets/{asset}/downloads?version_id={versions[-1]["id"]}')
save('checks.json', {'comments': comments, 'approval': approval, 'download_choices': choices,
                    'checks': ['v1+v2 ready', 'komentáře oddělené podle verzí',
                               'komentář bez času i v 17. sekundě', 'schválení přes API', 'nabídka stažení']})
(OUT / 'phase').write_text('done')
print(json.dumps(measurements, ensure_ascii=False), flush=True)
print('Audit HTTP prošel.', flush=True)
