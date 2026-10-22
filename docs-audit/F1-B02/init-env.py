"""Spustit v Dockeru; tajné údaje zůstanou jen v ignorovaném audit/.env."""
from pathlib import Path
import secrets

path = Path('/audit/.env')
if path.exists():
    raise SystemExit('audit/.env již existuje; ponecháno beze změny.')
password = secrets.token_hex(24)
key = secrets.token_hex(12)
secret = secrets.token_hex(24)
values = {
    'POSTGRES_USER': 'freeframe', 'POSTGRES_DB': 'freeframe',
    'POSTGRES_PASSWORD': password,
    'DATABASE_URL': f'postgresql://freeframe:{password}@postgres:5432/freeframe',
    'MINIO_ROOT_USER': key, 'MINIO_ROOT_PASSWORD': secret,
    'S3_ACCESS_KEY': key, 'S3_SECRET_KEY': secret,
    'JWT_SECRET': secrets.token_hex(64),
    'AUDIT_PASSWORD': secrets.token_urlsafe(24),
}
with open(path, 'x', opener=lambda p, flags: __import__('os').open(p, flags, 0o600)) as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print('Vytvořen audit/.env (600).')
