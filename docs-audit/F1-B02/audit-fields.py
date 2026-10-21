"""Zkouška, nakolik jdou body MVP1 (stavy, kanály, datum publikace, popisek) nahradit
vlastními poli FreeFrame. Běží po audit-api.py (čte state.json), jen vymyšlená data."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:18020'
state = json.loads(Path('/results/state.json').read_text())
token = None


def call(method, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(BASE + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:300]


status, login = call('POST', '/auth/login', {'email': 'honza.audit@example.com',
                                            'password': os.environ['AUDIT_PASSWORD']})
token = login['access_token']
project, asset = state['project_id'], state['asset_id']
result = {'access_token_expires_minutes_config': 15}

stavy = ['nápad', 'scénář', 'natáčení', 'střih', 'kontrola', 'schváleno',
         'připraveno k publikaci', 'publikováno']
fields = {}
for name, kind, options in [('Stav JR', 'select', stavy),
                            ('Kanál', 'multi_select', ['Instagram', 'Forendors', 'LinkedIn']),
                            ('Datum publikace', 'date', None),
                            ('Popisek IG', 'text', None)]:
    code, body = call('POST', f'/projects/{project}/metadata-fields',
                      {'name': name, 'field_type': kind, 'options': options})
    result[f'create_field_{kind}'] = code
    fields[name] = body['id'] if code == 201 else None
if None in fields.values():  # Opakovaný běh: jména polí jsou v projektu jedinečná (409).
    _, existing = call('GET', f'/projects/{project}/metadata-fields')
    fields = {name: next(f['id'] for f in existing if f['name'] == name) for name in fields}

# Typy povolené ve schématu API, ale chybějící v databázovém výčtu.
for kind in ('url', 'boolean'):
    code, body = call('POST', f'/projects/{project}/metadata-fields',
                      {'name': f'Zkouška {kind}', 'field_type': kind})
    result[f'create_field_{kind}'] = code

values = {'Stav JR': 'kontrola', 'Kanál': ['Instagram'], 'Datum publikace': '2026-11-03',
          'Popisek IG': 'Popisek v1 – vymyšlený text pro Ellu.'}
for name, value in values.items():
    code, _ = call('PUT', f'/assets/{asset}/metadata', [{'field_id': fields[name], 'value': value}])
    result[f'set_{name}'] = code
code, _ = call('PUT', f'/assets/{asset}/metadata',
               [{'field_id': fields['Popisek IG'], 'value': 'Popisek v2 – přepsaný.'}])
result['overwrite_caption'] = code
# Hlídá výběrové pole hodnoty mimo nabídku? (Pak by stav šel obejít.)
code, body = call('PUT', f'/assets/{asset}/metadata',
                  [{'field_id': fields['Stav JR'], 'value': 'neexistující stav'}])
result['select_accepts_unknown_value'] = code == 200
call('PUT', f'/assets/{asset}/metadata', [{'field_id': fields['Stav JR'], 'value': 'kontrola'}])
code, metadata = call('GET', f'/assets/{asset}/metadata')
result['metadata_after'] = metadata
# Je někde historie předchozí hodnoty popisku? Projde odpověď API.
result['caption_v1_still_visible'] = 'Popisek v1' in json.dumps(metadata, ensure_ascii=False)

# Vestavěný stav a termín
for value in ('in_review', 'approved', 'kontrola'):
    code, body = call('PATCH', f'/assets/{asset}', {'status': value})
    result[f'builtin_status_{value}'] = code
code, body = call('PATCH', f'/assets/{asset}', {'due_date': '2026-11-03T18:00:00Z'})
result['due_date'] = body.get('due_date') if code == 200 else code
code, approvals = call('GET', f'/assets/{asset}/approvals')
result['approvals_who_when'] = [{k: a[k] for k in ('user_id', 'status', 'created_at')}
                                for a in approvals]
Path('/results/fields.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False, indent=2))
