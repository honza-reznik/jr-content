"""Ověření původních UUID a spravovaných účtů po obnově; výstup jen souhrn."""
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backup'))
from sanitize_test import query

fixture = json.loads(Path('ops/test-output/P2-B01/fixture.json').read_text())
rows = json.loads(query('freeframe', "SELECT json_agg(json_build_object('id', id, 'email', email, 'managed', jrc_managed, 'name', name)) FROM users"))
assert rows and all(row['email'].lower().endswith('.invalid') for row in rows)
namespace = uuid.UUID('29c4ee9e-ab19-48c4-bb44-2fc77109b234')
for key in ('owner', 'client', 'real'):
    source = fixture['real_id'] if key == 'real' else fixture[key]['id']
    expected_id = str(uuid.uuid5(namespace, 'account:' + source))
    row = next(row for row in rows if row['id'] == expected_id)
    assert row['managed'] is True
    expected_email = hashlib.sha256(b'restored-person@example.org').hexdigest() + '@example.invalid' if key == 'real' else fixture[key]['email']
    assert row['email'] == expected_email
assert sum(row['name'] == 'jrc-sync' for row in rows) == 1
print('OK FF: všechny adresy .invalid, původní UUIDv5 a jrc_managed, jediný servisní účet')
