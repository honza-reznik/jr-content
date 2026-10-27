"""Rezervace stejného účtu z obou aplikací v jediném Redis, bez zásahu do jiných účtů."""
import hashlib
import uuid
from types import SimpleNamespace

from apps.jrc_storage import limits

# Náhodná identita izoluje test od uživatelů i probíhajících přenosů.
user = SimpleNamespace(pk=uuid.uuid4())
identity = uuid.uuid5(uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234"), f"account:{user.pk}")
digest = hashlib.sha256(f"user:{identity}".encode()).hexdigest()
releases = []
try:
    for _ in range(4):
        releases.append(limits.acquire(user))
    try:
        limits.acquire(user)
    except limits.TransferUnavailableError as exc:
        assert exc.status == 429
    else:
        raise AssertionError("Fifth shared transfer admitted")
    assert limits.connection().zcard(f"hls:active:{digest}") == 4
    releases.pop()()
    releases.append(limits.acquire(user))
    assert limits.connection().zcard(f"hls:active:{digest}") == 4
finally:
    for release in releases:
        release()
    limits.connection().delete(f"hls:active:{digest}", f"hls:rate:{digest}")
assert limits.connection().zcard(f"hls:active:{digest}") == 0
print("OK P1-B03: živý Redis odmítne pátý přenos stejné identity a uzavření obnoví kapacitu.")
