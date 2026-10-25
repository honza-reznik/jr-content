"""Ověří, že startovní nastavení bucketu je hotové a přesně odpovídá původu aplikace."""

import os
import sys

import boto3
from botocore.exceptions import ClientError


expected = os.environ["CORS_ALLOW_ORIGINS"]
bucket = os.environ["S3_BUCKET"]
client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    region_name=os.environ["S3_REGION"],
)

try:
    rules = client.get_bucket_cors(Bucket=bucket)["CORSRules"]
except ClientError:
    sys.exit(1)

origins = [origin for rule in rules for origin in rule["AllowedOrigins"]]
if origins != [expected] or "*" in origins:
    sys.exit(1)

print(f"OK: bucket {bucket} povoluje pouze {expected}")
