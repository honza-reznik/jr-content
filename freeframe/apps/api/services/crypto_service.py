"""Symmetric encryption for reversible secrets (e.g., share link passwords).

Uses Fernet (AES-128-CBC + HMAC-SHA256) derived from JWT_SECRET.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

try:
    from ..config import settings
except ImportError:
    from config import settings


def _get_fernet() -> Fernet:
    # Derive a 32-byte key from JWT_SECRET using SHA-256, then base64-encode for Fernet
    key_bytes = hashlib.sha256(settings.jwt_secret.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key_bytes)
    return Fernet(key_b64)


def encrypt_password(password: str) -> str:
    """Encrypt a password for reversible storage."""
    f = _get_fernet()
    return f.encrypt(password.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    """Decrypt a stored password back to plaintext."""
    f = _get_fernet()
    return f.decrypt(encrypted.encode()).decode()
