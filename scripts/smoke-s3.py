"""Ověření skutečného zápisu a čtení přes lokální rozhraní S3."""
import os
import uuid

import boto3
from botocore.config import Config

client = boto3.client(
    's3',
    endpoint_url=os.environ['S3_ENDPOINT_URL'],
    config=Config(
        signature_version='s3v4',
        s3={'addressing_style': 'path'},
        connect_timeout=5,
        read_timeout=10,
        retries={'max_attempts': 3},
    ),
)
bucket = os.environ['S3_BUCKET']
key = f'smoke/{uuid.uuid4()}.txt'
content = 'JR Content: vymyšlená testovací data.\n'.encode()
try:
    client.put_object(Bucket=bucket, Key=key, Body=content, ContentType='text/plain')
    response = client.get_object(Bucket=bucket, Key=key)
    with response['Body'] as body:
        actual = body.read()
    assert actual == content, 'Načtený obsah neodpovídá zapsanému objektu.'
    print('OK S3: zápis a přečtení stejného objektu')
finally:
    client.delete_object(Bucket=bucket, Key=key)
