"""Živé ověření úložiště S3 přes místní bránu (F2-B05); běží v Dockeru se sítí hostitele.

Ověřuje oddělení tří klíčů, soukromý bucket jrc-app (nepodepsaná, prošlá a upravená
adresa odmítnuta), CORS obou bucketů jen pro původ aplikace. Tajemství se nevypisují.
"""

import os
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

s3_origin = f"http://127.0.0.1:{os.environ.get('JRC_S3_PORT', '13900')}"
app_origin = os.environ.get("JRC_PUBLIC_ORIGIN") or f"http://127.0.0.1:{os.environ.get('JRC_APP_PORT', '13000')}"
config = Config(signature_version="s3v4", s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=10)


def client(access, secret):
    return boto3.client(
        "s3",
        endpoint_url=s3_origin,
        aws_access_key_id=os.environ[access],
        aws_secret_access_key=os.environ[secret],
        region_name="garage",
        config=config,
    )


keys = {
    "jrc-local": client("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "jrc-video": client("JRC_FF_S3_ACCESS_KEY", "JRC_FF_S3_SECRET_KEY"),
    "jrc-app": client("JRC_BB_S3_ACCESS_KEY", "JRC_BB_S3_SECRET_KEY"),
}


def status(url, method="GET", headers=None):
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


name = f"f2b05-smoke/{uuid.uuid4()}.txt"
body = "JR Content: vymyšlená testovací data.\n".encode()
try:
    # 1. Každý klíč smí jen do svého bucketu.
    for owner, s3 in keys.items():
        for bucket in keys:
            try:
                s3.put_object(Bucket=bucket, Key=name, Body=body, ContentType="text/plain")
                allowed = True
            except ClientError as error:
                assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 403, error.response["Error"]
                allowed = False
            assert allowed == (owner == bucket), f"klíč {owner} → bucket {bucket}: {allowed}"
    print("OK S3: klíče jrc-local, jrc-video a jrc-app mají přístup jen ke svému bucketu")

    # 2. jrc-app je soukromý: bez podpisu nebo s upraveným podpisem 403, prošlá adresa odmítnuta.
    app = keys["jrc-app"]
    assert status(f"{s3_origin}/jrc-app/{name}")[0] == 403
    signed = app.generate_presigned_url("get_object", Params={"Bucket": "jrc-app", "Key": name}, ExpiresIn=60)
    code, _, content = status(signed)
    assert (code, content) == (200, body), code
    assert status(signed.replace("X-Amz-Signature=", "X-Amz-Signature=0"))[0] == 403
    assert status(signed + "&timestamp=1")[0] == 403
    short = app.generate_presigned_url("get_object", Params={"Bucket": "jrc-app", "Key": name}, ExpiresIn=1)
    time.sleep(3)
    # Garage odmítá prošlou adresu jako 400 „Date is too old“.
    assert status(short)[0] in (400, 403)
    print("OK S3: jrc-app bez podpisu i s upraveným podpisem 403, prošlá adresa odmítnuta; podepsaná 200")

    # 3. CORS: jen původ aplikace. Cizímu původu Garage předběžný dotaz (preflight)
    # odmítne 403, takže prohlížeč požadavek nepošle; chybové odpovědi Garage nesou
    # vždy „Allow-Origin: *“, úspěšné podepsané čtení cizímu původu povolení nedá.
    foreign = ("http://localhost:3000", "http://localhost:3001", "http://evil.invalid")
    for bucket, method in (("jrc-app", "GET"), ("jrc-video", "PUT")):
        for origin in (app_origin, *foreign):
            code, headers, _ = status(
                f"{s3_origin}/{bucket}/{name}",
                method="OPTIONS",
                headers={"Origin": origin, "Access-Control-Request-Method": method},
            )
            granted = headers.get("Access-Control-Allow-Origin")
            if origin == app_origin:
                assert code == 200 and granted == origin, (bucket, origin, code, granted)
            else:
                assert code == 403, (bucket, origin, code, granted)
    for origin in (app_origin, *foreign):
        code, headers, _ = status(signed, headers={"Origin": origin})
        granted = headers.get("Access-Control-Allow-Origin")
        assert code == 200 and granted == (origin if origin == app_origin else None), (origin, code, granted)
    print("OK S3: CORS jrc-app (GET) i jrc-video (PUT) povoluje jen", app_origin)
finally:
    for bucket, s3 in keys.items():
        s3.delete_object(Bucket=bucket, Key=name)
