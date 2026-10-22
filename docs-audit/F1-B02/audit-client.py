"""Ověření klientského účtu přes skutečné HTTP; bez odesílání pozvánky e-mailem."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

base = 'http://127.0.0.1:18020'
state = json.loads(Path('/results/state.json').read_text())
token = None


def call(method, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(base + path, method=method, headers=headers,
        data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


token = call('POST', '/auth/login', {'email': 'honza.audit@example.com',
                                   'password': os.environ['AUDIT_PASSWORD']})['access_token']
admin_token = token
# Nová adresa při opakování, bez potřeby mazat předchozí testovací uživatele.
import uuid
email = f'ella.audit.{uuid.uuid4().hex[:8]}@example.com'
user = call('POST', '/users/invite', {'email': email, 'name': 'Ella Audit'})
# Pozvánku čte oprávněný správce z odpovědi API; poštovní pracovník neběží.
invite = user['invite_token']
call('POST', f'/projects/{state["project_id"]}/members', {'user_id': user['id'], 'role': 'editor'})
private = call('POST', '/projects', {'name': 'Jiný klient – neveřejný'})
call('POST', '/auth/accept-invite', {'token': invite, 'name': 'Ella Audit', 'password': os.environ['AUDIT_PASSWORD']})
token = call('POST', '/auth/login', {'email': email, 'password': os.environ['AUDIT_PASSWORD']})['access_token']
projects = call('GET', '/projects')
assert state['project_id'] in {p['id'] for p in projects}
assert private['id'] not in {p['id'] for p in projects}
try:
    call('GET', f'/projects/{private["id"]}')
    raise AssertionError('Cizí neveřejný projekt nesmí být dostupný')
except urllib.error.HTTPError as error:
    assert error.code in (403, 404)
    private_status = error.code
version = state['versions'][-1]['id']
comment = call('POST', f'/assets/{state["asset_id"]}/comments', {
    'version_id': version, 'body': 'Ella: celé video schvaluji.', 'timecode_start': None})
approval = call('POST', f'/assets/{state["asset_id"]}/approve', {'version_id': version, 'note': 'Schválila Ella'})
assert approval['user_id'] == user['id']
created = call('POST', '/projects', {'name': 'Ellin vlastní nápad'})
Path('/results/client.json').write_text(json.dumps({'result': 'passed', 'private_project_status': private_status,
    'approval': approval, 'client_can_create_project': bool(created['id']),
    'client_comment_id': comment['id'], 'email_delivery': 'disabled; invite taken from admin API'}, indent=2))
print('Klient: přihlášení, vlastní projekt, komentář, schválení a zákaz cizího projektu prošly.')
